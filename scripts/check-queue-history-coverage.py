#!/usr/bin/env python3
"""Permanent read-only check for seven days of recorded hourly status counts."""
import argparse
import json
import os
from pathlib import Path
import time


def check(history, now=None):
    now = time.time() if now is None else now
    end = int(now // 3600) * 3600
    hours = set(range(end - 7 * 86400, end + 1, 3600))
    recorded = set()
    fields = ("open", "needs_input", "awaiting_worker", "waiting_tickets", "other")
    try:
        with open(history) as source:
            for line in source:
                try:
                    row = json.loads(line)
                    hour = int(float(row["ts"]) // 3600) * 3600
                    if (hour in hours and row.get("status_counts_version") == 1
                            and all(isinstance(row.get(f), (int, float)) for f in fields)):
                        recorded.add(hour)
                except (ValueError, TypeError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    missing = len(hours - recorded)
    return {"status": "pending" if missing else "pass", "real_data": bool(recorded),
            "evidence": {"history": str(history), "recorded_hours": len(recorded),
                         "required_hours": len(hours), "missing_hours": missing}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    state_dir = Path(os.environ.get("CCC_STATE_DIR") or Path.home() / ".claude" / "command-center")
    parser.add_argument("--history", default=str(state_dir / "queue-history.jsonl"))
    args = parser.parse_args()
    print(json.dumps(check(args.history)))
