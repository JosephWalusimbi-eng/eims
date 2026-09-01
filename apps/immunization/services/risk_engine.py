"""Risk classification and dropout scoring.

Two entry points with identical semantics and very different cost:

  calculate_risk(child)  one child, several queries. Correct for the doctor
                         workflow, where a single record has just changed.

  score_cohort()         the whole cohort in a fixed number of queries. The
                         per-child path issues O(n) round-trips when run over a
                         district, which is the binding constraint at scale --
                         not the arithmetic, which is O(1) per child. The
                         paper reports both, because the gap between them is
                         the scalability result.

Both produce the tiers of Table III and the probability of Eq. (1).
"""
from __future__ import annotations

from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.immunization.models import Child, RiskScore, VaccineSchedule
from apps.immunization.services.detection import get_overdue_vaccines
from apps.immunization.services.prediction_engine import (
    calculate_dropout_probability,
    dropout_probability_from_features,
)

SCORE_LOW = 1
SCORE_MEDIUM = 2
SCORE_HIGH = 3


def classify(overdue_count: int):
    """Table III: 0 -> LOW, 1 -> MEDIUM, >=2 -> HIGH."""
    if overdue_count == 0:
        return RiskScore.Level.LOW, SCORE_LOW
    if overdue_count == 1:
        return RiskScore.Level.MEDIUM, SCORE_MEDIUM
    return RiskScore.Level.HIGH, SCORE_HIGH


def calculate_risk(child):
    """Score one child. A new RiskScore row is written only when the tier moves,
    so the table records transitions rather than one row per pass."""
    overdue_count = get_overdue_vaccines().filter(child=child).count()
    level, score = classify(overdue_count)

    latest = child.risk_scores.order_by("-calculated_at").first()
    if latest is not None and latest.level == level:
        return latest

    return RiskScore.objects.create(
        child=child,
        score=score,
        level=level,
        dropout_probability=calculate_dropout_probability(child),
    )


def score_cohort(batch_size: int = 5000) -> dict:
    """Score every child in a fixed number of queries.

    Four statements regardless of cohort size: one aggregate over schedules,
    one over the latest tier per child, one bulk insert, and the transaction.
    """
    today = timezone.now().date()

    # One pass over the schedule table yields every feature Eq. (1) needs.
    rows = (
        Child.objects.annotate(
            total=Count("vaccines", distinct=True),
            overdue=Count(
                "vaccines",
                filter=Q(vaccines__due_date__lt=today, vaccines__administered=False),
                distinct=True,
            ),
            last_overdue_due=Max(
                "vaccines__due_date",
                filter=Q(vaccines__due_date__lt=today, vaccines__administered=False),
            ),
        )
        .values("id", "total", "overdue", "last_overdue_due")
        .order_by("id")
    )

    # Current tier per child, so unchanged tiers are skipped exactly as the
    # per-child path skips them.
    current = dict(
        RiskScore.objects.order_by("child_id", "-calculated_at")
        .distinct("child_id")
        .values_list("child_id", "level")
    )

    pending, written, unchanged = [], 0, 0
    for row in rows.iterator(chunk_size=batch_size):
        level, score = classify(row["overdue"])
        if current.get(row["id"]) == level:
            unchanged += 1
            continue

        days_since = (today - row["last_overdue_due"]).days if row["last_overdue_due"] else 0
        probability = dropout_probability_from_features(
            overdue_count=row["overdue"],
            total_vaccines=row["total"],
            days_since_last_due=days_since,
        )
        pending.append(
            RiskScore(child_id=row["id"], score=score, level=level,
                      dropout_probability=probability)
        )
        if len(pending) >= batch_size:
            RiskScore.objects.bulk_create(pending, batch_size=batch_size)
            written += len(pending)
            pending = []

    if pending:
        RiskScore.objects.bulk_create(pending, batch_size=batch_size)
        written += len(pending)

    return {"scored": written + unchanged, "written": written, "unchanged": unchanged}
