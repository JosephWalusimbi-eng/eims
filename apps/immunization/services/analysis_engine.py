"""Full analysis pass: rescore every child, then queue and dispatch reminders."""
from apps.immunization.models import Child
from apps.immunization.services import notification_service as notify
from apps.immunization.services.reminder_engine import (
    send_five_day_email_reminders,
    send_overdue_reminders,
    send_upcoming_due_reminders,
)
from apps.immunization.services.risk_engine import calculate_risk


def run_analysis(dispatch: bool = True):
    """Returns (children_processed, reminders_queued).

    Scoring and queueing always run. Dispatch is separate and may legitimately
    do nothing -- that is the offline case, where messages accumulate in the
    queue until connectivity returns.
    """
    processed = 0
    for child in Child.objects.select_related("caregiver").iterator(chunk_size=500):
        calculate_risk(child)
        processed += 1

    queued = (
        send_overdue_reminders()
        + send_upcoming_due_reminders(days_ahead=3)
        + send_five_day_email_reminders()
    )

    if dispatch:
        notify.dispatch_pending()

    return processed, queued
