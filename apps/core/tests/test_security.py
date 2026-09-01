"""Verification of the controls the paper's Security and Privacy section claims.

Each test corresponds to one published sentence. The point is that no claim in
Sec. VI rests on a checklist: every one of them fails loudly here if the
implementation regresses.
"""
from datetime import date

from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.auth import DOCTOR_GROUP_NAME, MONITOR_GROUP_NAME
from apps.core.crypto import PREFIX, blind_index, decrypt, encrypt
from apps.core.models import AuditLog
from apps.immunization.models import Caregiver, Child


def make_child():
    caregiver = Caregiver.objects.create(
        full_name="Grace Akello", phone_number="+256700123456",
        email="grace@example.org", district="Soroti", village="Arapai",
        preferred_language=Caregiver.PreferredLanguage.ATESO,
    )
    return Child.objects.create(
        caregiver=caregiver, full_name="Peter Akello",
        date_of_birth=date(2026, 1, 1), gender=Child.Gender.MALE,
    )


class EncryptionAtRestTests(TestCase):
    """Claim: personally identifying fields are encrypted at rest."""

    def test_identifying_columns_hold_ciphertext_in_the_database(self):
        child = make_child()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT full_name, phone_number, email FROM immunization_caregiver WHERE id=%s",
                [child.caregiver_id],
            )
            name, phone, email = cur.fetchone()
            cur.execute("SELECT full_name FROM immunization_child WHERE id=%s", [child.id])
            child_name = cur.fetchone()[0]

        for raw in (name, phone, email, child_name):
            self.assertTrue(raw.startswith(PREFIX), raw[:24])
        blob = name + phone + email + child_name
        for plaintext in ("Grace Akello", "+256700123456", "grace@example.org", "Peter Akello"):
            self.assertNotIn(plaintext, blob)

    def test_values_round_trip_through_the_orm(self):
        child = make_child()
        reloaded = Child.objects.select_related("caregiver").get(pk=child.pk)
        self.assertEqual(reloaded.full_name, "Peter Akello")
        self.assertEqual(reloaded.caregiver.phone_number, "+256700123456")

    def test_a_ciphertext_cannot_be_moved_between_columns(self):
        """The field name is bound in as additional authenticated data, so
        transplanting a value from one column to another fails to decrypt."""
        blob = encrypt("+256700123456", "immunization.caregiver.phone_number")
        with self.assertRaises(Exception):
            decrypt(blob, "immunization.caregiver.email")

    def test_blind_index_permits_lookup_without_decryption(self):
        child = make_child()
        found = Caregiver.objects.get(phone_index=blind_index("+256 700 123 456"))
        self.assertEqual(found.pk, child.caregiver_id)
        self.assertNotEqual(found.phone_index, "+256700123456")


class AuditLogTests(TestCase):
    """Claim: every read and write against a child record is written to an
    append-only audit log."""

    def test_rows_cannot_be_amended(self):
        row = AuditLog.objects.create(action=AuditLog.Action.READ, entity="Child")
        row.detail = "tampered"
        with self.assertRaises(PermissionError):
            row.save()

    def test_rows_cannot_be_deleted(self):
        row = AuditLog.objects.create(action=AuditLog.Action.READ, entity="Child")
        with self.assertRaises(PermissionError):
            row.delete()

    def test_reading_a_child_record_writes_an_audit_row(self):
        child = make_child()
        doctor = User.objects.create_user("doc", password="correct-horse-battery")
        doctor.groups.add(Group.objects.get_or_create(name=DOCTOR_GROUP_NAME)[0])
        self.client.force_login(doctor)

        before = AuditLog.objects.filter(action=AuditLog.Action.READ).count()
        self.client.get(reverse("doctor_child_detail", kwargs={"child_uuid": child.unique_id}))
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.READ).count(), before + 1)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.READ, entity="Child",
                entity_uid=str(child.unique_id),
            ).count(),
            1,
        )
        self.assertEqual(AuditLog.objects.first().actor, "doc")


class AccessControlTests(TestCase):
    """Claim: distinct doctor, monitoring, and administrative roles are
    enforced at the view layer."""

    def setUp(self):
        self.child = make_child()
        self.doctor = User.objects.create_user("doc", password="correct-horse-battery")
        self.doctor.groups.add(Group.objects.get_or_create(name=DOCTOR_GROUP_NAME)[0])
        self.monitor = User.objects.create_user("mon", password="correct-horse-battery")
        self.monitor.groups.add(Group.objects.get_or_create(name=MONITOR_GROUP_NAME)[0])

    def test_dashboard_is_closed_to_anonymous_users(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_monitoring_user_sees_the_dashboard(self):
        self.client.force_login(self.monitor)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_monitoring_user_cannot_reach_registration(self):
        """The role boundary in the direction that matters: a monitoring
        account must not be able to create or modify records."""
        self.client.force_login(self.monitor)
        resp = self.client.get(reverse("doctor_register_child"), follow=True)
        # Bounced to the login page rather than served the form; the
        # registration template is never rendered for this role.
        self.assertNotIn(
            "core/doctor_register_child.html", [t.name for t in resp.templates if t.name]
        )
        self.assertEqual(resp.redirect_chain[0][1], 302)

        before = Child.objects.count()
        self.client.post(reverse("doctor_register_child"), {
            "caregiver_full_name": "X", "caregiver_phone_number": "+256700000009",
            "caregiver_village": "V", "caregiver_preferred_language": "ENGLISH",
            "child_full_name": "Y", "child_date_of_birth": "2026-01-01",
            "child_gender": "MALE",
        })
        self.assertEqual(Child.objects.count(), before)

    def test_analysis_trigger_rejects_anonymous_get(self):
        """An unauthenticated GET used to dispatch live SMS and email."""
        self.assertEqual(self.client.get(reverse("run_analysis")).status_code, 302)
        self.client.force_login(self.monitor)
        self.assertEqual(self.client.get(reverse("run_analysis")).status_code, 405)


class ArtifactLinkTests(TestCase):
    """Claim: the record is reachable only through an authenticated session or
    a signed, expiring link."""

    def setUp(self):
        self.child = make_child()
        self.pdf_url = reverse("immunization_card_pdf",
                               kwargs={"child_uuid": self.child.unique_id})

    def test_bare_uuid_is_not_a_capability(self):
        self.assertEqual(self.client.get(self.pdf_url).status_code, 404)

    def test_signed_link_grants_access(self):
        from apps.immunization.views import sign_artifact_link

        resp = self.client.get(sign_artifact_link(self.child, "pdf"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_a_token_for_one_child_does_not_open_another(self):
        from apps.immunization.views import sign_artifact_link

        other = make_child()
        token = sign_artifact_link(other, "pdf").split("t=")[1]
        self.assertEqual(self.client.get(self.pdf_url + "?t=" + token).status_code, 404)

    def test_a_pdf_token_does_not_open_the_calendar_endpoint(self):
        from apps.immunization.views import sign_artifact_link

        token = sign_artifact_link(self.child, "pdf").split("t=")[1]
        ics = reverse("immunization_schedule_ics", kwargs={"child_uuid": self.child.unique_id})
        self.assertEqual(self.client.get(ics + "?t=" + token).status_code, 404)

    def test_expired_link_is_refused(self):
        from apps.immunization.views import sign_artifact_link

        url = sign_artifact_link(self.child, "pdf")
        with override_settings(EIMS_ARTIFACT_LINK_MAX_AGE=-1):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_refused_attempt_is_audited(self):
        self.client.get(self.pdf_url)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.DENIED,
                                    entity_uid=str(self.child.unique_id)).exists()
        )
