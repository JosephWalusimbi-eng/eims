from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlencode


@dataclass(frozen=True)
class CalendarEvent:
    title: str
    start_date: date  # all-day
    end_date_exclusive: date  # all-day end (exclusive)
    description: str = ""
    location: str = ""


def _format_yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def google_calendar_link(event: CalendarEvent, tz: str = "Africa/Kampala") -> str:
    """
    Returns a Google Calendar 'TEMPLATE' URL for an all-day event.
    """
    params = {
        "action": "TEMPLATE",
        "text": event.title,
        "dates": f"{_format_yyyymmdd(event.start_date)}/{_format_yyyymmdd(event.end_date_exclusive)}",
        "details": event.description,
        "location": event.location,
        "ctz": tz,
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def build_ics(
    events: Iterable[CalendarEvent],
    *,
    prod_id: str = "-//EIMS//Immunization Schedule//EN",
) -> bytes:
    """
    Build a single .ics file with multiple all-day events suitable for iOS/Apple Calendar.
    """
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"PRODID:{prod_id}",
        "METHOD:PUBLISH",
    ]

    for idx, event in enumerate(events, start=1):
        uid = f"eims-{dtstamp}-{idx}@eims"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{dtstamp}",
                f"SUMMARY:{_ics_escape(event.title)}",
                f"DTSTART;VALUE=DATE:{_format_yyyymmdd(event.start_date)}",
                f"DTEND;VALUE=DATE:{_format_yyyymmdd(event.end_date_exclusive)}",
                f"DESCRIPTION:{_ics_escape(event.description)}",
                f"LOCATION:{_ics_escape(event.location)}",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    # RFC 5545 prefers CRLF line endings
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def schedule_to_events(
    *,
    child_name: str,
    caregiver_name: str,
    schedules: list[tuple[date, list[str]]],
    location: str = "",
    minimise: bool = True,
) -> list[CalendarEvent]:
    """Convert grouped schedules into all-day calendar events.

    schedules: list of (due_date, [vaccine_name,...]) grouped by due_date.

    A calendar entry is the least private of the three channels: it syncs to a
    cloud account, renders on a lock screen, and is visible to anyone the
    calendar is shared with. With `minimise` on (the default) the event
    therefore carries only a given name, the date, and where to go -- no
    vaccine names, no surname, no caregiver identity. The full schedule stays
    in the emailed PDF and behind an authenticated session.
    """
    given = (child_name or "").strip().split(" ")[0] or "your child"
    events: list[CalendarEvent] = []
    for due_date, vaccine_names in schedules:
        if minimise:
            title = "Immunization visit"
            description = (
                "Immunization visit for " + given + ".\n"
                "Please attend your nearest health facility on this date.\n"
                "Bring the immunization card."
            )
        else:
            vaccines = ", ".join(vaccine_names)
            title = "Immunization visit: " + vaccines
            description = (
                "Child: " + child_name + "\n"
                "Caregiver: " + caregiver_name + "\n"
                "Vaccines due: " + vaccines + "\n"
                "Please attend your nearest health facility."
            )
        events.append(
            CalendarEvent(
                title=title,
                start_date=due_date,
                end_date_exclusive=due_date + timedelta(days=1),
                description=description,
                location=location,
            )
        )
    return events
