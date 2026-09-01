"""Three-tier notification orchestration and the offline queue.

The paper claims that "in the simulated offline scenario, pending notifications
were queued locally and dispatched upon connectivity restoration, with no data
loss". These tests exercise exactly that sequence, and the dedupe tests cover
the second half of the claim -- that a repeated pass does not re-send.
"""
from datetime import date, timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.immunization.models import Caregiver, Child, NotificationLog, CalendarEvent
from apps.immunization.services import notification_service as notify
from apps.immunization.services.analysis_engine import run_analysis
from apps.immunization.services.reminder_engine import (
    send_five_day_email_reminders,
    send_overdue_reminders,
    send_upcoming_due_reminders,
)


def make_child(dob, name="Peter Akello"):
    caregiver = Caregiver.objects.create(
        full_name="Grace Akello", phone_number="+256700123456",
        email="grace@example.org", district="Soroti", village="Arapai",
        preferred_language=Caregiver.PreferredLanguage.ATESO,
    )
    return Child.objects.create(
        caregiver=caregiver, full_name=name, date_of_birth=dob,
        gender=Child.Gender.MALE,
    )


class TierRoutingTests(TestCase):
    def test_overdue_doses_queue_an_urgency_sms(self):
        make_child(timezone.now().date() - timedelta(days=200))
        NotificationLog.objects.all().delete()
        self.assertEqual(send_overdue_reminders(), 1)

        log = NotificationLog.objects.get(tier=NotificationLog.Tier.OVERDUE)
        self.assertEqual(log.channel, NotificationLog.Channel.SMS)
        self.assertEqual(log.status, NotificationLog.Status.QUEUED)

    def test_doses_due_in_three_days_queue_an_sms(self):
        make_child(timezone.now().date() - timedelta(days=42 - 2))
        NotificationLog.objects.all().delete()
        self.assertEqual(send_upcoming_due_reminders(days_ahead=3), 1)
        self.assertTrue(
            NotificationLog.objects.filter(tier=NotificationLog.Tier.URGENCY).exists()
        )

    def test_doses_due_in_five_days_queue_an_email(self):
        make_child(timezone.now().date() - timedelta(days=42 - 5))
        NotificationLog.objects.all().delete()
        self.assertEqual(send_five_day_email_reminders(), 1)

        log = NotificationLog.objects.get(tier=NotificationLog.Tier.PLANNING)
        self.assertEqual(log.channel, NotificationLog.Channel.EMAIL)

    def test_registration_materialises_the_persistence_tier(self):
        """Every distinct visit date becomes a calendar event row."""
        child = make_child(date(2026, 1, 1))
        dates = set(child.vaccines.values_list("due_date", flat=True))
        self.assertEqual(CalendarEvent.objects.filter(child=child).count(), len(dates))


class DataMinimisationTests(TestCase):
    """Sec. VI: SMS and calendar payloads carry a given name, a date, and the
    facility -- no vaccine names, no surname, no other clinical detail."""

    def setUp(self):
        self.child = make_child(timezone.now().date() - timedelta(days=200))
        NotificationLog.objects.all().delete()
        send_overdue_reminders()

    def test_sms_body_omits_clinical_detail_and_the_surname(self):
        body = NotificationLog.objects.get().message
        self.assertIn("Peter", body)
        self.assertNotIn("Akello", body)
        for vaccine in ("BCG", "OPV", "Penta", "PCV", "Rota", "Measles", "IPV"):
            self.assertNotIn(vaccine, body)

    def test_calendar_event_titles_omit_vaccine_names(self):
        for title in CalendarEvent.objects.values_list("title", flat=True):
            for vaccine in ("BCG", "OPV", "Penta", "PCV", "Rota", "Measles", "IPV"):
                self.assertNotIn(vaccine, title)

    def test_the_ics_payload_omits_clinical_detail(self):
        from apps.immunization.services.card_service import build_child_card_assets

        _, ics, _ = build_child_card_assets(self.child)
        text = ics.decode("utf-8")
        for vaccine in ("BCG", "OPV0", "Penta1", "Measles"):
            self.assertNotIn(vaccine, text)
        self.assertIn("Immunization visit", text)


class OfflineQueueTests(TestCase):
    """Enqueue always succeeds; dispatch is what needs the network."""

    def setUp(self):
        self.child = make_child(timezone.now().date() - timedelta(days=200))
        NotificationLog.objects.all().delete()

    def test_messages_survive_an_offline_window_and_drain_on_reconnection(self):
        with mock.patch(
            "apps.immunization.services.sms_service.deliver_sms",
            return_value=(False, "network unreachable"),
        ):
            send_overdue_reminders()
            offline = notify.dispatch_pending()

        self.assertEqual(offline["sent"], 0)
        self.assertEqual(offline["failed"], 1)
        # Nothing is dropped: the message is still queued for retry.
        self.assertEqual(notify.queue_depth(), 1)

        online = notify.dispatch_pending()
        self.assertEqual(online["sent"], 1)
        self.assertEqual(notify.queue_depth(), 0)

        log = NotificationLog.objects.get()
        self.assertEqual(log.status, NotificationLog.Status.SENT)
        self.assertEqual(log.attempts, 2)
        self.assertIsNotNone(log.dispatched_at)

    def test_a_repeated_pass_does_not_re_send(self):
        """Idempotency across analysis runs: the dedupe key suppresses a
        second copy of a message already queued or sent."""
        self.assertEqual(send_overdue_reminders(), 1)
        self.assertEqual(send_overdue_reminders(), 0)
        self.assertEqual(NotificationLog.objects.count(), 1)

    def test_enqueue_and_dispatch_latency_are_recorded_separately(self):
        send_overdue_reminders()
        notify.dispatch_pending()
        log = NotificationLog.objects.get()
        self.assertGreater(log.enqueue_ms, 0.0)
        self.assertGreaterEqual(log.gateway_ms, 0.0)


class AnalysisPassTests(TestCase):
    def test_a_full_pass_scores_every_child_and_queues_reminders(self):
        for i in range(3):
            make_child(timezone.now().date() - timedelta(days=200), "Child %d" % i)
        NotificationLog.objects.all().delete()

        processed, queued = run_analysis(dispatch=False)
        self.assertEqual(processed, 3)
        self.assertEqual(queued, 3)
        self.assertEqual(notify.queue_depth(), 3)

    def test_scoring_still_runs_when_the_network_is_down(self):
        make_child(timezone.now().date() - timedelta(days=200))
        with mock.patch(
            "apps.immunization.services.sms_service.deliver_sms",
            return_value=(False, "offline"),
        ):
            processed, queued = run_analysis(dispatch=True)
        self.assertEqual(processed, 1)
        self.assertGreaterEqual(queued, 1)
        self.assertGreaterEqual(notify.queue_depth(), 1)
