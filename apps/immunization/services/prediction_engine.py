"""Heuristic dropout probability (paper Eq. 1).

    P_d = 0.4 r_ov + 0.3 r_delay + 0.3 r_miss

    r_ov    = overdue_vaccines / total_vaccines
    r_delay = min(days_since_last_due / 30, 1)
    r_miss  = 1 if overdue_vaccines > 0 else 0

The arithmetic lives in `dropout_probability_from_features` so that the
per-child path, the set-based cohort path, and the offline analysis scripts all
evaluate one implementation of the published equation rather than three.

P_d is a follow-up ranking, not a calibrated probability: the sensitivity
analysis in the paper shows the induced ordering is robust to the weights while
the absolute values and tier cut-points are not.
"""
from django.utils import timezone

from apps.immunization.services.detection import get_overdue_vaccines

W_OVERDUE = 0.4
W_DELAY = 0.3
W_MISS = 0.3
DELAY_SATURATION_DAYS = 30.0


def dropout_probability_from_features(
    *, overdue_count: int, total_vaccines: int, days_since_last_due: int
) -> float:
    r_ov = overdue_count / total_vaccines if total_vaccines > 0 else 0.0
    r_delay = min(days_since_last_due / DELAY_SATURATION_DAYS, 1.0)
    r_miss = 1.0 if overdue_count > 0 else 0.0

    probability = W_OVERDUE * r_ov + W_DELAY * r_delay + W_MISS * r_miss
    return round(min(max(probability, 0.0), 1.0), 4)


def calculate_dropout_probability(child) -> float:
    """Evaluate Eq. (1) for one child against the database."""
    today = timezone.now().date()
    overdue = get_overdue_vaccines().filter(child=child)
    overdue_count = overdue.count()

    last_due = overdue.order_by("-due_date").values_list("due_date", flat=True).first()
    days_since_last_due = (today - last_due).days if last_due is not None else 0

    return dropout_probability_from_features(
        overdue_count=overdue_count,
        total_vaccines=child.vaccines.count(),
        days_since_last_due=days_since_last_due,
    )
