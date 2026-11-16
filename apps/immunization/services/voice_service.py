"""Automated voice-call channel.

An outbound call is the escalation tier: it is used for children whose latest
risk tier is HIGH (two or more missed doses) and on demand from the doctor
portal, where an SMS has already been tried or a caregiver cannot read one.

The "assistant" is a scripted text-to-speech agent, deliberately not a
generative one: what is spoken is fixed by `compose_script`, so the content is
auditable and carries the same minimum disclosure as an SMS (given name and
facility; no vaccine names, no clinical detail), because a phone call is
overheard as easily as a shared handset is read.

Limitation: the telephony provider's text-to-speech has no Luganda, Ateso or
Runyankole voice, so every call is spoken in English regardless of the
caregiver's recorded `preferred_language`.

Dispatch goes through the same NotificationLog queue as SMS and email: queued
first, delivered when connectivity allows. With no Twilio credentials the
channel runs a deterministic loopback, exactly as the SMS channel does.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape

from django.conf import settings

from apps.immunization.models import NotificationLog
from apps.immunization.services.notification_service import facility_name, given_name

SPOKEN_LANGUAGE = "English"


def compose_script(child, due_date=None, overdue: bool = True) -> str:
    """The exact words the assistant speaks. Minimal disclosure by design."""
    name = given_name(child.full_name)
    place = facility_name()
    if overdue or due_date is None:
        core = f"This is the immunization assistant from {place}. {name} has a missed immunization visit."
    else:
        core = f"This is the immunization assistant from {place}. {name} has an immunization visit due on {due_date}."
    return f"{core} Please bring {name} to {place} as soon as you can. Thank you."


def voice_configured() -> bool:
    return all(
        (getattr(settings, n, "") or "")
        for n in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
    )


def build_twiml(script: str) -> str:
    # Spoken twice with a pause: callers on a poor line often miss the opening.
    say = escape(script)
    return (
        "<Response>"
        f'<Say language="en-GB">{say}</Say><Pause length="2"/>'
        f'<Say language="en-GB">{say}</Say>'
        "</Response>"
    )


def _twilio_call(*, to: str, script: str) -> tuple[bool, str]:
    sid = settings.TWILIO_ACCOUNT_SID
    token = settings.TWILIO_AUTH_TOKEN
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
    data = urllib.parse.urlencode(
        {"To": to, "From": settings.TWILIO_FROM_NUMBER, "Twiml": build_twiml(script)}
    ).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode("ascii"))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return (True, "") if resp.status in (200, 201) else (False, f"HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", errors="replace")).get("message", str(e))
        except (ValueError, json.JSONDecodeError):
            detail = str(e)
        return False, detail
    except OSError as e:
        return False, str(e)


def deliver_voice(child, script: str) -> tuple[bool, str]:
    """Place one call. Returns (ok, error). Loopback when no gateway is set."""
    phone = (child.caregiver.phone_number or "").strip()
    if not phone:
        return False, "no caregiver phone number on record"
    if voice_configured():
        return _twilio_call(to=phone, script=script)
    return True, ""


def queue_voice_call(child, *, due_date=None, overdue: bool = True, dedupe_key: str = ""):
    from apps.immunization.services import notification_service as notify

    return notify.enqueue(
        child,
        channel=NotificationLog.Channel.VOICE,
        tier=NotificationLog.Tier.VOICE_CALL,
        message=compose_script(child, due_date=due_date, overdue=overdue),
        dedupe_key=dedupe_key,
    )
