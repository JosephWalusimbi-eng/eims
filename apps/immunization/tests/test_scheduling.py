"""Schedule generation, overdue detection, risk scoring, dropout probability.

These are the four algorithms in the paper (Sec. IV). Each test states the
property the paper asserts and checks it against the running code.
"""
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.immunization.models import Caregiver, Child, RiskScore, VaccineSchedule
from apps.immunization.services.detection import get_overdue_vaccines
from apps.immunization.services.prediction_engine import calculate_dropout_probability
from apps.immunization.services.risk_engine import calculate_risk
from apps.immunization.services.scheduler import UGANDA_EPI_SCHEDULE, generate_schedule

EXPECTED_DOSES = sum(len(names) for _, names in UGANDA_EPI_SCHEDULE)


def make_child(dob: date, name="Test Child") -> Child:
    caregiver = Caregiver.objects.create(
        full_name="Test Caregiver",
        phone_number="+256700000001",
        email="caregiver@example.org",
        district="Soroti",
        village="Test Village",
        preferred_language=Caregiver.PreferredLanguage.ENGLISH,
    )
    return Child.objects.create(
        caregiver=caregiver, full_name=name, date_of_birth=dob,
        gender=Child.Gender.FEMALE,
    )


class ScheduleGenerationTests(TestCase):
    def test_registration_generates_the_full_epi_schedule(self):
        """Sec. IV-A: registration yields one row per scheduled dose."""
        child = make_child(date(2026, 1, 1))
        self.assertEqual(child.vaccines.count(), EXPECTED_DOSES)
        self.assertEqual(EXPECTED_DOSES, 15)

    def test_due_dates_are_dob_plus_the_national_offsets(self):
        dob = date(2026, 1, 1)
        child = make_child(dob)
        for offset, names in UGANDA_EPI_SCHEDULE:
            for name in names:
                row = child.vaccines.get(vaccine_name=name)
                self.assertEqual(row.due_date, dob + timedelta(days=offset), name)

    def test_regeneration_is_idempotent(self):
        """The uniqueness check in Sec. IV-A: a second pass creates nothing."""
        child = make_child(date(2026, 1, 1))
        created = generate_schedule(child)
        self.assertEqual(created, [])
        self.assertEqual(child.vaccines.count(), EXPECTED_DOSES)

    def test_every_row_gets_a_distinct_replication_identity(self):
        child = make_child(date(2026, 1, 1))
        uids = set(child.vaccines.values_list("sync_uid", flat=True))
        self.assertEqual(len(uids), EXPECTED_DOSES)


class OverdueDetectionTests(TestCase):
    def test_only_past_due_unadministered_doses_are_flagged(self):
        today = timezone.now().date()
        child = make_child(today - timedelta(days=200))
        due_in_past = child.vaccines.filter(due_date__lt=today)
        self.assertEqual(get_overdue_vaccines().filter(child=child).count(), due_in_past.count())

        first = due_in_past.first()
        first.administered = True
        first.administered_date = today
        first.save()
        self.assertEqual(
            get_overdue_vaccines().filter(child=child).count(), due_in_past.count() - 1
        )

    def test_a_child_born_today_has_nothing_overdue(self):
        child = make_child(timezone.now().date())
        self.assertEqual(get_overdue_vaccines().filter(child=child).count(), 0)


class RiskAndDropoutTests(TestCase):
    def _overdue(self, child, k):
        """Force exactly k overdue doses by administering all the rest."""
        today = timezone.now().date()
        rows = list(child.vaccines.filter(due_date__lt=today).order_by("-due_date"))
        for row in rows[k:]:
            row.administered = True
            row.administered_date = today
            row.save()
        return rows[:k]

    def test_risk_tiers_follow_the_published_thresholds(self):
        """Table III: 0 -> LOW, 1 -> MEDIUM, >=2 -> HIGH."""
        for k, level in ((0, RiskScore.Level.LOW), (1, RiskScore.Level.MEDIUM),
                         (3, RiskScore.Level.HIGH)):
            child = make_child(timezone.now().date() - timedelta(days=300), f"C{k}")
            self._overdue(child, k)
            self.assertEqual(calculate_risk(child).level, level, f"k={k}")

    def test_dropout_probability_matches_equation_one(self):
        """P_d = 0.4 r_ov + 0.3 r_delay + 0.3 r_miss, computed independently."""
        today = timezone.now().date()
        child = make_child(today - timedelta(days=300))
        overdue_rows = self._overdue(child, 2)

        total = child.vaccines.count()
        r_ov = 2 / total
        last_due = max(r.due_date for r in overdue_rows)
        r_delay = min((today - last_due).days / 30.0, 1.0)
        expected = round(0.4 * r_ov + 0.3 * r_delay + 0.3 * 1.0, 4)
        self.assertAlmostEqual(calculate_dropout_probability(child), expected, places=4)

    def test_a_fully_adherent_child_scores_zero(self):
        child = make_child(timezone.now().date() - timedelta(days=300))
        self._overdue(child, 0)
        self.assertEqual(calculate_dropout_probability(child), 0.0)

    def test_the_first_missed_dose_crosses_the_published_discontinuity(self):
        """Sec. VII-D: any child with one overdue dose scores at least
        0.3 + 0.4/15, the step the paper reports rather than leaves to a reader."""
        child = make_child(timezone.now().date() - timedelta(days=300))
        self._overdue(child, 1)
        self.assertGreaterEqual(calculate_dropout_probability(child), 0.3 + 0.4 / 15)
