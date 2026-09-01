"""Caregiver-facing artifact endpoints.

The immunization card and the .ics schedule are addressed by the child's UUID.
A bare UUID in a URL is a capability: anyone who obtains the link -- from a
forwarded email, a shared handset, a browser history on a shared workstation --
holds permanent, unauthenticated access to a named child's clinical record.

Access is therefore either an authenticated staff session, or a signed link
that names the child and expires (EIMS_ARTIFACT_LINK_MAX_AGE, 14 days by
default). Both paths are audited.
"""
from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_exempt

from apps.core import audit_helpers as audit
from apps.core.auth import user_is_staff_member
from apps.immunization.models import Child
from apps.immunization.services.card_service import build_child_card_assets

SIGNING_SALT = "eims.artifact.link"


def sign_artifact_link(child, kind: str) -> str:
    """Return a relative URL carrying a signed, expiring token for `child`."""
    token = signing.dumps({"uid": str(child.unique_id), "kind": kind}, salt=SIGNING_SALT)
    name = "immunization_card_pdf" if kind == "pdf" else "immunization_schedule_ics"
    return f"{reverse(name, kwargs={'child_uuid': child.unique_id})}?t={token}"


def _authorise(request, child_uuid, kind: str):
    """Grant access to an authenticated staff member or a valid signed token."""
    user = getattr(request, "user", None)
    if user_is_staff_member(user):
        return "session"

    token = request.GET.get("t", "")
    if not token:
        audit.log(request, "DENIED", "Child", child_uuid, detail=f"{kind}: no token")
        raise Http404
    try:
        data = signing.loads(
            token, salt=SIGNING_SALT,
            max_age=getattr(settings, "EIMS_ARTIFACT_LINK_MAX_AGE", 14 * 24 * 3600),
        )
    except signing.BadSignature:
        audit.log(request, "DENIED", "Child", child_uuid, detail=f"{kind}: bad or expired token")
        raise Http404
    if data.get("uid") != str(child_uuid) or data.get("kind") != kind:
        audit.log(request, "DENIED", "Child", child_uuid, detail=f"{kind}: token mismatch")
        raise Http404
    return "link"


@xframe_options_exempt
def immunization_card_pdf(request, child_uuid):
    via = _authorise(request, child_uuid, "pdf")
    child = get_object_or_404(Child.objects.select_related("caregiver"), unique_id=child_uuid)
    audit.log(request, "EXPORT", "Child", child.unique_id, detail=f"immunization card via {via}")

    disposition = "attachment" if request.GET.get("download") == "1" else "inline"
    if hasattr(child, "immunization_card") and child.immunization_card.pdf:
        with child.immunization_card.pdf.open("rb") as fh:
            payload = fh.read()
    else:
        payload, _, _ = build_child_card_assets(child)

    resp = HttpResponse(payload, content_type="application/pdf")
    resp["Content-Disposition"] = (
        f'{disposition}; filename="immunization-card-{child.unique_id}.pdf"'
    )
    resp["Cache-Control"] = "private, no-store"
    return resp


def immunization_schedule_ics(request, child_uuid):
    via = _authorise(request, child_uuid, "ics")
    child = get_object_or_404(Child.objects.select_related("caregiver"), unique_id=child_uuid)
    audit.log(request, "EXPORT", "Child", child.unique_id, detail=f"ics schedule via {via}")

    _, ics_bytes, _ = build_child_card_assets(child)
    resp = HttpResponse(ics_bytes, content_type="text/calendar; charset=utf-8")
    resp["Content-Disposition"] = (
        f'attachment; filename="immunization-schedule-{child.unique_id}.ics"'
    )
    resp["Cache-Control"] = "private, no-store"
    return resp
