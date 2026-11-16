from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.immunization.models import (
    CalendarEvent as CalendarEventRecord,
    Child,
    ImmunizationCard,
    VaccineSchedule,
)
from apps.immunization.services.calendar_service import (
    build_ics,
    google_calendar_link,
    schedule_to_events,
)
from apps.immunization.services.pdf_service import DoseEntry, ImmunizationCardRow, build_immunization_card_pdf


def _group_schedules_by_date(schedules: list[VaccineSchedule]) -> list[tuple[date, list[str]]]:
    grouped: dict[date, list[str]] = defaultdict(list)
    for s in schedules:
        grouped[s.due_date].append(s.vaccine_name)
    return sorted(((d, sorted(vs)) for d, vs in grouped.items()), key=lambda x: x[0])


def build_child_card_assets(child: Child) -> tuple[bytes, bytes, list[str]]:
    """
    Returns (pdf_bytes, ics_bytes, google_links)
    """
    schedules = list(child.vaccines.order_by("due_date", "vaccine_name").all())
    grouped = _group_schedules_by_date(schedules)

    by_date: dict[date, list[DoseEntry]] = defaultdict(list)
    for sched in schedules:
        by_date[sched.due_date].append(
            DoseEntry(
                vaccine=sched.vaccine_name,
                given_on=(sched.administered_date or sched.due_date) if sched.administered else None,
            )
        )
    rows = [
        ImmunizationCardRow(
            due_date=d,
            vaccines=[e.vaccine for e in sorted(entries, key=lambda e: e.vaccine)],
            doses=sorted(entries, key=lambda e: e.vaccine),
        )
        for d, entries in sorted(by_date.items())
    ]

    pdf_bytes = build_immunization_card_pdf(
        child_name=child.full_name,
        child_dob=child.date_of_birth,
        child_gender=child.gender,
        caregiver_name=child.caregiver.full_name,
        caregiver_phone=child.caregiver.phone_number,
        rows=rows,
        updated_at=timezone.localtime(),
    )

    events = schedule_to_events(
        child_name=child.full_name,
        caregiver_name=child.caregiver.full_name,
        schedules=grouped,
    )
    ics_bytes = build_ics(events)
    google_links = [google_calendar_link(e, tz=getattr(settings, "TIME_ZONE", "Africa/Kampala")) for e in events]

    _record_calendar_events(child, events)

    return pdf_bytes, ics_bytes, google_links


def _record_calendar_events(child: Child, events) -> int:
    """Persist one CalendarEvent row per generated .ics event.

    The persistence tier is the channel with no delivery receipt of any kind,
    so without a row of its own there is nothing to attribute an outcome to in
    the planned trial. Idempotent on (child, date).
    """
    created = 0
    for event in events:
        uid = f"{child.unique_id}:{event.start_date.isoformat()}"
        _, was_created = CalendarEventRecord.objects.get_or_create(
            ics_uid=uid,
            defaults={
                "child": child,
                "title": event.title,
                "event_date": event.start_date,
            },
        )
        created += int(was_created)
    return created


def upsert_immunization_card(child: Child, pdf_bytes: bytes) -> ImmunizationCard:
    """
    Create or replace stored immunization card PDF for a child.
    """
    card, _ = ImmunizationCard.objects.get_or_create(child=child, defaults={"pdf": ContentFile(b"", name="tmp.pdf")})

    filename = f"immunization-card-{child.unique_id}.pdf"
    card.pdf.save(filename, ContentFile(pdf_bytes), save=True)
    return card



def card_progress(child: Child) -> str:
    total = child.vaccines.count()
    given = child.vaccines.filter(administered=True).count()
    return f"{given} of {total} doses given"


def queue_card_update(child: Child, *, reason: str, schedule: VaccineSchedule | None = None):
    """Refresh the stored card and queue it for the caregiver.

    The card is regenerated locally (works offline) and the email goes through
    the same queue as every other outbound message, so a dose recorded at a
    facility with no connectivity is delivered when the link returns. Returns
    the NotificationLog row, or None if an identical update is already queued
    or the caregiver has no email address.
    """
    from apps.immunization.models import NotificationLog
    from apps.immunization.services import notification_service as notify

    # Nothing to deliver to: do not leave a row that can never drain.
    if not (child.caregiver.email or child.caregiver.alternate_email):
        return None

    # The PDF is deliberately not built here. It is rebuilt and stored when the
    # message is dispatched, so a dose recorded on the sync hot path costs one
    # queue insert rather than a PDF render, and an update that waits out an
    # outage still carries the card as it stands when it leaves.

    # One update per change event: keyed on the dose and the instant it was
    # saved, so tick -> cancel -> tick yields three updates, while a repeated
    # call for the same save is idempotent. Manual sends key on the clock.
    stamp = schedule.updated_at if schedule is not None and schedule.updated_at else timezone.now()
    key = f"card:{child.pk}:{schedule.pk if schedule is not None else 'manual'}:{stamp.isoformat()}"
    return notify.enqueue(
        child,
        channel=NotificationLog.Channel.EMAIL,
        tier=NotificationLog.Tier.CARD_UPDATE,
        message=f"Updated immunization card ({reason}). {card_progress(child)}.",
        dedupe_key=key,
    )
