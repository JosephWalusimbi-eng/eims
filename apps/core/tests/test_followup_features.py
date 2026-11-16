"""Self-updating card, at-risk drill-down, and the automated voice channel.

Three behaviours the paper now claims, each pinned to a test:

  * recording a dose ticks the caregiver's card and queues it for resend
  * a dashboard tile and the list behind it contain the same children, and the
    Monitor role never receives names through that list
  * the voice channel is queued, minimised, and drained like SMS and email
"""
import io
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.auth import DOCTOR_GROUP_NAME, MONITOR_GROUP_NAME
from apps.core.models import AuditLog
from apps.immunization.models import (
    Caregiver, Child, NotificationLog, RiskScore, VaccineSchedule,
)
from apps.immunization.services import atrisk
from apps.immunization.services import notification_service as notify
from apps.immunization.services.analysis_engine import run_analysis
from apps.immunization.services.card_service import build_child_card_assets
from apps.immunization.services.risk_engine import score_cohort
from apps.immunization.services.voice_service import (
    build_twiml, compose_script, queue_voice_call,
)

LOCMEM = override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")


def pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def pdf_ticks(pdf_bytes: bytes) -> int:
    """Number of doses the card shows as Given (status label beside the drawn tick)."""
    import re
    return len(re.findall(r"\bGiven\b", pdf_text(pdf_bytes)))


def make_child(district="Soroti", dob_days=200, name="Peter Akello", email="grace@example.org", n=None):
    n = Caregiver.objects.count() if n is None else n
    caregiver = Caregiver.objects.create(
        full_name="Grace Akello", phone_number="+2567001%05d" % n,
        email=email, district=district, village="Arapai",
        preferred_language=Caregiver.PreferredLanguage.ATESO,
    )
    return Child.objects.create(
        caregiver=caregiver, full_name=name,
        date_of_birth=timezone.now().date() - timedelta(days=dob_days),
        gender=Child.Gender.MALE,
    )


def login(client, group):
    user = User.objects.create_user("u_" + group, password="correct-horse-battery")
    user.groups.add(Group.objects.get_or_create(name=group)[0])
    client.force_login(user)
    return user


@LOCMEM
class CardSelfUpdateTests(TestCase):
    def setUp(self):
        self.child = make_child()
        NotificationLog.objects.all().delete()
        mail.outbox.clear()

    def test_card_pdf_shows_a_tick_only_for_given_doses(self):
        before = pdf_ticks(build_child_card_assets(self.child)[0])
        dose = self.child.vaccines.order_by("due_date").first()
        dose.administered, dose.administered_date = True, timezone.now().date()
        dose.save()
        pdf = build_child_card_assets(self.child)[0]
        self.assertEqual(before, 0)
        self.assertEqual(pdf_ticks(pdf), 1)
        self.assertIn("1 of %d doses given" % self.child.vaccines.count(), pdf_text(pdf))

    def test_recording_a_dose_queues_a_card_update_for_the_caregiver(self):
        dose = self.child.vaccines.order_by("due_date").first()
        dose.administered, dose.administered_date = True, timezone.now().date()
        dose.save()
        log = NotificationLog.objects.get(tier=NotificationLog.Tier.CARD_UPDATE)
        self.assertEqual(log.channel, NotificationLog.Channel.EMAIL)
        self.assertEqual(log.status, NotificationLog.Status.QUEUED)

    def test_every_dose_queues_its_own_update_and_a_cancel_queues_another(self):
        doses = list(self.child.vaccines.order_by("due_date")[:2])
        for d in doses:
            d.administered, d.administered_date = True, timezone.now().date()
            d.save()
        doses[0].administered, doses[0].administered_date = False, None
        doses[0].save()
        self.assertEqual(
            NotificationLog.objects.filter(tier=NotificationLog.Tier.CARD_UPDATE).count(), 3)

    def test_resaving_without_a_change_in_state_queues_nothing(self):
        dose = self.child.vaccines.first()
        dose.save()
        self.assertFalse(NotificationLog.objects.filter(tier=NotificationLog.Tier.CARD_UPDATE).exists())

    def test_the_queued_update_sends_a_ticked_pdf_when_drained(self):
        dose = self.child.vaccines.order_by("due_date").first()
        dose.administered, dose.administered_date = True, timezone.now().date()
        dose.save()
        notify.dispatch_pending()
        msg = next(m for m in mail.outbox if "UPDATED" in m.subject)
        pdf = next(a for a in msg.attachments if a[0].endswith(".pdf"))[1]
        self.assertEqual(pdf_ticks(pdf), 1)

    def test_an_offline_failure_leaves_the_update_queued(self):
        dose = self.child.vaccines.order_by("due_date").first()
        dose.administered, dose.administered_date = True, timezone.now().date()
        dose.save()
        with mock.patch("apps.immunization.services.email_service.EmailMessage.send",
                        side_effect=OSError("network down")):
            notify.dispatch_pending()
        self.assertEqual(
            NotificationLog.objects.get(tier=NotificationLog.Tier.CARD_UPDATE).status,
            NotificationLog.Status.QUEUED)

    def test_update_card_and_send_button_sends_now(self):
        login(self.client, DOCTOR_GROUP_NAME)
        dose = self.child.vaccines.order_by("due_date").first()
        dose.administered, dose.administered_date = True, timezone.now().date()
        dose.save()
        NotificationLog.objects.all().delete()
        mail.outbox.clear()
        resp = self.client.post(reverse("doctor_update_card_and_send", args=[self.child.unique_id]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len([m for m in mail.outbox if "UPDATED" in m.subject]), 1)
        self.assertEqual(
            NotificationLog.objects.get(tier=NotificationLog.Tier.CARD_UPDATE).status,
            NotificationLog.Status.SENT)

    def test_button_is_doctor_only_and_post_only(self):
        url = reverse("doctor_update_card_and_send", args=[self.child.unique_id])
        login(self.client, MONITOR_GROUP_NAME)
        self.assertEqual(self.client.post(url).status_code, 302)  # bounced to login
        self.assertFalse(NotificationLog.objects.filter(tier=NotificationLog.Tier.CARD_UPDATE).exists())
        login_doctor = User.objects.create_user('doc2', password='correct-horse-battery')
        login_doctor.groups.add(Group.objects.get_or_create(name=DOCTOR_GROUP_NAME)[0])
        self.client.force_login(login_doctor)
        self.assertEqual(self.client.get(url).status_code, 405)


class AtRiskDrillDownTests(TestCase):
    def setUp(self):
        # 3 districts; mix of children with 0, 1 and 2+ overdue doses.
        self.kids = []
        for i, (district, age) in enumerate([
            ("Gulu", 30), ("Gulu", 200), ("Gulu", 400),
            ("Jinja", 200), ("Jinja", 450), ("Jinja", 5),
            ("Mbarara", 300), ("Mbarara", 20), ("Mbarara", 600), ("Mbarara", 100),
        ]):
            self.kids.append(make_child(district, age, "Kid %02d" % i, n=i))
        # Recover one child fully so list != all.
        VaccineSchedule.objects.filter(
            child=self.kids[2], due_date__lt=timezone.now().date()
        ).update(administered=True, administered_date=timezone.now().date())
        score_cohort()

    def _list_ids(self, client, kind, district=None):
        ids, page = [], 1
        while True:
            q = "?filter=%s&page=%d" % (kind, page)
            if district:
                q += "&district=" + district
            resp = client.get(reverse("at_risk_list") + q)
            self.assertEqual(resp.status_code, 200)
            ids += [c.pk for c in resp.context["page"]]
            if not resp.context["page"].has_next():
                return ids, resp
            page += 1

    def test_each_tile_equals_the_length_of_its_list(self):
        login(self.client, DOCTOR_GROUP_NAME)
        dash = self.client.get(reverse("dashboard")).context
        high, _ = self._list_ids(self.client, "high")
        prio, _ = self._list_ids(self.client, "priority")
        over, _ = self._list_ids(self.client, "overdue")
        self.assertEqual(len(high), dash["high_risk_children"])
        self.assertEqual(len(prio), dash["priority_follow_up"])
        self.assertEqual(len(over), dash["overdue_children"])
        self.assertGreater(len(high), 0)

    def test_overdue_dose_counts_on_the_list_sum_to_the_overdue_tile(self):
        login(self.client, DOCTOR_GROUP_NAME)
        dash = self.client.get(reverse("dashboard")).context
        total = sum(c.overdue_doses for c in atrisk.children_for("overdue"))
        self.assertEqual(total, dash["overdue_vaccines"])

    def test_district_cells_match_the_district_lists(self):
        login(self.client, DOCTOR_GROUP_NAME)
        dash = self.client.get(reverse("dashboard")).context
        for row in dash["district_stats"]:
            for kind, field in (("high", "high_risk_count"), ("priority", "priority_count")):
                ids, _ = self._list_ids(self.client, kind, row["district"])
                self.assertEqual(len(ids), row[field], (row["district"], kind))

    def test_high_risk_means_latest_tier_not_ever_high(self):
        # self.kids[2] was recovered after being HIGH; a fresh score marks it LOW.
        RiskScore.objects.filter(child=self.kids[2]).delete()
        RiskScore.objects.create(child=self.kids[2], score=3, level="HIGH", dropout_probability=0.9)
        RiskScore.objects.create(child=self.kids[2], score=1, level="LOW", dropout_probability=0.1)
        login(self.client, DOCTOR_GROUP_NAME)
        ids, _ = self._list_ids(self.client, "high")
        self.assertNotIn(self.kids[2].pk, ids)
        self.assertEqual(len(ids), self.client.get(reverse("dashboard")).context["high_risk_children"])

    def test_list_is_ordered_most_urgent_first(self):
        login(self.client, DOCTOR_GROUP_NAME)
        _, resp = self._list_ids(self.client, "overdue")
        probs = [c.dropout_p for c in resp.context["page"]]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_doctor_sees_names_and_a_link_to_the_record(self):
        login(self.client, DOCTOR_GROUP_NAME)
        resp = self.client.get(reverse("at_risk_list") + "?filter=high")
        first = resp.context["page"][0]
        self.assertContains(resp, first.full_name)
        self.assertContains(resp, reverse("doctor_child_detail", args=[first.unique_id]))

    def test_monitor_gets_the_rows_but_no_names_or_contact(self):
        login(self.client, MONITOR_GROUP_NAME)
        resp = self.client.get(reverse("at_risk_list") + "?filter=overdue")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        for k in self.kids:
            self.assertNotIn(k.full_name, body)
            self.assertNotIn(k.caregiver.phone_number, body)
        self.assertNotIn("/doctor/child/", body)
        self.assertNotIn("Grace Akello", body)

    def test_anonymous_is_redirected_and_unknown_filter_404s(self):
        resp = self.client.get(reverse("at_risk_list") + "?filter=high")
        self.assertEqual(resp.status_code, 302)
        login(self.client, DOCTOR_GROUP_NAME)
        self.assertEqual(self.client.get(reverse("at_risk_list") + "?filter=bogus").status_code, 404)

    def test_the_read_is_audited(self):
        login(self.client, DOCTOR_GROUP_NAME)
        self.client.get(reverse("at_risk_list") + "?filter=high&district=Gulu")
        self.assertTrue(AuditLog.objects.filter(entity="AtRiskList", detail__contains="district=Gulu").exists())


class VoiceChannelTests(TestCase):
    def setUp(self):
        self.child = make_child(dob_days=300, name="Peter Akello")
        score_cohort()
        NotificationLog.objects.all().delete()

    def test_script_discloses_only_given_name_and_facility(self):
        script = compose_script(self.child)
        self.assertIn("Peter", script)
        self.assertNotIn("Akello", script)
        self.assertNotIn("Grace", script)
        for banned in ("BCG", "Penta", "Polio", "Measles", "OPV", "PCV", "Rota"):
            self.assertNotIn(banned, script)

    def test_twiml_is_escaped_and_well_formed(self):
        import xml.etree.ElementTree as ET
        xml = build_twiml('Peter & <friends> "said"')
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, "Response")

    def test_call_is_queued_then_drained_in_loopback(self):
        log = queue_voice_call(self.child, dedupe_key="v:1")
        self.assertEqual(log.channel, NotificationLog.Channel.VOICE)
        self.assertEqual(log.status, NotificationLog.Status.QUEUED)
        stats = notify.dispatch_pending()
        log.refresh_from_db()
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(log.status, NotificationLog.Status.SENT)

    def test_a_call_is_idempotent_per_day(self):
        self.assertIsNotNone(queue_voice_call(self.child, dedupe_key="v:same"))
        self.assertIsNone(queue_voice_call(self.child, dedupe_key="v:same"))

    def test_call_with_no_phone_stays_queued(self):
        Caregiver.objects.filter(pk=self.child.caregiver_id).update(phone_number="")
        child = Child.objects.select_related("caregiver").get(pk=self.child.pk)
        child.caregiver.phone_number = ""
        log = queue_voice_call(child, dedupe_key="v:nophone")
        with mock.patch("apps.immunization.services.voice_service.deliver_voice",
                        return_value=(False, "no caregiver phone number on record")):
            notify.dispatch_pending()
        log.refresh_from_db()
        self.assertEqual(log.status, NotificationLog.Status.QUEUED)

    @override_settings(EIMS_VOICE_AUTOCALL=False, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_analysis_pass_places_no_calls_unless_enabled(self):
        run_analysis(dispatch=False)
        self.assertFalse(NotificationLog.objects.filter(channel="VOICE").exists())

    @override_settings(EIMS_VOICE_AUTOCALL=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_analysis_pass_escalates_only_high_risk_children_once_a_day(self):
        low = make_child(dob_days=0, name="Newborn Low", n=99)
        score_cohort()
        run_analysis(dispatch=False)
        run_analysis(dispatch=False)
        calls = NotificationLog.objects.filter(channel="VOICE")
        self.assertEqual(calls.count(), 1)
        self.assertEqual(calls.get().child_id, self.child.pk)
        self.assertFalse(calls.filter(child=low).exists())

    @LOCMEM
    def test_doctor_call_button_places_a_call_and_is_doctor_only(self):
        url = reverse("doctor_call_caregiver", args=[self.child.unique_id])
        login(self.client, MONITOR_GROUP_NAME)
        self.client.post(url)
        self.assertFalse(NotificationLog.objects.filter(channel="VOICE").exists())
        self.client.logout()
        login(self.client, DOCTOR_GROUP_NAME)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(NotificationLog.objects.get(channel="VOICE").status, NotificationLog.Status.SENT)

    @LOCMEM
    def test_next_redirect_rejects_foreign_hosts(self):
        login(self.client, DOCTOR_GROUP_NAME)
        resp = self.client.post(
            reverse("doctor_call_caregiver", args=[self.child.unique_id]),
            {"next": "https://evil.example/"},
        )
        self.assertNotIn("evil.example", resp["Location"])


@LOCMEM
class DuplicateSuppressionAndSyncTests(TestCase):
    def setUp(self):
        self.child = make_child()
        NotificationLog.objects.all().delete()
        mail.outbox.clear()

    def test_a_stale_copy_of_a_row_cannot_be_sent_twice(self):
        log = notify.enqueue(
            self.child, channel=NotificationLog.Channel.EMAIL,
            tier=NotificationLog.Tier.PLANNING, message="hello", dedupe_key="dup:1",
        )
        stale = NotificationLog.objects.get(pk=log.pk)
        fresh = NotificationLog.objects.get(pk=log.pk)
        notify.dispatch_log(fresh)
        ok, err = notify.dispatch_log(stale)
        self.assertFalse(ok)
        self.assertEqual(len(mail.outbox), 1)

    def test_overlapping_drains_send_each_message_once(self):
        for i in range(3):
            notify.enqueue(
                self.child, channel=NotificationLog.Channel.EMAIL,
                tier=NotificationLog.Tier.PLANNING, message="m%d" % i, dedupe_key="dup:%d" % i,
            )
        rows_a = list(NotificationLog.objects.filter(status="QUEUED"))
        rows_b = list(NotificationLog.objects.filter(status="QUEUED"))
        for ra, rb in zip(rows_a, rows_b):   # interleaved, as two threads would
            notify.dispatch_log(ra)
            notify.dispatch_log(rb)
        self.assertEqual(len(mail.outbox), 3)

    def test_a_dose_merged_from_an_offline_device_ticks_and_queues_the_card(self):
        from apps.sync import services as sync
        row = self.child.vaccines.order_by("due_date").first()
        sync.enqueue(
            device_id="facility-01", entity="VaccineSchedule",
            entity_uid=str(row.sync_uid),
            payload={"administered": True, "administered_date": "2026-02-01"},
            base_version=row.version,
        )
        sync.flush_queue(device_id="facility-01")
        self.assertEqual(
            NotificationLog.objects.filter(tier=NotificationLog.Tier.CARD_UPDATE).count(), 1)
        notify.dispatch_pending()
        msg = next(m for m in mail.outbox if "UPDATED" in m.subject)
        pdf = next(a for a in msg.attachments if a[0].endswith(".pdf"))[1]
        self.assertEqual(pdf_ticks(pdf), 1)

    def test_no_update_is_queued_for_a_caregiver_without_email(self):
        child = make_child(email="", n=77)
        NotificationLog.objects.all().delete()
        dose = child.vaccines.first()
        dose.administered, dose.administered_date = True, timezone.now().date()
        dose.save()
        self.assertFalse(NotificationLog.objects.filter(tier=NotificationLog.Tier.CARD_UPDATE).exists())
