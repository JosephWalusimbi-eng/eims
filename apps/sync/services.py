"""Conflict-aware synchronization.

Two resolution policies are implemented so the paper can compare them on the
same workload rather than assert that one is better:

  last_write_wins   the envelope with the later client timestamp overwrites the
                    row wholesale. Simple, and the policy the original
                    prototype described. It can silently discard a clinical
                    fact: if device A records that Penta1 was administered and
                    device B, offline and unaware, edits the same row a minute
                    later, the administration is lost.

  merge             field-level merge with a monotonic rule for clinical
                    facts. `administered` is a latch: once any replica reports
                    a dose given, no later write can un-administer it, and the
                    earliest reported administration date is kept. Non-clinical
                    fields (due_date, reminder flags) fall back to
                    last-write-wins.

The merge policy is the deployed default. `last_write_wins` is retained because
the evaluation in the paper quantifies exactly what it costs.
"""
from __future__ import annotations

import time
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.immunization.models import Child, VaccineSchedule
from apps.sync.models import Resolution, SyncEnvelope, SyncRun, SyncStatus

CHUNK = 500

CLINICAL_LATCH_FIELDS = ("administered",)
MERGEABLE_FIELDS = (
    "administered",
    "administered_date",
    "due_date",
    "five_day_email_reminder_sent",
)


def enqueue(
    *,
    device_id: str,
    entity: str,
    entity_uid: str,
    payload: dict,
    base_version: int = 0,
    client_updated_at=None,
    operation: str = "update",
    client_id=None,
) -> SyncEnvelope:
    """Record a mutation made while offline. Idempotent on client_id."""
    kwargs = dict(
        device_id=device_id,
        entity=entity,
        entity_uid=str(entity_uid),
        payload=payload,
        base_version=base_version,
        operation=operation,
        client_updated_at=client_updated_at or timezone.now(),
    )
    if client_id is not None:
        # A retry after a dropped connection must not enqueue a second copy.
        # The stored envelope is returned untouched -- in particular its
        # PENDING status survives, so the original edit still gets applied.
        env, _created = SyncEnvelope.objects.get_or_create(client_id=client_id, defaults=kwargs)
        return env
    return SyncEnvelope.objects.create(**kwargs)


def _as_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _is_newer(env: SyncEnvelope, row: VaccineSchedule) -> bool:
    """Does this envelope carry a later device observation than the row holds?

    Conflicts are ordered by device clock, never by server apply time.
    Comparing against `updated_at` would rank edits by when each facility
    regained connectivity, so whichever device reconnected last would win
    regardless of when the clinical work actually happened. A row that has
    never taken a device edit has no client clock, and any envelope is newer.
    """
    clock = row.last_client_write_at
    return clock is None or env.client_updated_at > clock


def _apply_schedule(row: VaccineSchedule, env: SyncEnvelope, policy: str):
    """Apply one envelope to a VaccineSchedule row. Returns a Resolution."""
    payload = env.payload or {}
    conflicted = row.version > env.base_version

    if not conflicted:
        for field in MERGEABLE_FIELDS:
            if field in payload:
                setattr(row, field, _coerce(field, payload[field]))
        return Resolution.NONE

    if policy == "last_write_wins":
        if not _is_newer(env, row):
            return Resolution.LWW_SERVER
        for field in MERGEABLE_FIELDS:
            if field in payload:
                setattr(row, field, _coerce(field, payload[field]))
        return Resolution.LWW_CLIENT

    # Field-level merge with a monotonic latch on clinical facts.
    for field in MERGEABLE_FIELDS:
        if field not in payload:
            continue
        incoming = _coerce(field, payload[field])
        if field in CLINICAL_LATCH_FIELDS:
            # True latches: a dose reported administered stays administered.
            setattr(row, field, bool(getattr(row, field)) or bool(incoming))
        elif field == "administered_date":
            current = row.administered_date
            if incoming is not None and (current is None or incoming < current):
                row.administered_date = incoming
        elif _is_newer(env, row):
            setattr(row, field, incoming)
    return Resolution.MERGED


def _coerce(field, value):
    if field in ("administered", "five_day_email_reminder_sent"):
        return bool(value)
    if field in ("due_date", "administered_date"):
        return _as_date(value)
    return value


def _apply_one(env: SyncEnvelope, policy: str) -> str:
    if env.entity != "VaccineSchedule":
        env.status = SyncStatus.REJECTED
        env.detail = f"unsupported entity {env.entity}"
        env.save(update_fields=["status", "detail"])
        return "rejected"

    row = (
        VaccineSchedule.objects.select_for_update()
        .filter(sync_uid=env.entity_uid)
        .first()
    )
    if row is None:
        env.status = SyncStatus.REJECTED
        env.detail = "unknown entity_uid"
        env.save(update_fields=["status", "detail"])
        return "rejected"

    resolution = _apply_schedule(row, env, policy)
    if resolution != Resolution.LWW_SERVER:
        row.version += 1
        row.sync_status = "SYNCED"
        if _is_newer(env, row):
            row.last_client_write_at = env.client_updated_at
        row.save()

    env.resolution = resolution
    env.status = SyncStatus.CONFLICT if resolution != Resolution.NONE else SyncStatus.APPLIED
    env.applied_at = timezone.now()
    env.save(update_fields=["resolution", "status", "applied_at"])
    return "conflict" if resolution != Resolution.NONE else "applied"


def flush_queue(*, device_id: str = "", policy: str = "merge", limit: int | None = None) -> SyncRun:
    """Upload and reconcile every pending envelope. Returns the SyncRun record."""
    run = SyncRun.objects.create(device_id=device_id, policy=policy)
    qs = SyncEnvelope.objects.filter(status=SyncStatus.PENDING)
    if device_id:
        qs = qs.filter(device_id=device_id)
    qs = qs.order_by("client_updated_at", "id")
    if limit:
        qs = qs[:limit]

    envelopes = list(qs)
    counters = {"applied": 0, "conflict": 0, "rejected": 0, "duplicate": 0}

    # Envelopes are committed in chunks rather than one transaction each. A
    # per-envelope commit made the flush fsync-bound, which is the wrong cost
    # to pay on the one operation that runs after a long offline window with a
    # large backlog. A chunk is still atomic, so a failure rolls back at most
    # CHUNK envelopes, all of which stay PENDING and are retried.
    t0 = time.perf_counter()
    for start in range(0, len(envelopes), CHUNK):
        with transaction.atomic():
            for env in envelopes[start:start + CHUNK]:
                counters[_apply_one(env, policy)] += 1
    duration_ms = (time.perf_counter() - t0) * 1000.0

    run.finished_at = timezone.now()
    run.envelopes = len(envelopes)
    run.applied = counters["applied"]
    run.conflicts = counters["conflict"]
    run.rejected = counters["rejected"]
    run.duplicates = counters["duplicate"]
    run.duration_ms = duration_ms
    run.save()
    return run


def pending_count(device_id: str = "") -> int:
    qs = SyncEnvelope.objects.filter(status=SyncStatus.PENDING)
    if device_id:
        qs = qs.filter(device_id=device_id)
    return qs.count()
