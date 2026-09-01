from __future__ import annotations

from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import EmailMessage

from apps.immunization.models import Child


def send_vaccine_due_in_five_days_reminder_email(
    *,
    child: Child,
    due_date,
    vaccine_names: list[str],
) -> int:
    """
    Email caregiver that specific vaccine doses are due in five days (due_date is today+5).
    Returns number of messages sent (0 if no email addresses).
    """
    caregiver = child.caregiver
    recipients = [e for e in [caregiver.email, caregiver.alternate_email] if e]
    if not recipients:
        return 0

    vaccines_line = ", ".join(vaccine_names)
    subject = f"EIMS reminder: immunization in 5 days — {child.full_name}"
    lines = [
        f"Hello {caregiver.full_name},",
        "",
        f"This is a reminder from EIMS about {child.full_name}.",
        f"The following vaccine(s) are due on {due_date} (in 5 days): {vaccines_line}.",
        "",
        "Please plan to visit your nearest health facility on or before that date.",
        "",
        "EIMS",
    ]
    body = "\n".join(lines)
    msg = EmailMessage(
        subject=subject,
        body=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=recipients,
    )
    return msg.send(fail_silently=True)


def send_immunization_card_email(
    *,
    child: Child,
    pdf_bytes: bytes,
    ics_bytes: bytes,
    google_links: list[str],
    pdf_download_url: str,
    ics_download_url: str,
) -> int:
    caregiver = child.caregiver
    recipients = [e for e in [caregiver.email, caregiver.alternate_email] if e]
    if not recipients:
        return 0

    base = getattr(settings, "SITE_URL", "").rstrip("/")
    if base:
        if pdf_download_url:
            pdf_download_url = urljoin(base + "/", pdf_download_url.lstrip("/"))
        if ics_download_url:
            ics_download_url = urljoin(base + "/", ics_download_url.lstrip("/"))

    subject = f"EIMS Immunization Card - {child.full_name}"
    lines = [
        f"Hello {caregiver.full_name},",
        "",
        f"An immunization schedule has been created for {child.full_name}.",
        "",
        "The schedule file (.ics) uses standard calendar format. Each visit is already set to the correct due date; "
        "open or import the attachment and the events will appear on those dates in your calendar.",
        "",
        "How to add to your calendar:",
        "- Google Calendar (web/Android): Settings > Import (web), or open the .ics attachment in Gmail/Google Calendar.",
        "- iPhone/iPad (Apple Calendar): tap the .ics attachment, then Add All.",
        "- Android (other apps): open the attachment and choose your calendar app, or use the Google links below.",
        "",
        "Google Calendar (one link per visit — opens with pre-filled dates):",
        *[f"- {link}" for link in google_links],
        "",
        "Downloads (same as attachments):",
        f"- Immunization card (PDF): {pdf_download_url}",
        f"- Schedule file (.ics): {ics_download_url}",
        "",
        "The PDF and .ics are attached to this email.",
        "",
        "EIMS",
    ]
    body = "\n".join(lines)

    msg = EmailMessage(
        subject=subject,
        body=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=recipients,
    )
    msg.attach(f"immunization-card-{child.unique_id}.pdf", pdf_bytes, "application/pdf")
    msg.attach(f"immunization-schedule-{child.unique_id}.ics", ics_bytes, "text/calendar; charset=utf-8")
    return msg.send(fail_silently=True)



def deliver_queued_email(log) -> tuple[bool, str]:
    """Dispatch one queued NotificationLog row over SMTP.

    Email reaches a personal inbox rather than a shared handset, so unlike SMS
    it may carry the full reminder body composed at enqueue time. A
    registration email additionally carries the PDF card and the .ics
    schedule, which are rebuilt here rather than stored on the queue row.
    """
    from apps.immunization.models import NotificationLog

    caregiver = log.child.caregiver
    recipients = [e for e in [caregiver.email, caregiver.alternate_email] if e]
    if not recipients:
        return False, "no caregiver email on record"

    try:
        if log.tier == NotificationLog.Tier.PERSISTENCE:
            from apps.immunization.services.card_service import build_child_card_assets
            from apps.immunization.views import sign_artifact_link

            pdf_bytes, ics_bytes, google_links = build_child_card_assets(log.child)
            return (
                bool(
                    send_immunization_card_email(
                        child=log.child, pdf_bytes=pdf_bytes, ics_bytes=ics_bytes,
                        google_links=google_links,
                        pdf_download_url=sign_artifact_link(log.child, "pdf"),
                        ics_download_url=sign_artifact_link(log.child, "ics"),
                    )
                ),
                "",
            )

        msg = EmailMessage(
            subject="EIMS immunization reminder",
            body=log.message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=recipients,
        )
        n = msg.send(fail_silently=False)
        return bool(n), "" if n else "SMTP returned 0"
    except Exception as exc:  # noqa: BLE001 - reported, row stays queued for retry
        return False, str(exc)
