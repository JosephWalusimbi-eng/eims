"""Regenerate the system architecture figure (Fig. 1).

The original figure was drawn before the system was built out, and had drifted
from it in three ways that mattered:

  * it advertised "Conflict Resolution (latest timestamp wins)" as the design,
    which is the policy the evaluation measures as losing 100% of contested
    dose administrations;
  * it had no notification queue, though the separation of enqueue from
    dispatch is what makes the offline claim operational;
  * it showed no security layer, though encryption at rest and the append-only
    audit log are now central claims.

A figure that contradicts the paper's own results is worse than no figure, so
this script generates it from a declarative description that lives next to the
code it depicts. Rendered with headless Chrome so it is reproducible and needs
no drawing tool.

Usage:
    python analysis/render_architecture_figure.py --out EIMS_IEEE_NCC/offline_architecture.png
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

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


HTML = r"""
<meta charset="utf-8">
<style>
  :root {
    --ink:#1a1a1a; --muted:#5b6470; --line:#8a93a0;
    --actor-bg:#eaf2fb; --actor-br:#3d6fa8;
    --app-bg:#eaf6ee;  --app-br:#3f8055;
    --data-bg:#fdf4e3; --data-br:#b08333;
    --sync-bg:#f1ecf8; --sync-br:#6b5698;
    --queue-bg:#fdeef0; --queue-br:#a8455a;
    --srv-bg:#eef1f5;  --srv-br:#4a5568;
    --off-bg:#fbecec;  --off-br:#a33;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; padding:20px 22px 24px;
    font:13.5px/1.35 "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    color:var(--ink); background:#fff; width:940px;
  }
  .band { border-radius:9px; padding:9px 12px 11px; margin:0; border:1.5px solid; }
  .band > h4 { margin:0 0 7px; font-size:13px; font-weight:700; letter-spacing:.15px; }
  .row { display:flex; gap:8px; flex-wrap:wrap; }
  .chip {
    flex:1 1 0; min-width:0; text-align:center; border-radius:6px;
    padding:7px 6px; font-size:12px; font-weight:600; color:#fff; line-height:1.25;
  }
  .chip small { display:block; font-weight:400; font-size:10.5px; opacity:.92; margin-top:2px; }

  .actor { background:var(--actor-bg); border-color:var(--actor-br); }
  .actor h4 { color:var(--actor-br); }
  .actor .chip { background:#3d6fa8; }

  .app { background:var(--app-bg); border-color:var(--app-br); }
  .app h4 { color:var(--app-br); }
  .app .chip { background:#3f8055; }

  .data { background:var(--data-bg); border-color:var(--data-br); }
  .data h4 { color:#8a6520; }
  .data .chip { background:#b08333; }

  .queue { background:var(--queue-bg); border-color:var(--queue-br); }
  .queue h4 { color:var(--queue-br); }
  .queue .chip { background:#a8455a; }

  .sync { background:var(--sync-bg); border-color:var(--sync-br); }
  .sync h4 { color:var(--sync-br); }
  .sync .chip { background:#6b5698; }

  .srv { background:var(--srv-bg); border-color:var(--srv-br); }
  .srv h4 { color:var(--srv-br); }
  .srv .chip { background:#4a5568; }

  .arrow { text-align:center; color:var(--line); font-size:17px; line-height:1; margin:5px 0; }
  .arrow span { font-size:10.5px; color:var(--muted); vertical-align:2px; margin-left:6px; font-style:italic; }

  .split { display:flex; gap:14px; align-items:flex-start; }
  .split > .band { flex:1; align-self:flex-start; }
  .split > .band .row { align-content:flex-start; }
  .offline {
    width:238px; flex:0 0 238px; background:var(--off-bg); border:1.5px dashed var(--off-br);
    border-radius:9px; padding:9px 11px 11px;
  }
  .offline h4 { margin:0 0 6px; font-size:12.5px; font-weight:700; color:var(--off-br);
                letter-spacing:.3px; text-transform:uppercase; }
  .offline ul { margin:0; padding-left:15px; }
  .offline li { font-size:11.5px; margin-bottom:4px; color:#4a2020; }
  .offline .note { margin-top:7px; font-size:10.5px; color:#7a3a3a; font-style:italic;
                   border-top:1px dashed #c98a8a; padding-top:6px; }
  .lock { font-weight:600; font-size:11.5px; color:var(--muted); }
</style>

<div class="band actor">
  <h4>Health Worker &mdash; point of care</h4>
  <div class="row">
    <div class="chip">Register Child</div>
    <div class="chip">Enter Caregiver Contacts</div>
    <div class="chip">Record Vaccination</div>
  </div>
</div>

<div class="arrow">&#8595;<span>executes locally, no network required</span></div>

<div class="band app">
  <h4>Local EIMS Application</h4>
  <div class="row">
    <div class="chip">Schedule Generation<small>EPI offsets</small></div>
    <div class="chip">Overdue Detection</div>
    <div class="chip">Risk &amp; Dropout Scoring<small>ranking, not probability</small></div>
    <div class="chip">PDF Card</div>
    <div class="chip">Calendar (.ics)<small>minimised payload</small></div>
  </div>
</div>

<div class="arrow">&#8595;</div>

<div class="split">
  <div class="band data">
    <h4>Local Replica <span class="lock">&mdash; encrypted at rest</span></h4>
    <div class="row">
      <div class="chip">Child &amp; Caregiver<small>AES-256-GCM columns</small></div>
      <div class="chip">Vaccine Records<small>version + device clock</small></div>
      <div class="chip">Outbox<small>queued mutations</small></div>
      <div class="chip">Audit Log<small>append-only</small></div>
    </div>
  </div>
  <div class="offline">
    <h4>Offline Mode</h4>
    <ul>
      <li>All clinical operations continue</li>
      <li>Mutations accumulate in the outbox</li>
      <li>Notifications hold at <b>queued</b></li>
      <li>PDF and .ics still issued</li>
    </ul>
    <div class="note">On reconnection the outbox and the queue drain; nothing is dropped.</div>
  </div>
</div>

<div class="arrow">&#8595;</div>

<div class="band queue">
  <h4>Notification Queue &mdash; enqueue and dispatch are separate events</h4>
  <div class="row">
    <div class="chip">Planning<small>email, T&minus;5 d</small></div>
    <div class="chip">Urgency<small>SMS, T&minus;3 d</small></div>
    <div class="chip">Overdue<small>SMS on detection</small></div>
    <div class="chip">Persistence<small>.ics at registration</small></div>
    <div class="chip">Retry &amp; Dedupe<small>failure stays queued</small></div>
  </div>
</div>

<div class="arrow">&#8595;<span>when connectivity returns</span></div>

<div class="band sync">
  <h4>Synchronization Engine</h4>
  <div class="row">
    <div class="chip">Flush in Device-Clock Order<small>not server arrival order</small></div>
    <div class="chip">Conflict Detection<small>base version vs. server</small></div>
    <div class="chip">Field Merge + Clinical Latch<small>administration is monotonic</small></div>
    <div class="chip">Idempotent Replay<small>envelope key</small></div>
  </div>
</div>

<div class="arrow">&#8595;</div>

<div class="band srv">
  <h4>Central Server (PostgreSQL) <span class="lock">&mdash; encrypted at rest, TLS in transit</span></h4>
  <div class="row">
    <div class="chip">Master Records</div>
    <div class="chip">Notification Log</div>
    <div class="chip">Sync Envelopes</div>
    <div class="chip">Audit Log<small>reads and writes</small></div>
    <div class="chip">Role-Based Access<small>doctor / monitor / admin</small></div>
  </div>
</div>

<div class="arrow">&#8595;<span>TLS to gateways</span></div>

<div class="band app">
  <h4>External Services</h4>
  <div class="row">
    <div class="chip">SMS Gateway</div>
    <div class="chip">Email SMTP</div>
    <div class="chip">Calendar Platforms</div>
  </div>
</div>

<div class="arrow">&#8595;</div>

<div class="band actor">
  <h4>Caregiver</h4>
  <div class="row">
    <div class="chip">SMS Reminder<small>given name, date, facility</small></div>
    <div class="chip">Email + PDF Card<small>full schedule</small></div>
    <div class="chip">Calendar Events<small>passive persistence</small></div>
  </div>
</div>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="offline_architecture.png")
    ap.add_argument("--width", type=int, default=980)
    ap.add_argument("--height", type=int, default=1015)
    args = ap.parse_args()

    chrome = find_chrome()
    if chrome is None:
        raise SystemExit("No Chrome or Edge binary found for headless rendering.")

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "architecture.html"
        page.write_text(HTML, encoding="utf-8")
        out = Path(args.out).resolve()
        subprocess.run(
            [
                chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                "--force-device-scale-factor=2",
                "--virtual-time-budget=6000",
                "--window-size=%d,%d" % (args.width, args.height),
                "--screenshot=%s" % out,
                page.as_uri(),
            ],
            check=True, capture_output=True, timeout=180,
        )
        print("Wrote %s (%.0f KB)" % (out, out.stat().st_size / 1024))


if __name__ == "__main__":
    main()
