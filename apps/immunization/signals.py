"""Point-of-care activation.

Registering a child immediately produces the schedule, the PDF card, and the
calendar events, and queues the caregiver email. The email is *queued*, not
sent inline: an SMTP round-trip on the request path made registration block for
seconds against a remote mail server, and it fails outright at a facility with
no connectivity -- which is precisely the condition the system exists to work
under. Queueing keeps the doctor's session fast and makes the offline and
online paths identical.
"""
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Child, NotificationLog, VaccineSchedule
from .services import notification_service as notify
from .services.card_service import (
    build_child_card_assets,
    queue_card_update,
    upsert_immunization_card,
)
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


# --- card self-update ------------------------------------------------------
#
# Whenever a dose flips administered <-> not administered, by whatever route
# (doctor portal, a device edit merged by the sync engine, the admin), the
# caregiver's card is regenerated with the tick and queued for resend. Hooking
# the model rather than the view is what makes "every administered dose" true
# for the offline path as well. bulk_update/bulk_create bypass signals, so
# seeding and benchmark loads do not generate mail.

@receiver(pre_save, sender=VaccineSchedule)
def _remember_administered_state(sender, instance, **kwargs):
    instance._was_administered = None
    if instance.pk:
        instance._was_administered = (
            VaccineSchedule.objects.filter(pk=instance.pk)
            .values_list("administered", flat=True)
            .first()
        )


@receiver(post_save, sender=VaccineSchedule)
def _resend_card_on_dose_change(sender, instance, created, **kwargs):
    was = getattr(instance, "_was_administered", None)
    if created or was is None or was == instance.administered:
        return
    verb = "dose recorded" if instance.administered else "dose cancelled"
    queue_card_update(
        instance.child,
        reason=f"{verb}: {instance.vaccine_name}",
        schedule=instance,
    )
    notify.dispatch_pending_in_background()
