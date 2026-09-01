"""Predictive validity of the EIMS heuristic dropout model.

The sensitivity analysis answers "are the weights defensible?". It does not
answer "does the score predict anything?", which is the separate question
Reviewer 3 raised. Stability under reweighting is not accuracy: a model can be
perfectly stable and perfectly useless.

This script supplies the missing half. It builds a cohort with a *known
forward outcome*, so discrimination can actually be computed:

  1. Each child has a latent adherence propensity p.
  2. History up to an observation date t0 is simulated from p, and the paper's
     three predictors are computed from that history exactly as the deployed
     code computes them.
  3. The outcome is then simulated forward from the same p: does the child
     fail to receive the next scheduled dose within a 30-day grace window?
  4. Features from step 2 are scored against the outcome from step 3.

Comparators, all on identical data:

  heuristic         paper Eq. (1) with the published weights
  equal weights     the canonical improper linear model
  logistic (fitted) an upper bound -- a model with access to the labels the
                    heuristic by construction does not have
  overdue only      the simplest single-feature rule a facility already has
  age-normalised    the refinement the paper identifies but does not deploy
  random            the floor

Reported: ROC AUC, average precision, Brier score, calibration slope, and
precision/recall at a 10% follow-up budget -- the last being the number that
actually governs a health worker's day.

Because national dropout rates vary and no single value should be assumed, the
whole analysis is swept across a range of DPT1-DPT3 dropout rates rather than
pinned to one. Reproducible from a fixed seed; no external data.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold

SEED = 20260901
TOTAL_VACCINES = 15
GRACE_DAYS = 30

# EPI offsets in days for the 15 scheduled doses (paper Table I)
EPI_OFFSETS = np.array([0, 0,
                        42, 42, 42, 42,
                        70, 70, 70, 70,
                        98, 98, 98, 98,
                        270])


# --------------------------------------------------------------------------
# cohort with a known forward outcome
# --------------------------------------------------------------------------
def build_cohort(n, target_dropout, rng):
    """Simulate history to t0, predictors at t0, and the next-dose outcome.

    `target_dropout` sets the mean probability that a due dose is missed, so
    the cohort can be tuned to a given national DPT1-DPT3 dropout rate. The
    latent propensity is Beta-distributed around it, giving heterogeneity
    without assuming every family behaves the same way.
    """
    age_days = rng.integers(0, 730, size=n)

    # Beta with mean = target_dropout and a fixed concentration.
    concentration = 6.0
    a = max(target_dropout * concentration, 0.05)
    b = max((1.0 - target_dropout) * concentration, 0.05)
    miss_propensity = rng.beta(a, b, size=n)

    due = age_days[:, None] >= EPI_OFFSETS[None, :]
    missed = (rng.random((n, TOTAL_VACCINES)) < miss_propensity[:, None]) & due
    overdue_mask = missed

    overdue = overdue_mask.sum(axis=1)
    doses_due = due.sum(axis=1)

    offsets = np.broadcast_to(EPI_OFFSETS, (n, TOTAL_VACCINES))
    last_due_off = np.where(overdue_mask, offsets, -1).max(axis=1)
    days_since_last_due = np.where(last_due_off >= 0, age_days - last_due_off, 0)

    # --- predictors, exactly as deployed (paper Eq. 2-4) -------------------
    r_ov = overdue / TOTAL_VACCINES
    r_delay = np.minimum(days_since_last_due / 30.0, 1.0)
    r_miss = (overdue > 0).astype(float)
    r_ov_agenorm = np.divide(overdue, doses_due,
                             out=np.zeros(n, dtype=float), where=doses_due > 0)

    # --- forward outcome ---------------------------------------------------
    # The next dose is the first one not yet due at t0. A child with a dose
    # still ahead of them can dropout; a child past the whole schedule is
    # excluded, since there is nothing left to miss.
    next_idx = np.argmax(~due, axis=1)
    has_next = (~due).any(axis=1)
    outcome = (rng.random(n) < miss_propensity).astype(int)

    keep = has_next
    return {
        "r_ov": r_ov[keep],
        "r_delay": r_delay[keep],
        "r_miss": r_miss[keep],
        "r_ov_agenorm": r_ov_agenorm[keep],
        "age_days": age_days[keep],
        "doses_due": doses_due[keep],
        "overdue": overdue[keep],
        "y": outcome[keep],
    }


# --------------------------------------------------------------------------
# scorers
# --------------------------------------------------------------------------
def heuristic(f, w=(0.4, 0.3, 0.3), agenorm=False):
    r_ov = f["r_ov_agenorm"] if agenorm else f["r_ov"]
    return w[0] * r_ov + w[1] * f["r_delay"] + w[2] * f["r_miss"]


def fitted_logistic(f, y, rng, agenorm=False):
    """Cross-validated out-of-fold predictions from a model that has seen the
    labels. This is the ceiling the heuristic is being measured against, not a
    deployable alternative -- at first deployment these labels do not exist."""
    r_ov = f["r_ov_agenorm"] if agenorm else f["r_ov"]
    X = np.column_stack([r_ov, f["r_delay"], f["r_miss"]])
    oof = np.zeros(len(y))
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    for train, test in folds.split(X, y):
        model = LogisticRegression(max_iter=2000)
        model.fit(X[train], y[train])
        oof[test] = model.predict_proba(X[test])[:, 1]
    full = LogisticRegression(max_iter=2000).fit(X, y)
    return oof, full.coef_[0], full.intercept_[0]


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def precision_at_budget(scores, y, budget=0.10):
    """What a fixed follow-up capacity actually buys.

    A health worker can chase a fraction of the cohort. Of the children they
    chase, what share truly drop out (precision), and of all children who drop
    out, what share do they reach (recall)?
    """
    k = max(1, int(budget * len(y)))
    top = np.argsort(-scores)[:k]
    captured = y[top].sum()
    return captured / k, captured / max(y.sum(), 1)


def calibration_slope(scores, y):
    """Regress the outcome on the logit of the score. A well-calibrated score
    gives a slope near 1; far from 1 means the values are not probabilities
    even if the ordering is sound."""
    p = np.clip(scores, 1e-6, 1 - 1e-6)
    logit = np.log(p / (1 - p)).reshape(-1, 1)
    model = LogisticRegression(max_iter=2000).fit(logit, y)
    return float(model.coef_[0][0])


def evaluate(name, scores, y, base_rate):
    prec, rec = precision_at_budget(scores, y)
    return {
        "model": name,
        "auc": float(roc_auc_score(y, scores)),
        "ap": float(average_precision_score(y, scores)),
        "ap_lift": float(average_precision_score(y, scores) / base_rate),
        "brier": float(brier_score_loss(y, np.clip(scores, 0, 1))),
        "cal_slope": calibration_slope(scores, y),
        "precision_at_10": float(prec),
        "recall_at_10": float(rec),
    }


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def run_one(n, target_dropout, verbose=True):
    rng = np.random.default_rng(SEED)
    f = build_cohort(n, target_dropout, rng)
    y = f["y"]
    base_rate = y.mean()

    rows = []
    rows.append(evaluate("Heuristic, Eq. (1) weights", heuristic(f), y, base_rate))
    rows.append(evaluate("Equal weights (1/3 each)",
                         heuristic(f, (1 / 3, 1 / 3, 1 / 3)), y, base_rate))
    rows.append(evaluate("Heuristic, age-normalised r_ov",
                         heuristic(f, agenorm=True), y, base_rate))
    rows.append(evaluate("Overdue count only", f["r_ov"], y, base_rate))
    rows.append(evaluate("Delay only", f["r_delay"], y, base_rate))
    rows.append(evaluate("Any-miss flag only", f["r_miss"], y, base_rate))

    oof, coefs, intercept = fitted_logistic(f, y, rng)
    rows.append(evaluate("Logistic regression (fitted, CV)", oof, y, base_rate))

    rows.append(evaluate("Random", rng.random(len(y)), y, base_rate))

    if verbose:
        print("\n" + "=" * 92)
        print("Cohort n=%d after excluding children with no remaining dose; "
              "outcome base rate %.3f" % (len(y), base_rate))
        print("Target DPT1-DPT3 dropout rate: %.0f%%" % (target_dropout * 100))
        print("=" * 92)
        print("%-34s%7s%8s%8s%8s%9s%9s%8s"
              % ("Model", "AUC", "AP", "lift", "Brier", "cal.slope", "prec@10%", "rec@10%"))
        for r in rows:
            print("%-34s%7.3f%8.3f%8.2f%8.3f%9.2f%9.3f%8.3f"
                  % (r["model"], r["auc"], r["ap"], r["ap_lift"], r["brier"],
                     r["cal_slope"], r["precision_at_10"], r["recall_at_10"]))
        print("\nFitted logistic coefficients (r_ov, r_delay, r_miss): "
              "%.3f, %.3f, %.3f   intercept %.3f" % (*coefs, intercept))
        print("Normalised to sum 1: %.3f, %.3f, %.3f   "
              "(paper weights: 0.400, 0.300, 0.300)"
              % tuple(np.abs(coefs) / np.abs(coefs).sum()))

    return {
        "target_dropout": target_dropout,
        "n": int(len(y)),
        "base_rate": float(base_rate),
        "rows": rows,
        "fitted_coefs": [float(c) for c in coefs],
        "fitted_intercept": float(intercept),
        "fitted_normalised": [float(c) for c in np.abs(coefs) / np.abs(coefs).sum()],
    }


def sweep(n, rates):
    print("\n" + "=" * 92)
    print("SWEEP over plausible dropout rates -- no single national rate is assumed")
    print("=" * 92)
    print("%-12s%10s%10s%10s%10s%12s"
          % ("dropout", "base rate", "AUC heur", "AUC fitted", "gap", "prec@10%"))
    results = []
    for rate in rates:
        res = run_one(n, rate, verbose=False)
        heur = next(r for r in res["rows"] if r["model"].startswith("Heuristic, Eq"))
        fit = next(r for r in res["rows"] if r["model"].startswith("Logistic"))
        print("%-12.0f%%%9.3f%10.3f%10.3f%10.3f%12.3f"
              % (rate * 100, res["base_rate"], heur["auc"], fit["auc"],
                 fit["auc"] - heur["auc"], heur["precision_at_10"]))
        results.append(res)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50000)
    ap.add_argument("--central", type=float, default=0.15,
                    help="central DPT1-DPT3 dropout rate for the detailed table")
    ap.add_argument("--out", default="predictive_validity.json")
    args = ap.parse_args()

    print("EIMS predictive-validity analysis (seed %d)" % SEED)
    central = run_one(args.n, args.central)
    rates = [0.05, 0.10, 0.15, 0.20, 0.30]
    swept = sweep(args.n, rates)

    with open(args.out, "w") as fh:
        json.dump({"central": central, "sweep": swept}, fh, indent=2)
    print("\nWrote %s" % args.out)


if __name__ == "__main__":
    main()
