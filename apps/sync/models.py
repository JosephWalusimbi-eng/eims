"""Offline-first synchronization state.

A facility device works against a local replica and records every mutation as
an envelope in a local outbox. When connectivity returns the outbox is flushed
to the server in client-timestamp order. Each envelope carries:

  client_id          idempotency key -- a replayed flush is a no-op
  device_id          which replica produced it
  base_version       the server version the device had when it made the edit
  client_updated_at  the device clock reading at the time of the edit

`base_version` is what makes conflict detection possible: if the server row has
moved past that version, two devices edited the same record from the same
starting point and the resolution policy decides the outcome.
"""
from __future__ import annotations

import uuid

from django.db import models


class SyncStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPLIED = "APPLIED", "Applied"
    DUPLICATE = "DUPLICATE", "Duplicate (idempotent replay)"
    CONFLICT = "CONFLICT", "Conflict resolved"
    REJECTED = "REJECTED", "Rejected"


class Resolution(models.TextChoices):
    NONE = "NONE", "No conflict"
    LWW_CLIENT = "LWW_CLIENT", "Last-write-wins: client won"
    LWW_SERVER = "LWW_SERVER", "Last-write-wins: server won"
    MERGED = "MERGED", "Field-level merge"


class SyncEnvelope(models.Model):
    """One queued mutation from an offline replica."""

    client_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    device_id = models.CharField(max_length=64, db_index=True)
    entity = models.CharField(max_length=64)
    entity_uid = models.CharField(max_length=64, db_index=True)
    operation = models.CharField(max_length=16, default="update")
    payload = models.JSONField(default=dict)
    base_version = models.IntegerField(default=0)
    client_updated_at = models.DateTimeField()

    status = models.CharField(
        max_length=12, choices=SyncStatus.choices, default=SyncStatus.PENDING, db_index=True
    )
    resolution = models.CharField(
        max_length=12, choices=Resolution.choices, default=Resolution.NONE
    )
    detail = models.CharField(max_length=255, blank=True, default="")
    queued_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["client_updated_at", "id"]
        indexes = [models.Index(fields=["status", "client_updated_at"])]

    def __str__(self):
        return f"{self.entity}:{self.entity_uid} {self.operation} [{self.status}]"


class SyncRun(models.Model):
    """One flush of the outbox, for operational monitoring and benchmarking."""

    device_id = models.CharField(max_length=64, blank=True, default="")
    policy = models.CharField(max_length=32, default="merge")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    envelopes = models.IntegerField(default=0)
    applied = models.IntegerField(default=0)
    duplicates = models.IntegerField(default=0)
    conflicts = models.IntegerField(default=0)
    rejected = models.IntegerField(default=0)
    duration_ms = models.FloatField(default=0.0)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"SyncRun {self.device_id} {self.envelopes} envelopes in {self.duration_ms:.1f} ms"
