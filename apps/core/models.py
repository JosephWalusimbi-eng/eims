"""Audit trail.

Uganda's Data Protection and Privacy Act, 2019 places accountability
obligations on the data controller. EIMS therefore records every access to a
child record -- reads as well as writes -- in a log that the application layer
cannot amend or delete.
"""
from __future__ import annotations

from django.db import models


class AuditLog(models.Model):
    """Append-only record of access to identifiable child data.

    Append-only is enforced in two places: `save()` refuses to overwrite an
    existing row, and `delete()` is disabled. A database role that lacks UPDATE
    and DELETE on this table is the deployment-side half of the same control
    and is documented in docs/DEPLOYMENT_SECURITY.md.
    """

    class Action(models.TextChoices):
        READ = "READ", "Read"
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"
        EXPORT = "EXPORT", "Export"
        LOGIN = "LOGIN", "Login"
        DENIED = "DENIED", "Access denied"

    actor = models.CharField(max_length=150, blank=True, default="")
    actor_id = models.IntegerField(null=True, blank=True)
    action = models.CharField(max_length=10, choices=Action.choices)
    entity = models.CharField(max_length=64)
    entity_uid = models.CharField(max_length=64, blank=True, default="")
    detail = models.CharField(max_length=255, blank=True, default="")
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["entity", "entity_uid"])]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("AuditLog rows are append-only and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditLog rows are append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.timestamp} {self.actor} {self.action} {self.entity}:{self.entity_uid}"


def record(request, action: str, entity: str, entity_uid: str = "", detail: str = ""):
    """Write one audit row. Never raises into the request path."""
    user = getattr(request, "user", None)
    try:
        AuditLog.objects.create(
            actor=getattr(user, "username", "") or "anonymous",
            actor_id=getattr(user, "id", None),
            action=action,
            entity=entity,
            entity_uid=str(entity_uid or ""),
            detail=detail[:255],
            source_ip=_client_ip(request),
        )
    except Exception:  # noqa: BLE001 - auditing must not break clinical work
        pass


def _client_ip(request):
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None
