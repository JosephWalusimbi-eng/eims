"""Single definition of "at risk", shared by the dashboard tiles and the lists
they link to.

A tile and the list behind it must agree to the unit; if each computed its own
count they would drift (the previous tile counted any child who had *ever* been
scored HIGH, while the priority figure used the latest score). Every figure now
derives from one rule: a child's risk is its **latest** RiskScore.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, F, Min, OuterRef, Q, Subquery
from django.utils import timezone

from apps.immunization.models import Child, RiskScore

FILTERS = {
    "priority": "Priority follow-up (top 10% by dropout score)",
    "high": "High-risk children (two or more missed doses)",
    "overdue": "Children with overdue doses",
    "due_week": "Doses due in the next 7 days",
}


def latest_scores():
    """RiskScore rows that are each child's most recent."""
    return RiskScore.objects.filter(
        id=Subquery(
            RiskScore.objects.filter(child=OuterRef("child"))
            .order_by("-calculated_at", "-id")
            .values("id")[:1]
        )
    )


def high_risk_child_ids():
    """Subquery of child ids whose latest tier is HIGH."""
    return latest_scores().filter(level=RiskScore.Level.HIGH).values("child_id")


def priority_ids() -> tuple[set, float]:
    """Top-10% follow-up budget by dropout probability (deterministic ties)."""
    scored = list(
        latest_scores().order_by("-dropout_probability", "child_id")
        .values_list("child_id", "dropout_probability")
    )
    budget = max(1, int(round(0.10 * len(scored)))) if scored else 0
    ids = {cid for cid, _ in scored[:budget]}
    threshold = scored[budget - 1][1] if budget else 0.0
    return ids, threshold


def children_for(kind: str, district: str | None = None):
    """Children behind a dashboard tile, annotated for the follow-up list.

    Ordered most-urgent first (dropout probability, then oldest overdue dose).
    """
    today = timezone.now().date()
    overdue_q = Q(vaccines__due_date__lt=today, vaccines__administered=False)
    due_week_q = Q(
        vaccines__due_date__gte=today,
        vaccines__due_date__lte=today + timedelta(days=7),
        vaccines__administered=False,
    )
    latest = latest_scores().filter(child=OuterRef("pk"))

    qs = (
        Child.objects.select_related("caregiver")
        .annotate(
            overdue_doses=Count("vaccines", filter=overdue_q, distinct=True),
            due_week_doses=Count("vaccines", filter=due_week_q, distinct=True),
            oldest_overdue=Min("vaccines__due_date", filter=overdue_q),
            risk_level=Subquery(latest.values("level")[:1]),
            dropout_p=Subquery(latest.values("dropout_probability")[:1]),
        )
    )

    if kind == "priority":
        ids, _ = priority_ids()
        qs = qs.filter(pk__in=ids)
    elif kind == "high":
        qs = qs.filter(pk__in=high_risk_child_ids())
    elif kind == "overdue":
        qs = qs.filter(overdue_doses__gt=0)
    elif kind == "due_week":
        qs = qs.filter(due_week_doses__gt=0)
    else:
        raise ValueError(f"unknown at-risk filter: {kind!r}")

    if district:
        qs = qs.filter(caregiver__district="" if district == "Unknown" else district)

    return qs.order_by(F("dropout_p").desc(nulls_last=True), "oldest_overdue", "pk")
