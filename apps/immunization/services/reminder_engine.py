"""Three-tier reminder orchestration.

Tiers (paper Sec. III-F):
    PLANNING     email at T-5 days   -- lets a caregiver arrange the trip
    URGENCY      SMS at T-3 days     -- drives the action
    OVERDUE      SMS on detection    -- recovers a missed dose
    PERSISTENCE  .ics at registration -- passive, in the caregiver's calendar

Every tier writes into the same NotificationLog queue rather than calling a
gateway directly, so the engine behaves identically online and offline: it
enqueues, and dispatch drains whenever connectivity allows.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from apps.immunization.models import Child, NotificationLog, VaccineSchedule
from apps.immunization.services.detection import (
    get_children_with_upcoming_vaccines,
    get_overdue_children,
    get_upcoming_vaccines,
)
from apps.immunization.services import notification_service as notify


def send_overdue_reminders() -> int:
    """Queue an overdue SMS for each child with at least one missed dose."""
    today = timezone.now().date()
    count = 0
    for child in get_overdue_children().select_related("caregiver"):
        log = notify.enqueue(
            child,
            channel=NotificationLog.Channel.SMS,
            tier=NotificationLog.Tier.OVERDUE,
            message=notify.compose(NotificationLog.Tier.OVERDUE, child),
            dedupe_key=f"overdue:{child.pk}:{today.isoformat()}",
        )
        if log is not None:
            count += 1
    return count


def send_upcoming_due_reminders(days_ahead: int = 3) -> int:
    """Queue the urgency-tier SMS for doses due within `days_ahead`."""
    count = 0
    for child in get_children_with_upcoming_vaccines(days_ahead=days_ahead).select_related("caregiver"):
        upcoming = (
            get_upcoming_vaccines(days_ahead=days_ahead)
            .filter(child=child)
            .order_by("due_date")
            .first()
        )
        if upcoming is None:
            continue
        log = notify.enqueue(
            child,
            channel=NotificationLog.Channel.SMS,
            tier=NotificationLog.Tier.URGENCY,
            message=notify.compose(NotificationLog.Tier.URGENCY, child, upcoming.due_date),
            dedupe_key=f"urgency:{child.pk}:{upcoming.due_date.isoformat()}",
        )
        if log is not None:
            count += 1
    return count


def send_five_day_email_reminders() -> int:
    """Queue the planning-tier email for doses due in exactly five days."""
    today = timezone.now().date()
    target_due = today + timedelta(days=5)
    schedules = (
        VaccineSchedule.objects.filter(
            due_date=target_due,
            administered=False,
            five_day_email_reminder_sent=False,
        )
        .select_related("child", "child__caregiver")
        .order_by("child_id", "vaccine_name")
    )
    by_child: dict = defaultdict(list)
    for s in schedules:
        by_child[s.child].append(s)

    queued = 0
    for child, slist in by_child.items():
        vaccines = ", ".join(s.vaccine_name for s in slist)
        body = (
            f"Hello,\n\nThis is a reminder from EIMS about {child.full_name}.\n"
            f"The following vaccine(s) are due on {target_due} (in 5 days): {vaccines}.\n\n"
            "Please plan to visit your nearest health facility on or before that date.\n\nEIMS"
        )
        log = notify.enqueue(
            child,
            channel=NotificationLog.Channel.EMAIL,
            tier=NotificationLog.Tier.PLANNING,
            message=body,
            dedupe_key=f"planning:{child.pk}:{target_due.isoformat()}",
        )
        if log is not None:
            VaccineSchedule.objects.filter(pk__in=[s.pk for s in slist]).update(
                five_day_email_reminder_sent=True
            )
            queued += 1
    return queued


def send_voice_escalations() -> int:
    """Queue an automated call for each child whose latest tier is HIGH.

    One call per child per day. Escalation, not first contact: these are the
    children with two or more missed doses, for whom the SMS has already been
    queued by the overdue tier.
    """
    from apps.immunization.services import atrisk
    from apps.immunization.services.voice_service import queue_voice_call

    today = timezone.now().date()
    count = 0
    for child in Child.objects.filter(pk__in=atrisk.high_risk_child_ids()).select_related("caregiver"):
        log = queue_voice_call(
            child, overdue=True,
            dedupe_key=f"voice:{child.pk}:{today.isoformat()}",
        )
        if log is not None:
            count += 1
    return count
