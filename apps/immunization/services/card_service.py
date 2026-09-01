from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.conf import settings
from django.core.files.base import ContentFile

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
from apps.immunization.services.pdf_service import ImmunizationCardRow, build_immunization_card_pdf


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

    rows = [ImmunizationCardRow(due_date=d, vaccines=vaccines) for d, vaccines in grouped]

    pdf_bytes = build_immunization_card_pdf(
        child_name=child.full_name,
        child_dob=child.date_of_birth,
        child_gender=child.gender,
        caregiver_name=child.caregiver.full_name,
        caregiver_phone=child.caregiver.phone_number,
        rows=rows,
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

