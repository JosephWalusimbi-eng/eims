"""Offline-first synchronization: idempotency, conflict detection, resolution.

The paper claims the engine "uploads pending records in timestamp order,
resolves write conflicts, and suppresses duplicate uploads via idempotency
checks". Each of those three is a test here. The last class is the one that
produces a result rather than a check: it measures what last-write-wins
actually costs on a concurrent-administration workload, which is the evidence
behind the paper's choice of a merge policy.
"""
import uuid
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.immunization.models import Caregiver, Child, VaccineSchedule
from apps.sync import services as sync
from apps.sync.models import Resolution, SyncEnvelope, SyncStatus


def make_child(name="Sync Child"):
    caregiver = Caregiver.objects.create(
        full_name="Sync Caregiver", phone_number="+256700000002",
        district="Soroti", village="Arapai",
        preferred_language=Caregiver.PreferredLanguage.ENGLISH,
    )
    return Child.objects.create(
        caregiver=caregiver, full_name=name,
        date_of_birth=date(2026, 1, 1), gender=Child.Gender.MALE,
    )


class OutboxTests(TestCase):
    def setUp(self):
        self.child = make_child()
        self.row = self.child.vaccines.first()

    def test_a_queued_edit_applies_on_flush(self):
        sync.enqueue(
            device_id="facility-01", entity="VaccineSchedule",
            entity_uid=str(self.row.sync_uid),
            payload={"administered": True, "administered_date": "2026-02-01"},
            base_version=self.row.version,
        )
        run = sync.flush_queue(device_id="facility-01")
        self.row.refresh_from_db()

        self.assertEqual(run.applied, 1)
        self.assertEqual(run.conflicts, 0)
        self.assertTrue(self.row.administered)
        self.assertEqual(self.row.administered_date, date(2026, 2, 1))
        self.assertEqual(self.row.version, 1)

    def test_replaying_the_same_client_id_is_a_no_op(self):
        """Idempotency: a device that retries after a dropped connection must
        not double-apply."""
        client_id = uuid.uuid4()
        for _ in range(3):
            sync.enqueue(
                device_id="facility-01", entity="VaccineSchedule",
                entity_uid=str(self.row.sync_uid),
                payload={"administered": True}, base_version=0, client_id=client_id,
            )
        self.assertEqual(SyncEnvelope.objects.count(), 1)

        run = sync.flush_queue()
        self.row.refresh_from_db()
        self.assertEqual(run.envelopes, 1)
        self.assertEqual(self.row.version, 1)

    def test_envelopes_apply_in_client_timestamp_order(self):
        now = timezone.now()
        for offset, due in ((2, "2026-03-03"), (0, "2026-03-01"), (1, "2026-03-02")):
            sync.enqueue(
                device_id="facility-01", entity="VaccineSchedule",
                entity_uid=str(self.row.sync_uid), payload={"due_date": due},
                base_version=0, client_updated_at=now + timedelta(minutes=offset),
            )
        sync.flush_queue()
        self.row.refresh_from_db()
        # The latest device edit is the one that survives.
        self.assertEqual(self.row.due_date, date(2026, 3, 3))

    def test_an_unknown_target_is_rejected_not_silently_dropped(self):
        sync.enqueue(
            device_id="facility-01", entity="VaccineSchedule",
            entity_uid=str(uuid.uuid4()), payload={"administered": True},
        )
        run = sync.flush_queue()
        self.assertEqual(run.rejected, 1)
        self.assertEqual(SyncEnvelope.objects.first().status, SyncStatus.REJECTED)

    def test_a_second_flush_has_nothing_to_do(self):
        sync.enqueue(
            device_id="facility-01", entity="VaccineSchedule",
            entity_uid=str(self.row.sync_uid), payload={"administered": True},
        )
        sync.flush_queue()
        self.assertEqual(sync.flush_queue().envelopes, 0)


class ConflictResolutionTests(TestCase):
    """Two replicas edit the same row from the same starting version."""

    def setUp(self):
        self.child = make_child()
        self.row = self.child.vaccines.first()
        self.base_version = self.row.version

        # Device A, offline, records the dose as given.
        sync.enqueue(
            device_id="A", entity="VaccineSchedule", entity_uid=str(self.row.sync_uid),
            payload={"administered": True, "administered_date": "2026-02-01"},
            base_version=self.base_version,
            client_updated_at=timezone.now() - timedelta(minutes=5),
        )
        # Device B, also offline and unaware, later edits the same row.
        sync.enqueue(
            device_id="B", entity="VaccineSchedule", entity_uid=str(self.row.sync_uid),
            payload={"administered": False, "due_date": "2026-04-01"},
            base_version=self.base_version,
            client_updated_at=timezone.now(),
        )

    def test_the_concurrent_edit_is_detected_rather_than_applied_blindly(self):
        run = sync.flush_queue(policy="merge")
        self.assertEqual(run.envelopes, 2)
        self.assertEqual(run.conflicts, 1)
        self.assertEqual(
            SyncEnvelope.objects.get(device_id="B").resolution, Resolution.MERGED
        )

    def test_last_write_wins_discards_the_administration_record(self):
        """The failure mode the paper names: a clinical fact recorded on one
        device is erased by a later, unrelated edit on another."""
        sync.flush_queue(policy="last_write_wins")
        self.row.refresh_from_db()
        self.assertFalse(self.row.administered)
        self.assertEqual(self.row.due_date, date(2026, 4, 1))
        # Worse than a plain loss: because device B's payload did not mention
        # administered_date, the row is left internally inconsistent -- not
        # administered, yet carrying an administration date.
        self.assertEqual(self.row.administered_date, date(2026, 2, 1))

    def test_merge_preserves_the_administration_and_the_later_edit(self):
        """The deployed policy: `administered` latches, so the dose survives,
        while the non-clinical field still takes the later value."""
        sync.flush_queue(policy="merge")
        self.row.refresh_from_db()
        self.assertTrue(self.row.administered)
        self.assertEqual(self.row.administered_date, date(2026, 2, 1))
        self.assertEqual(self.row.due_date, date(2026, 4, 1))

    def test_the_earliest_reported_administration_date_is_kept(self):
        # A row not already contested by setUp. `vaccines.all()[1]` is not
        # safe here: Meta orders by due_date and the first four doses tie at
        # day 0, so the index can land back on self.row.
        row = self.child.vaccines.exclude(pk=self.row.pk).order_by("pk").first()
        for day, when in ((10, 2), (3, 1)):
            sync.enqueue(
                device_id="C", entity="VaccineSchedule", entity_uid=str(row.sync_uid),
                payload={"administered": True, "administered_date": "2026-02-%02d" % day},
                base_version=0,
                client_updated_at=timezone.now() - timedelta(minutes=when),
            )
        sync.flush_queue(policy="merge")
        row.refresh_from_db()
        self.assertEqual(row.administered_date, date(2026, 2, 3))


class PolicyComparisonTests(TestCase):
    """Quantifies the cost of last-write-wins on a concurrent workload.

    Reported in the paper as a measured loss rate rather than an assertion that
    one policy is better than the other.
    """

    N_ROWS = 60

    def _build_workload(self):
        child = make_child()
        rows = list(child.vaccines.all())
        while len(rows) < self.N_ROWS:
            extra = make_child("Child %d" % len(rows))
            rows.extend(extra.vaccines.all())
        rows = rows[: self.N_ROWS]

        now = timezone.now()
        for i, row in enumerate(rows):
            sync.enqueue(
                device_id="A", entity="VaccineSchedule", entity_uid=str(row.sync_uid),
                payload={"administered": True, "administered_date": "2026-02-01"},
                base_version=row.version, client_updated_at=now - timedelta(minutes=5),
            )
            # Device B uploads its whole local snapshot, in which the dose
            # is still outstanding -- it never saw device A's administration.
            # Uploading whole records rather than deltas is what makes
            # last-write-wins lossy in practice.
            sync.enqueue(
                device_id="B", entity="VaccineSchedule", entity_uid=str(row.sync_uid),
                payload={"administered": False, "administered_date": None,
                         "five_day_email_reminder_sent": True},
                base_version=row.version, client_updated_at=now,
            )
        return rows

    def _loss_rate(self, policy):
        rows = self._build_workload()
        sync.flush_queue(policy=policy)
        uids = [r.sync_uid for r in rows]
        kept = VaccineSchedule.objects.filter(sync_uid__in=uids, administered=True).count()
        return 1.0 - kept / len(rows)

    def test_last_write_wins_loses_every_contested_administration(self):
        self.assertEqual(self._loss_rate("last_write_wins"), 1.0)

    def test_merge_loses_none(self):
        self.assertEqual(self._loss_rate("merge"), 0.0)
