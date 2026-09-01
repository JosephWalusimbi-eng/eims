"""
Sensitivity analysis of the EIMS heuristic dropout-probability model.

Model under test (paper Eq. 1):
    P_d = w_ov * r_ov + w_delay * r_delay + w_miss * r_miss
    baseline weights w = (0.4, 0.3, 0.3)

    r_ov    = overdue_vaccines / total_vaccines          (total_vaccines = 15)
    r_delay = min(days_since_last_due / 30, 1)
    r_miss  = 1 if overdue_vaccines > 0 else 0

Reviewers asked whether the fixed weights (0.4, 0.3, 0.3) are defensible.

Operationally the weights matter only insofar as they change the ORDER in which
health workers are asked to follow children up. We therefore measure stability
of the induced ranking (and of the triage tiers) under wide perturbation of the
weight vector, on a large synthetic cohort.

Reproducible: fixed seed, no external data.
"""
import numpy as np
from scipy.stats import spearmanr

RNG = np.random.default_rng(20260831)
TOTAL_VACCINES = 15
N_CHILDREN = 10_000
N_WEIGHTS = 5_000

# EPI offsets in days for the 15 scheduled doses (paper Table I)
EPI_OFFSETS = np.array([0, 0,                      # BCG, OPV0
                        42, 42, 42, 42,            # Penta1, OPV1, PCV1, Rota1
                        70, 70, 70, 70,            # Penta2, OPV2, PCV2, Rota2
                        98, 98, 98, 98,            # Penta3, OPV3, PCV3, IPV
                        270])                      # Measles


def synth_cohort(n=N_CHILDREN):
    """Synthetic cohort of children at varying ages with heterogeneous adherence.

    Each child has a latent adherence propensity p ~ Beta(2,2) (wide spread, so
    no particular population dropout rate is assumed). A dose already due is
    recorded as administered with probability p, otherwise it is overdue.
    """
    age_days = RNG.integers(0, 730, size=n)                 # 0-24 months
    propensity = RNG.beta(2.0, 2.0, size=n)

    due = age_days[:, None] >= EPI_OFFSETS[None, :]         # (n, 15) dose is due
    administered = (RNG.random((n, TOTAL_VACCINES)) < propensity[:, None]) & due
    overdue_mask = due & ~administered

    overdue = overdue_mask.sum(axis=1)
    doses_due = due.sum(axis=1)

    # days since the most recent due date that is still unadministered
    off = np.broadcast_to(EPI_OFFSETS, (n, TOTAL_VACCINES))
    last_due_off = np.where(overdue_mask, off, -1).max(axis=1)
    days_since_last_due = np.where(last_due_off >= 0, age_days - last_due_off, 0)

    r_ov = overdue / TOTAL_VACCINES
    r_delay = np.minimum(days_since_last_due / 30.0, 1.0)
    r_miss = (overdue > 0).astype(float)

    # variant discussed in the revision: normalise by doses due to date rather
    # than by the full 15-dose schedule (removes the age confound)
    r_ov_agenorm = np.divide(overdue, doses_due, out=np.zeros(n, dtype=float),
                             where=doses_due > 0)

    return dict(r_ov=r_ov, r_delay=r_delay, r_miss=r_miss,
                r_ov_agenorm=r_ov_agenorm, overdue=overdue,
                doses_due=doses_due, age_days=age_days)


def pd_score(feats, w, agenorm=False):
    rov = feats["r_ov_agenorm"] if agenorm else feats["r_ov"]
    return w[0] * rov + w[1] * feats["r_delay"] + w[2] * feats["r_miss"]


def tiers(p, lo=1.0 / 3, hi=2.0 / 3):
    return np.digitize(p, [lo, hi])


def perturbation_study(feats, agenorm=False, dirichlet_alpha=None, label=""):
    base_w = np.array([0.4, 0.3, 0.3])
    base_p = pd_score(feats, base_w, agenorm)
    base_t = tiers(base_p)
    k = max(1, int(0.10 * len(base_p)))
    base_top = set(np.argsort(-base_p)[:k].tolist())

    if dirichlet_alpha is None:                       # uniform over the simplex
        W = RNG.dirichlet(np.ones(3), size=N_WEIGHTS)
    else:
        W = RNG.dirichlet(dirichlet_alpha, size=N_WEIGHTS)

    rho, tier_agree, top_overlap = [], [], []
    for w in W:
        p = pd_score(feats, w, agenorm)
        rho.append(spearmanr(base_p, p).statistic)
        tier_agree.append((tiers(p) == base_t).mean())
        top_overlap.append(len(base_top & set(np.argsort(-p)[:k].tolist())) / k)

    rho = np.array(rho)
    tier_agree = np.array(tier_agree)
    top_overlap = np.array(top_overlap)
    print("\n--- %s ---" % label)
    print("  Spearman rho vs baseline : mean %.4f  min %.4f  p5 %.4f"
          % (rho.mean(), rho.min(), np.percentile(rho, 5)))
    print("  Tier agreement (3 tiers) : mean %.2f%%  min %.2f%%"
          % (tier_agree.mean() * 100, tier_agree.min() * 100))
    print("  Top-10%% list overlap     : mean %.2f%%  min %.2f%%"
          % (top_overlap.mean() * 100, top_overlap.min() * 100))
    return dict(rho=rho, tier=tier_agree, top=top_overlap)


def named_weight_comparison(feats, agenorm=False):
    base_w = np.array([0.4, 0.3, 0.3])
    base_p = pd_score(feats, base_w, agenorm)
    k = max(1, int(0.10 * len(base_p)))
    base_top = set(np.argsort(-base_p)[:k].tolist())
    schemes = [
        ("Baseline (0.4,0.3,0.3)", (0.4, 0.3, 0.3)),
        ("Unit / equal (1/3 each)", (1.0 / 3, 1.0 / 3, 1.0 / 3)),
        ("Overdue-dominant (0.6,0.2,0.2)", (0.6, 0.2, 0.2)),
        ("Delay-dominant (0.2,0.6,0.2)", (0.2, 0.6, 0.2)),
        ("Miss-dominant (0.2,0.2,0.6)", (0.2, 0.2, 0.6)),
        ("Overdue-only (1,0,0)", (1.0, 0.0, 0.0)),
        ("Delay-only (0,1,0)", (0.0, 1.0, 0.0)),
    ]
    kind = "age-normalised" if agenorm else "paper Eq. 1"
    print("\n--- Named weighting schemes vs baseline (%s) ---" % kind)
    print("%-32s%8s%9s%9s%9s" % ("Scheme", "rho", "tier%", "top10%", "meanPd"))
    for name, w in schemes:
        p = pd_score(feats, np.array(w), agenorm)
        r = spearmanr(base_p, p).statistic
        t = (tiers(p) == tiers(base_p)).mean() * 100
        o = len(base_top & set(np.argsort(-p)[:k].tolist())) / k * 100
        print("%-32s%8.4f%9.2f%9.2f%9.4f" % (name, r, t, o, p.mean()))


def structure_checks(feats):
    print("\n--- Structural properties of Eq. (1) ---")
    r_ov, r_miss = feats["r_ov"], feats["r_miss"]
    print("  r_miss is a deterministic step of r_ov: %s"
          % np.array_equal(r_miss, (r_ov > 0).astype(float)))
    print("  corr(r_ov, r_miss)      = %.4f" % np.corrcoef(r_ov, r_miss)[0, 1])
    print("  corr(r_ov, r_delay)     = %.4f" % np.corrcoef(r_ov, feats["r_delay"])[0, 1])
    print("  corr(r_ov, age)         = %.4f" % np.corrcoef(r_ov, feats["age_days"])[0, 1])
    print("  corr(r_ov_agenorm, age) = %.4f"
          % np.corrcoef(feats["r_ov_agenorm"], feats["age_days"])[0, 1])

    base_w = np.array([0.4, 0.3, 0.3])
    comps = np.vstack([base_w[0] * r_ov, base_w[1] * feats["r_delay"],
                       base_w[2] * r_miss])
    p = comps.sum(axis=0)
    print("\n  Variance share of P_d attributable to each weighted term:")
    for nm, c in zip(("w_ov*r_ov", "w_delay*r_delay", "w_miss*r_miss"), comps):
        print("    %-18s %6.2f%%" % (nm, np.cov(c, p)[0, 1] / np.var(p) * 100))

    nz = r_ov > 0
    print("\n  Discontinuity at the first missed dose: any child with >=1 overdue"
          " dose scores at least %.4f" % (0.3 + 0.4 * (1.0 / 15)))
    print("  mean P_d | no overdue  = %.4f" % p[~nz].mean())
    print("  mean P_d | any overdue = %.4f" % p[nz].mean())
    print("  share of cohort with >=1 overdue dose = %.2f%%" % (nz.mean() * 100))
    print("  max attainable P_d (paper Eq. 1)      = %.4f" % p.max())


if __name__ == "__main__":
    feats = synth_cohort()
    print("Synthetic cohort: n=%d, %d random weight vectors" % (N_CHILDREN, N_WEIGHTS))
    print("Mean overdue doses/child = %.2f of %d; mean doses due to date = %.2f"
          % (feats["overdue"].mean(), TOTAL_VACCINES, feats["doses_due"].mean()))

    structure_checks(feats)
    perturbation_study(feats, agenorm=False,
                       label="Eq.(1) weights, UNIFORM over simplex (worst case)")
    perturbation_study(feats, agenorm=False,
                       dirichlet_alpha=np.array([16.0, 12.0, 12.0]),
                       label="Eq.(1) weights, Dirichlet concentrated near (0.4,0.3,0.3)")
    named_weight_comparison(feats, agenorm=False)
    perturbation_study(feats, agenorm=True,
                       label="Age-normalised r_ov, UNIFORM over simplex")
    named_weight_comparison(feats, agenorm=True)
