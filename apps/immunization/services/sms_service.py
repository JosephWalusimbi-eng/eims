import base64
import json
import random
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from apps.immunization.models import SMSLog


def _twilio_send(*, to: str, body: str) -> tuple[bool, str]:
    account_sid = settings.TWILIO_ACCOUNT_SID
    auth_token = settings.TWILIO_AUTH_TOKEN
    from_number = settings.TWILIO_FROM_NUMBER
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = urllib.parse.urlencode(
        {
            "To": to,
            "From": from_number,
            "Body": body,
        }
    ).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode("ascii")
    req.add_header("Authorization", f"Basic {credentials}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 201):
                return True, ""
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            detail = json.loads(err_body).get("message", err_body)
        except (ValueError, json.JSONDecodeError):
            detail = str(e)
        return False, detail
    except OSError as e:
        return False, str(e)


def send_sms(child, message) -> SMSLog:
    """
    Send an SMS to the child's caregiver phone number.

    If TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER are set in
    settings (via environment variables), sends via Twilio REST API.

    Otherwise simulates sending: creates SMSLog with random SENT/FAILED (legacy dev behavior).
    """
    phone_number = (child.caregiver.phone_number or "").strip()

    sid = getattr(settings, "TWILIO_ACCOUNT_SID", "") or ""
    token = getattr(settings, "TWILIO_AUTH_TOKEN", "") or ""
    from_num = getattr(settings, "TWILIO_FROM_NUMBER", "") or ""

    if sid and token and from_num and phone_number:
        ok, _err = _twilio_send(to=phone_number, body=message)
        status = SMSLog.Status.SENT if ok else SMSLog.Status.FAILED
        return SMSLog.objects.create(
            child=child,
            message=message,
            status=status,
        )

    # No provider configured: keep local simulation for development
    status = random.choice([SMSLog.Status.SENT, SMSLog.Status.FAILED])
    return SMSLog.objects.create(
        child=child,
        message=message,
        status=status,
    )


def deliver_sms(child, message: str) -> tuple[bool, str]:
    """Dispatch one SMS. Returns (ok, error).

    With no gateway configured this reports a deterministic local-loopback
    success rather than the random SENT/FAILED the prototype used, because a
    coin flip in the delivery path makes every measured delivery rate
    meaningless. Simulation mode is signalled explicitly by the caller.
    """
    phone_number = (child.caregiver.phone_number or "").strip()
    if not phone_number:
        return False, "no caregiver phone number on record"

    sid = getattr(settings, "TWILIO_ACCOUNT_SID", "") or ""
    token = getattr(settings, "TWILIO_AUTH_TOKEN", "") or ""
    from_num = getattr(settings, "TWILIO_FROM_NUMBER", "") or ""

    if sid and token and from_num:
        return _twilio_send(to=phone_number, body=message)

    # Loopback: no gateway configured. Succeeds deterministically so that
    # queue-drain measurements reflect the system, not a simulated failure rate.
    return True, ""


def gateway_configured() -> bool:
    return all(
        (getattr(settings, name, "") or "")
        for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
    )
