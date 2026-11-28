#!/usr/bin/env python3
"""Server time of token-holding sidebar boot reads (CCC-730).

A page boot that holds a delta token saved under an earlier list-reader
process sends it as ``since``; the reader answers with an adopted-token delta
(list_server ``since=adopted``). Those reads cost 400-970 ms on the server
while later reads cost ~150 ms, because an idle reader reloaded inside them.

Each human boot read (client ``list_read`` with ``detail.boot``, steady: not
automated, hidden, a popout, warm-up, a saturated host or a fresh restart) is
joined to the reader's ``list_server`` row by time and server_ms. The gate is
the p95 of their server_ms over the window, on builds containing --commit.

Prints {"status": pass|fail|pending, "real_data": bool, "evidence": {...}}.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import perf_gate

BUDGET_MS = 300
MINIMUM = 5
WINDOW_DAYS = 7
# The server row is written before the response leaves; the client row after
# its body. Join within this many seconds before the client row (plus its ms).
JOIN_SLACK_S = 5.0
# Server-Timing is stamped before the body is sent; the diag row after.
MS_TOLERANCE = 60.0
MS_TOLERANCE_RATIO = 0.10
# Boot reads that held a saved token. "adopted" (a previous reader's token)
# is the ticket's cohort; "ok"/"current" boot reads overlapped the same idle
# reload (00:49:42Z: since=ok, 406 ms, busy_reload_ms 406), so they count too.
TOKEN_SINCE = ("adopted", "ok", "current")


def _server_ms(row):
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    value = detail.get("server_ms")
    return float(value) if isinstance(value, (int, float)) else None


def boot_reads(client_rows, deployed):
    """(steady, startup) human boot list reads on deployed builds."""
    steady, startup = [], []
    for row in client_rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        if row.get("kind") != "list_read" or not isinstance(detail.get("boot"), dict):
            continue
        if _server_ms(row) is None or not deployed(row):
            continue
        reason = perf_gate._steady_reason(row)
        if reason == "":
            steady.append(row)
        elif reason == "boot":
            startup.append(row)
    return steady, startup


def token_match(read, server_rows):
    """The list_server row that answered `read` with a saved token, or None."""
    ms = _server_ms(read)
    end = read["_ts"]
    start = end - timedelta(seconds=float(read.get("ms") or 0) / 1000.0 + JOIN_SLACK_S)
    best = None
    for row in server_rows:
        if not (start <= row["_ts"] <= end):
            continue
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        if detail.get("status") == "relayed":
            continue  # the dashboard's relay leg; the reader's row answers
        gap = abs(float(row.get("ms") or 0) - ms)
        if gap <= max(MS_TOLERANCE, MS_TOLERANCE_RATIO * ms) and (best is None or gap < best[0]):
            best = (gap, row)
    if best is None:
        return None
    detail = best[1].get("detail") or {}
    return best[1] if detail.get("since") in TOKEN_SINCE else None


def check(client_rows, server_rows, deployed):
    steady, startup = boot_reads(client_rows, deployed)
    samples, startup_samples = [], []
    for reads, out in ((steady, samples), (startup, startup_samples)):
        for read in reads:
            match = token_match(read, server_rows)
            if match is None:
                continue
            detail = match.get("detail") or {}
            out.append({"ts": read["ts"][:19], "server_ms": _server_ms(read), "boot_id": read.get("boot_id"),
                        "window": detail.get("window"), "since": detail.get("since"),
                        "reader": bool(detail.get("reader")),
                        "busy_reload_ms": detail.get("busy_reload_ms"),
                        "busy_warm_ms": detail.get("busy_warm_ms")})
    values = [s["server_ms"] for s in samples]
    adopted = [s["server_ms"] for s in samples if s["since"] == "adopted"]
    p95 = perf_gate._percentile(values, 95) if values else None
    status = "pending" if len(values) < MINIMUM else "pass" if p95 <= BUDGET_MS else "fail"
    slowest = sorted(samples, key=lambda s: -s["server_ms"])[:5]
    return {"status": status, "real_data": bool(values), "evidence": {
        "budget_ms": BUDGET_MS, "minimum": MINIMUM, "window_days": WINDOW_DAYS,
        "samples": len(values), "p95_ms": p95,
        "adopted_p95_ms": perf_gate._percentile(adopted, 95) if adopted else None,
        "adopted_samples": len(adopted),
        "over_budget": sum(v > BUDGET_MS for v in values),
        "overlapping_reload": sum(bool(s["busy_reload_ms"]) for s in samples),
        "slowest": slowest,
        "startup_samples_ungated": startup_samples[-5:],
    }}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", required=True, help="fix commit the builds must contain")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--events", default=str(perf_gate.DEFAULT_EVENTS))
    parser.add_argument("--days", type=float, default=WINDOW_DAYS)
    args = parser.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    rows = perf_gate._read_rows_for_kinds(args.events, since, ("list_read", "list_server"))
    client = [r for r in rows if r.get("kind") == "list_read"]
    # Every answering row, not only adopted ones: a boot read whose closest
    # match was answered another way must not borrow a neighbour's row.
    server = [r for r in rows if r.get("kind") == "list_server"]
    result = check(client, server, perf_gate._Deployed(args.repo, args.commit))
    result["evidence"].update(commit=args.commit, events=args.events)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
