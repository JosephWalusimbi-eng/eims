"""Multi-channel notification orchestration with an offline queue.

Every outbound message is enqueued first and dispatched second. When the
facility is offline, `enqueue` still succeeds and the row sits at QUEUED until
`dispatch_pending` runs after reconnection -- this is what "no data loss in the
offline scenario" means operationally, and it is what the benchmark measures.

Message content is minimised (paper Sec. VI): the child's given name, the due
date, and the facility. Vaccine names and every other clinical detail are
withheld from SMS and from calendar payloads, because both can be read by
someone other than the caregiver.
"""
from __future__ import annotations

import time

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from apps.immunization.models import NotificationLog


def given_name(full_name: str) -> str:
    """First token only. An SMS naming a child in full on a shared handset is
    more disclosure than the reminder needs."""
    return (full_name or "").strip().split(" ")[0] or "your child"


def facility_name() -> str:
    return getattr(settings, "EIMS_FACILITY_NAME", "your health facility")


def compose(tier: str, child, due_date=None) -> str:
    name = given_name(child.full_name)
    place = facility_name()
    if tier == NotificationLog.Tier.OVERDUE:
        return f"EIMS: {name} has a missed immunization visit. Please come to {place}."
    if due_date is not None:
        return f"EIMS: {name} has an immunization visit due on {due_date}. Please come to {place}."
    return f"EIMS: {name} has an immunization visit due. Please come to {place}."


def enqueue(child, *, channel: str, tier: str = "", message: str = "",
            dedupe_key: str = "") -> NotificationLog | None:
    """Queue one message. Returns None if an identical message is already queued
    or sent, which is what makes a repeated analysis run idempotent."""
    t0 = time.perf_counter()
    if dedupe_key and NotificationLog.objects.filter(
        dedupe_key=dedupe_key,
        status__in=[NotificationLog.Status.QUEUED, NotificationLog.Status.SENT,
                    NotificationLog.Status.DELIVERED],
    ).exists():
        return None

    # Built, timed, then written once. Writing the row and then updating it
    # with its own latency doubled the cost of the cheapest operation in the
    # system, and measured the wrong thing besides.
    log = NotificationLog(
        child=child,
        channel=channel,
        tier=tier,
        message=message,
        status=NotificationLog.Status.QUEUED,
        dedupe_key=dedupe_key,
    )
    log.enqueue_ms = (time.perf_counter() - t0) * 1000.0
    log.save()
    return log


CLAIMED_ELSEWHERE = "claimed by another dispatcher"


def dispatch_log(log: NotificationLog) -> tuple[bool, str]:
    """Attempt one queued row. On failure the row stays QUEUED for retry."""
    from apps.immunization.services.email_service import deliver_queued_email
    from apps.immunization.services.sms_service import deliver_sms
    from apps.immunization.services.voice_service import deliver_voice

    # Claim the row before touching a gateway. Overlapping drains (two quick
    # dose ticks, a sync flush, the manual button) read the same QUEUED rows;
    # the conditional UPDATE lets exactly one of them proceed, so a message is
    # never sent twice. Matching on `attempts` makes a stale in-memory copy
    # lose the race instead of overwriting the winner.
    claimed = NotificationLog.objects.filter(
        pk=log.pk, status=NotificationLog.Status.QUEUED, attempts=log.attempts
    ).update(attempts=F("attempts") + 1)
    if not claimed:
        return False, CLAIMED_ELSEWHERE
    log.attempts += 1

    t0 = time.perf_counter()
    if log.channel == NotificationLog.Channel.SMS:
        ok, err = deliver_sms(log.child, log.message)
    elif log.channel == NotificationLog.Channel.EMAIL:
        ok, err = deliver_queued_email(log)
    elif log.channel == NotificationLog.Channel.VOICE:
        ok, err = deliver_voice(log.child, log.message)
    else:
        ok, err = True, ""
    dt = (time.perf_counter() - t0) * 1000.0

    log.gateway_ms = dt
    if ok:
        log.status = NotificationLog.Status.SENT
        log.dispatched_at = timezone.now()
        log.error = ""
    else:
        # Stays QUEUED so an offline window is retried, not dropped.
        log.error = (err or "")[:255]
    log.save(update_fields=["attempts", "gateway_ms", "status", "dispatched_at", "error"])
    return ok, err or ""


def dispatch_pending(limit: int | None = None) -> dict:
    """Drain the queue. Safe to call repeatedly; safe to call while offline
    (failures stay QUEUED and are retried on the next run)."""
    qs = NotificationLog.objects.filter(
        status=NotificationLog.Status.QUEUED
    ).select_related("child", "child__caregiver").order_by("queued_at")
    if limit:
        qs = qs[:limit]

    sent = failed = 0
    latencies = []
    for log in qs:
        ok, err = dispatch_log(log)
        if err == CLAIMED_ELSEWHERE:
            continue
        latencies.append(log.gateway_ms)
        if ok:
            sent += 1
        else:
            failed += 1

    latencies.sort()
    return {
        "sent": sent,
        "failed": failed,
        "total": sent + failed,
        "median_gateway_ms": latencies[len(latencies) // 2] if latencies else 0.0,
    }


def dispatch_pending_in_background() -> None:
    """Drain the queue after the current transaction commits, off the request
    thread. A doctor ticking a dose must not wait on an SMTP round-trip (nor
    fail when the facility is offline); the row is already durable in the
    queue, so a failed or skipped drain only delays delivery."""
    import threading

    from django.db import connection, transaction

    # At most one drain per transaction: a sync flush that flips many doses
    # would otherwise start one thread per dose.
    if getattr(connection, "_eims_drain_scheduled", False):
        return
    connection._eims_drain_scheduled = True

    def _drain():
        try:
            dispatch_pending()
        except Exception:  # noqa: BLE001 - rows remain QUEUED for the next pass
            pass
        finally:
            connection.close()

    def _start():
        connection._eims_drain_scheduled = False
        threading.Thread(target=_drain, daemon=True).start()

    transaction.on_commit(_start)


def queue_depth() -> int:
    return NotificationLog.objects.filter(status=NotificationLog.Status.QUEUED).count()
