"""Point-of-care activation.

Registering a child immediately produces the schedule, the PDF card, and the
calendar events, and queues the caregiver email. The email is *queued*, not
sent inline: an SMTP round-trip on the request path made registration block for
seconds against a remote mail server, and it fails outright at a facility with
no connectivity -- which is precisely the condition the system exists to work
under. Queueing keeps the doctor's session fast and makes the offline and
online paths identical.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Child, NotificationLog
from .services import notification_service as notify
from .services.card_service import build_child_card_assets, upsert_immunization_card
from .services.scheduler import generate_schedule


@receiver(post_save, sender=Child)
def child_post_save(sender, instance, created, **kwargs):
    if not created or instance.vaccines.exists():
        return

    generate_schedule(instance)

    # Local generation: available regardless of network state.
    pdf_bytes, _ics_bytes, _links = build_child_card_assets(instance)
    upsert_immunization_card(instance, pdf_bytes)

    notify.enqueue(
        instance,
        channel=NotificationLog.Channel.EMAIL,
        tier=NotificationLog.Tier.PERSISTENCE,
        message=(
            "Your child's immunization card and vaccination calendar are attached. "
            "Add the calendar file to your phone so each visit appears automatically."
        ),
        dedupe_key="registration:%s" % instance.unique_id,
    )
