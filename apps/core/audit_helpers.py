"""Thin wrapper so views call one function and the audit model stays swappable."""
from apps.core.models import AuditLog, record


def log(request, action: str, entity: str, entity_uid="", detail: str = ""):
    return record(request, getattr(AuditLog.Action, action, action), entity, entity_uid, detail)
