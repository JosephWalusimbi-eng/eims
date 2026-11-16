"""Regenerate the monitoring-dashboard figure from the running system.

The published figure showed eight district rows for five districts, with the
per-district overdue counts summing to 192 against a stated total of 124. The
cause was `Caregiver.Meta.ordering` leaking into a `SELECT DISTINCT`. This
script rebuilds the figure from the fixed view so the numbers in the caption
are the numbers a reader can reproduce.

It renders the real Django template through the authenticated test client --
no dev server, no manual screenshot -- and rasterises the result with headless
Chrome. The cohort comes from `manage.py seed_prototype`, which is seeded, so
the figure is deterministic.

Usage:
    python analysis/render_dashboard_figure.py --out EIMS_IEEE_NCC/dashboard.png
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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

from django.contrib.auth.models import Group, User  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_databases, teardown_databases  # noqa: E402

from apps.core.auth import MONITOR_GROUP_NAME  # noqa: E402

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome",
    "chromium",
]


def find_chrome():
    for candidate in CHROME_CANDIDATES:
        if os.path.sep in candidate or ":" in candidate:
            if Path(candidate).exists():
                return candidate
        elif shutil.which(candidate):
            return shutil.which(candidate)
    return None


def render_html() -> str:
    """Return the dashboard HTML as a monitoring user would see it."""
    call_command("seed_prototype", flush=True, verbosity=0)

    user = User.objects.create_user("figure-monitor", password="figure-only-not-a-secret")
    user.groups.add(Group.objects.get_or_create(name=MONITOR_GROUP_NAME)[0])

    client = Client()
    client.force_login(user)
    response = client.get("/")
    if response.status_code != 200:
        raise SystemExit("dashboard returned HTTP %d" % response.status_code)

    html = response.content.decode("utf-8")

    # The figure is a still: drop the action button, which would otherwise
    # render as a live form control in a printed page.
    html = re.sub(r"<form method=\"post\".*?</form>", "", html, flags=re.S)
    return html


def summarise():
    """Print the numbers the figure caption must state."""
    from apps.immunization.models import Child, RiskScore
    from apps.immunization.services.detection import get_overdue_vaccines

    overdue = get_overdue_vaccines()
    print("  children              %d" % Child.objects.count())
    print("  overdue dose-instances %d" % overdue.count())
    print("  high-risk children    %d"
          % Child.objects.filter(pk__in=__import__("apps.immunization.services.atrisk", fromlist=["x"]).high_risk_child_ids()).count())
    for level in ("LOW", "MEDIUM", "HIGH"):
        n = Child.objects.filter(risk_scores__level=level).distinct().count()
        print("    tier %-7s %d" % (level, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dashboard.png")
    ap.add_argument("--width", type=int, default=1500)
    ap.add_argument("--height", type=int, default=840)
    args = ap.parse_args()

    chrome = find_chrome()
    if chrome is None:
        raise SystemExit("No Chrome or Edge binary found for headless rendering.")

    old_config = setup_databases(verbosity=0, interactive=False)
    try:
        html = render_html()
        print("Prototype cohort:")
        summarise()

        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "dashboard.html"
            page.write_text(html, encoding="utf-8")
            out = Path(args.out).resolve()
            subprocess.run(
                [
                    chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=2",
                    "--virtual-time-budget=8000",
                    "--window-size=%d,%d" % (args.width, args.height),
                    "--screenshot=%s" % out,
                    page.as_uri(),
                ],
                check=True, capture_output=True, timeout=180,
            )
            print("Wrote %s (%.0f KB)" % (out, out.stat().st_size / 1024))
    finally:
        teardown_databases(old_config, verbosity=0)


if __name__ == "__main__":
    main()
