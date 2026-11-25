#!/usr/bin/env python3
"""Watch check for CCC-680: do composer launches reach the engine quickly?

Reads the spawn timeline (read-only) for dashboard-launched Claude spawns
recorded since a given epoch that carry both the server's step marks
(server_request_received ... server_engine_submit) and the page's
client_request_sent mark. Emits one JSON object:

  status    pass | fail | pending
  real_data True when the verdict rests on real launches
  evidence  per-launch split of click -> POST -> server -> submit

fail:    some launch waited > LIMIT_MS inside the dashboard before its engine
         submit (the ticket's symptom), or > LIMIT_MS between POST and the
         server seeing it while the server was otherwise answering.
pass:    at least MIN_SAMPLES such launches, none over the limit.
pending: fewer qualifying launches so far.
"""

import argparse
import json
import os
from pathlib import Path

LIMIT_MS = 3000.0
MIN_SAMPLES = 5


def _state_dir():
    return Path(os.environ.get("CCC_STATE_DIR") or Path.home() / ".claude" / "command-center")


def analyse(timeline, since_epoch, limit_ms=LIMIT_MS, min_samples=MIN_SAMPLES):
    rows = []
    for sid, entry in (timeline or {}).items():
        if not isinstance(entry, dict) or entry.get("engine") != "claude":
            continue
        try:
            t0 = float(entry.get("t0") or 0)
        except (TypeError, ValueError):
            continue
        marks = entry.get("marks") or {}
        if t0 < since_epoch or "server_engine_submit" not in marks:
            continue
        if "client_request_sent" not in marks:
            continue  # scripted/API launches: no page in the loop
        received = float(marks.get("server_request_received", 0))
        submit = float(marks["server_engine_submit"])
        sent = float(marks["client_request_sent"])
        rows.append({
            "session_id": sid,
            "t0": t0,
            "page_ms": round(sent, 1),
            "network_ms": round(received - sent, 1),
            "dashboard_presubmit_ms": round(submit - received, 1),
            "click_to_submit_ms": round(submit, 1),
        })
    rows.sort(key=lambda r: r["t0"])
    slow_dashboard = [r for r in rows if r["dashboard_presubmit_ms"] > limit_ms]
    slow_total = [r for r in rows if r["click_to_submit_ms"] > limit_ms]
    if slow_dashboard:
        status = "fail"
    elif len(rows) >= min_samples:
        status = "pass"
    else:
        status = "pending"
    return {
        "status": status,
        "real_data": bool(rows),
        "evidence": {
            "launches": len(rows),
            "since_epoch": since_epoch,
            "limit_ms": limit_ms,
            "dashboard_presubmit_over_limit": slow_dashboard[-5:],
            # Reported, not failing: a slow phone link between the page and
            # the server is outside the dashboard; the split shows which side.
            "click_to_submit_over_limit": slow_total[-5:],
            "recent": rows[-5:],
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=float, required=True, help="epoch seconds of the deploy")
    ap.add_argument("--timeline", default=str(_state_dir() / "spawn-timeline.json"))
    args = ap.parse_args()
    try:
        timeline = json.loads(Path(args.timeline).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "pending", "real_data": False,
                          "evidence": {"error": f"timeline unreadable: {exc}"}}))
        return
    print(json.dumps(analyse(timeline, args.since)))


if __name__ == "__main__":
    main()
