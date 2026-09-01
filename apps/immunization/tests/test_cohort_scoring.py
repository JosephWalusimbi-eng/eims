"""The set-based cohort scorer must agree exactly with the per-child path.

The paper reports a large speedup for `score_cohort`. That is only a result if
the two paths produce identical tiers and identical probabilities, which is
what this checks.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.immunization.models import Caregiver, Child, RiskScore
from apps.immunization.services.prediction_engine import calculate_dropout_probability
from apps.immunization.services.risk_engine import calculate_risk, score_cohort


def make_child(dob, name):
    caregiver = Caregiver.objects.create(
        full_name="C " + name, phone_number="+25670%08d" % Caregiver.objects.count(),
        district="Soroti", village="V",
        preferred_language=Caregiver.PreferredLanguage.ENGLISH,
    )
    return Child.objects.create(
        caregiver=caregiver, full_name=name, date_of_birth=dob,
        gender=Child.Gender.MALE,
    )


class CohortScoringEquivalenceTests(TestCase):
    def setUp(self):
        today = timezone.now().date()
        self.children = []
        for i, age in enumerate((5, 60, 120, 300, 700)):
            child = make_child(today - timedelta(days=age), "Child %d" % i)
            rows = list(child.vaccines.filter(due_date__lt=today).order_by("due_date"))
            for row in rows[: i]:          # administer i of the due doses
                row.administered = True
                row.administered_date = today
                row.save()
            self.children.append(child)

    def test_tiers_and_probabilities_match_the_per_child_path(self):
        expected = {}
        for child in self.children:
            score = calculate_risk(child)
            expected[child.pk] = (score.level, score.dropout_probability)

        RiskScore.objects.all().delete()
        score_cohort()

        for child in self.children:
            row = child.risk_scores.order_by("-calculated_at").first()
            level, probability = expected[child.pk]
            self.assertEqual(row.level, level, child.full_name)
            self.assertAlmostEqual(row.dropout_probability, probability, places=4)
            self.assertAlmostEqual(
                row.dropout_probability, calculate_dropout_probability(child), places=4
            )

    def test_a_second_pass_writes_nothing_when_no_tier_moved(self):
        first = score_cohort()
        second = score_cohort()
        self.assertEqual(first["written"], len(self.children))
        self.assertEqual(second["written"], 0)
        self.assertEqual(second["unchanged"], len(self.children))

    def test_the_cohort_pass_uses_a_constant_number_of_queries(self):
        """The property behind the scalability claim: query count does not
        grow with cohort size."""
        RiskScore.objects.all().delete()
        with self.assertNumQueries(3):
            score_cohort()

        for i in range(5, 15):
            make_child(timezone.now().date() - timedelta(days=300), "Extra %d" % i)
        RiskScore.objects.all().delete()
        with self.assertNumQueries(3):
            score_cohort()
