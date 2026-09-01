"""EIMS end-to-end performance benchmark.

Runs against the deployed Django/PostgreSQL stack -- not against a NumPy
re-implementation of the algorithms. Every number the paper reports in the
system-performance table comes from this script.

The harness builds its own throwaway database (Django's test database
machinery), seeds cohorts of 1k / 10k / 100k children, and measures:

  * registration        the full doctor workflow: schedule generation, PDF
                        card, .ics events, calendar rows
  * overdue sweep       detection over the whole schedule table
  * risk + dropout      both the per-child path and the set-based cohort path,
                        because the gap between them is the scalability result
  * dashboard           the monitoring aggregation as the view issues it
  * sync flush          offline outbox drain, per envelope, both policies
  * notification        local enqueue, and dispatch to the configured backend

Usage:
    python analysis/benchmark.py                 # 1k and 10k
    python analysis/benchmark.py --full          # adds 100k (slow)
    python analysis/benchmark.py --sizes 1000    # explicit

Output includes a LaTeX table body ready to paste into the manuscript.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

# --- locate the Django project -------------------------------------------
def _find_project_dir() -> Path:
    """Locate the Django project root (the directory holding manage.py).

    Walks upward from this file so the script works whether it is run from
    inside the repository, from the manuscript directory, or from anywhere
    else. EIMS_PROJECT_DIR overrides the search.
    """
    override = os.environ.get("EIMS_PROJECT_DIR")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "manage.py").exists():
            return candidate
        if (candidate / "eims" / "manage.py").exists():
            return candidate / "eims"
    raise SystemExit(
        "Cannot locate the EIMS Django project (no manage.py found above %s). "
        "Set EIMS_PROJECT_DIR to the directory containing manage.py." % here
    )


PROJECT_DIR = _find_project_dir()
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.chdir(PROJECT_DIR)

import django  # noqa: E402

django.setup()

from django.db import connection, transaction  # noqa: E402
from django.db.models import signals  # noqa: E402
from django.test.utils import setup_databases, teardown_databases  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.core.crypto import blind_index  # noqa: E402
from apps.immunization.models import (  # noqa: E402
    Caregiver, Child, NotificationLog, RiskScore, VaccineSchedule,
)
from apps.immunization.services import notification_service as notify  # noqa: E402
from apps.immunization.services.detection import get_overdue_vaccines  # noqa: E402
from apps.immunization.services.risk_engine import calculate_risk, score_cohort  # noqa: E402
from apps.immunization.services.scheduler import UGANDA_EPI_SCHEDULE  # noqa: E402
from apps.immunization import signals as imm_signals  # noqa: E402
from apps.sync import services as sync  # noqa: E402
from apps.sync.models import SyncEnvelope  # noqa: E402

REPEATS = 7
DISTRICTS = ["Gulu", "Mbarara", "Jinja", "Wakiso", "Kampala", "Soroti"]
DOSES_PER_CHILD = sum(len(names) for _, names in UGANDA_EPI_SCHEDULE)


# --------------------------------------------------------------------------
# timing helpers
# --------------------------------------------------------------------------
def timeit(fn, repeats=REPEATS):
    """Median and p95 wall-clock milliseconds, after one discarded warm-up."""
    fn()
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    return statistics.median(samples), samples[min(len(samples) - 1, int(0.95 * len(samples)))]


def fmt(ms):
    if ms >= 10000:
        return "%.1f s" % (ms / 1000.0)
    if ms >= 100:
        return "%.0f" % ms
    if ms >= 1:
        return "%.1f" % ms
    return "%.2f" % ms


# --------------------------------------------------------------------------
# cohort seeding
# --------------------------------------------------------------------------
def seed(n_children: int, seed_value: int = 20260901):
    """Bulk-load a cohort. Signals are disconnected: registration cost is
    measured separately, one child at a time, with the signals live."""
    import random

    rng = random.Random(seed_value)
    signals.post_save.disconnect(imm_signals.child_post_save, sender=Child)
    try:
        today = timezone.now().date()
        caregivers = []
        for i in range(n_children):
            phone = "+2567%08d" % i
            caregivers.append(
                Caregiver(
                    full_name="Caregiver %d" % i,
                    phone_number=phone,
                    phone_index=blind_index(phone),
                    email="c%d@example.org" % i,
                    district=DISTRICTS[i % len(DISTRICTS)],
                    village="Village %d" % (i % 40),
                    preferred_language=Caregiver.PreferredLanguage.ENGLISH,
                )
            )
        Caregiver.objects.bulk_create(caregivers, batch_size=2000)
        caregiver_ids = list(Caregiver.objects.order_by("id").values_list("id", flat=True))

        children = [
            Child(
                caregiver_id=cid,
                full_name="Child %d" % i,
                date_of_birth=today - timedelta(days=rng.randint(0, 730)),
                gender=Child.Gender.MALE if i % 2 else Child.Gender.FEMALE,
            )
            for i, cid in enumerate(caregiver_ids)
        ]
        Child.objects.bulk_create(children, batch_size=2000)

        rows = []
        for child in Child.objects.order_by("id").only("id", "date_of_birth").iterator(chunk_size=2000):
            for offset, names in UGANDA_EPI_SCHEDULE:
                due = child.date_of_birth + timedelta(days=offset)
                for name in names:
                    administered = due < today and rng.random() < 0.55
                    rows.append(
                        VaccineSchedule(
                            child_id=child.id, vaccine_name=name, due_date=due,
                            administered=administered,
                            administered_date=due if administered else None,
                            sync_uid=uuid.uuid4(),
                        )
                    )
            if len(rows) >= 20000:
                VaccineSchedule.objects.bulk_create(rows, batch_size=5000)
                rows = []
        if rows:
            VaccineSchedule.objects.bulk_create(rows, batch_size=5000)
    finally:
        signals.post_save.connect(imm_signals.child_post_save, sender=Child)


def wipe():
    Caregiver.objects.all().delete()
    NotificationLog.objects.all().delete()
    SyncEnvelope.objects.all().delete()
    RiskScore.objects.all().delete()


# --------------------------------------------------------------------------
# individual measurements
# --------------------------------------------------------------------------
def bench_registration(n=50):
    """Full point-of-care workflow with signals live: schedule + PDF + .ics."""
    from django.core import mail

    mail.outbox = []
    latencies = []
    for i in range(n):
        phone = "+2569%08d" % i
        caregiver = Caregiver.objects.create(
            full_name="Reg Caregiver %d" % i, phone_number=phone,
            email="reg%d@example.org" % i, district="Soroti", village="Bench",
            preferred_language=Caregiver.PreferredLanguage.ENGLISH,
        )
        t0 = time.perf_counter()
        Child.objects.create(
            caregiver=caregiver, full_name="Reg Child %d" % i,
            date_of_birth=date(2026, 1, 1), gender=Child.Gender.MALE,
        )
        latencies.append((time.perf_counter() - t0) * 1000.0)
    latencies.sort()
    return statistics.median(latencies), latencies[int(0.95 * len(latencies))]


def bench_overdue_sweep():
    return timeit(lambda: get_overdue_vaccines().count())


def bench_dashboard():
    """The aggregation the monitoring view issues, including the district
    breakdown that the DISTINCT fix corrected."""
    today = timezone.now().date()

    def run():
        Child.objects.count()
        VaccineSchedule.objects.filter(
            due_date__gte=today, due_date__lte=today + timedelta(days=7), administered=False
        ).count()
        get_overdue_vaccines().count()
        Child.objects.filter(risk_scores__level=RiskScore.Level.HIGH).distinct().count()
        districts = list(
            Caregiver.objects.filter(children__isnull=False).order_by()
            .values_list("district", flat=True).distinct()
        )
        for district in districts:
            Child.objects.filter(caregiver__district=district).count()
            get_overdue_vaccines().filter(child__caregiver__district=district).count()
        return districts

    return timeit(run, repeats=5)


def bench_scoring_per_child(sample=100):
    ids = list(Child.objects.order_by("id").values_list("id", flat=True)[:sample])
    children = list(Child.objects.filter(id__in=ids))
    t0 = time.perf_counter()
    for child in children:
        calculate_risk(child)
    total_ms = (time.perf_counter() - t0) * 1000.0
    return total_ms / max(len(children), 1)


def bench_scoring_cohort():
    RiskScore.objects.all().delete()
    t0 = time.perf_counter()
    result = score_cohort()
    return (time.perf_counter() - t0) * 1000.0, result


def bench_sync(n_envelopes=2000, policy="merge"):
    SyncEnvelope.objects.all().delete()
    uids = list(
        VaccineSchedule.objects.order_by("id").values_list("sync_uid", flat=True)[:n_envelopes]
    )
    now = timezone.now()
    SyncEnvelope.objects.bulk_create(
        [
            SyncEnvelope(
                client_id=uuid.uuid4(), device_id="bench-01", entity="VaccineSchedule",
                entity_uid=str(u),
                payload={"administered": True, "administered_date": str(date(2026, 2, 1))},
                base_version=0, client_updated_at=now + timedelta(seconds=i),
            )
            for i, u in enumerate(uids)
        ],
        batch_size=2000,
    )
    run = sync.flush_queue(device_id="bench-01", policy=policy)
    per_record = run.duration_ms / max(run.envelopes, 1)
    return run.duration_ms, per_record, run


def bench_notifications(n=500):
    NotificationLog.objects.all().delete()
    children = list(Child.objects.select_related("caregiver").order_by("id")[:n])

    t0 = time.perf_counter()
    for child in children:
        notify.enqueue(
            child, channel=NotificationLog.Channel.SMS,
            tier=NotificationLog.Tier.OVERDUE,
            message=notify.compose(NotificationLog.Tier.OVERDUE, child),
            dedupe_key="bench:%d" % child.pk,
        )
    enqueue_ms = (time.perf_counter() - t0) * 1000.0 / max(len(children), 1)

    t0 = time.perf_counter()
    result = notify.dispatch_pending()
    dispatch_ms = (time.perf_counter() - t0) * 1000.0 / max(result["total"], 1)
    return enqueue_ms, dispatch_ms, result


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def run_size(n):
    print("\n" + "=" * 74)
    print("COHORT: %s children (%s schedule rows)" % (f"{n:,}", f"{n * DOSES_PER_CHILD:,}"))
    print("=" * 74)

    wipe()
    t0 = time.perf_counter()
    seed(n)
    print("  seeded in %.1f s" % (time.perf_counter() - t0))

    out = {"n_children": n, "n_schedules": VaccineSchedule.objects.count()}

    med, p95 = bench_registration()
    out["registration_ms"], out["registration_p95_ms"] = med, p95
    print("  registration (schedule+PDF+ics)   median %8s ms  p95 %8s" % (fmt(med), fmt(p95)))

    med, p95 = bench_overdue_sweep()
    out["overdue_sweep_ms"], out["overdue_sweep_p95_ms"] = med, p95
    print("  overdue detection sweep           median %8s ms  p95 %8s" % (fmt(med), fmt(p95)))

    per_child = bench_scoring_per_child()
    out["scoring_per_child_ms"] = per_child
    out["scoring_per_child_projected_s"] = per_child * n / 1000.0
    print("  risk+dropout, per-child path      %8s ms/child  ->  %.1f s for cohort"
          % (fmt(per_child), per_child * n / 1000.0))

    total, result = bench_scoring_cohort()
    out["scoring_cohort_ms"] = total
    out["scoring_cohort_per_child_ms"] = total / max(n, 1)
    out["scoring_speedup"] = (per_child * n) / max(total, 1e-9)
    print("  risk+dropout, set-based path      %8s ms total (%s ms/child, %.0fx faster)"
          % (fmt(total), fmt(total / max(n, 1)), out["scoring_speedup"]))

    med, p95 = bench_dashboard()
    out["dashboard_ms"], out["dashboard_p95_ms"] = med, p95
    print("  dashboard aggregation             median %8s ms  p95 %8s" % (fmt(med), fmt(p95)))

    envelopes = min(2000, n * 2)
    total, per_record, run = bench_sync(envelopes, policy="merge")
    out["sync_total_ms"], out["sync_per_record_ms"] = total, per_record
    out["sync_envelopes"], out["sync_conflicts"] = run.envelopes, run.conflicts
    print("  sync flush (merge)                %8s ms/record  (%d envelopes, %d conflicts)"
          % (fmt(per_record), run.envelopes, run.conflicts))

    _, lww_per_record, lww_run = bench_sync(envelopes, policy="last_write_wins")
    out["sync_lww_per_record_ms"] = lww_per_record
    print("  sync flush (last-write-wins)      %8s ms/record" % fmt(lww_per_record))

    enqueue_ms, dispatch_ms, result = bench_notifications()
    out["notify_enqueue_ms"] = enqueue_ms
    out["notify_dispatch_ms"] = dispatch_ms
    out["notify_sent"] = result["sent"]
    print("  notification enqueue              %8s ms/message" % fmt(enqueue_ms))
    print("  notification dispatch (loopback)  %8s ms/message" % fmt(dispatch_ms))

    return out


LATEX_ROWS = [
    ("Registration (full workflow)", "registration_ms"),
    ("Overdue detection sweep", "overdue_sweep_ms"),
    ("Risk + dropout, per-child path", "scoring_per_child_ms"),
    ("Risk + dropout, cohort path", "scoring_cohort_per_child_ms"),
    ("Dashboard aggregation query", "dashboard_ms"),
    ("Sync flush (per envelope)", "sync_per_record_ms"),
    ("Notification enqueue (per msg)", "notify_enqueue_ms"),
    ("Notification dispatch (per msg)", "notify_dispatch_ms"),
]


def emit_latex(results):
    print("\n" + "=" * 74)
    print("LaTeX table body")
    print("=" * 74)
    for label, key in LATEX_ROWS:
        cells = " & ".join(fmt(r[key]) for r in results)
        print("%s & %s \\\\" % (label, cells))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="*", default=None)
    ap.add_argument("--full", action="store_true", help="include the 100k cohort")
    ap.add_argument("--out", default="benchmark_results.json")
    args = ap.parse_args()

    sizes = args.sizes or ([1000, 10000, 100000] if args.full else [1000, 10000])

    print("EIMS benchmark -- Django %s, %s" % (django.get_version(), connection.vendor))
    with connection.cursor() as cur:
        cur.execute("SELECT version()")
        print(cur.fetchone()[0])

    # A throwaway database, so the benchmark never touches deployment data.
    old_config = setup_databases(verbosity=0, interactive=False)
    try:
        results = [run_size(n) for n in sizes]
        emit_latex(results)
        Path(args.out).write_text(json.dumps(results, indent=2))
        print("\nWrote %s" % Path(args.out).resolve())
    finally:
        teardown_databases(old_config, verbosity=0)


if __name__ == "__main__":
    main()
