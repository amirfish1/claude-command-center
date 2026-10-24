#!/usr/bin/env python3
"""Acceptance gate over CCC's perf telemetry (perf-events.jsonl).

Used as a WatchTower ``cmd:`` gate on deep perf tickets, so "done" is the
measured symptom gone -- not a plausible cause fixed. Passes (exit 0) only when
enough recent samples of ``--kind``, measured on code that contains the fix,
meet the percentile budget. Fails (exit 1) with a reason a worker can act on:
too few post-deploy samples means "deploy and collect data" (``wt defer``), a
blown budget means "not fixed yet".

Which samples count, when ``--commit`` (default ``$WT_GATE_COMMIT``) is set:
  * the sample's server ``build`` stamp contains the commit (git ancestry), or
  * the only files changed between that build and the commit are ``static/``
    (served live from disk -- a page reload, not a restart, deploys them) and
    the sample was recorded after the commit was made.

Stdlib only. Example:
    python3 scripts/perf_gate.py --kind conv_open --p95-ms 1000 --min-samples 20
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_EVENTS = Path.home() / ".claude" / "command-center" / "perf-events.jsonl"
TAIL_BYTES = 8 * 1024 * 1024


def _parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _git(repo, *args):
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return proc.returncode, proc.stdout.strip()


def _read_rows(path, since):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            blob = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = blob.splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]  # first line is likely cut
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        ts = _parse_ts(row.get("ts")) if isinstance(row, dict) else None
        if ts and ts >= since:
            row["_ts"] = ts
            rows.append(row)
    return rows


class _Deployed:
    """Memoised "does build B contain the fix" check."""

    def __init__(self, repo, commit):
        self.repo, self.commit, self.cache = repo, commit, {}
        rc, out = _git(repo, "show", "-s", "--format=%cI", commit) if commit else (1, "")
        self.commit_time = _parse_ts(out) if rc == 0 else None

    def __call__(self, row):
        if not self.commit:
            return True
        build = str(row.get("build") or "")
        if build not in self.cache:
            self.cache[build] = self._verdict(build)
        mode = self.cache[build]
        if mode == "contains":
            return True
        if mode == "static-only":
            return bool(self.commit_time and row["_ts"] >= self.commit_time)
        return False

    def _verdict(self, build):
        if not build:
            return "unknown"
        rc, _ = _git(self.repo, "merge-base", "--is-ancestor", self.commit, build)
        if rc == 0:
            return "contains"
        rc, out = _git(self.repo, "diff", "--name-only", build, self.commit)
        if rc == 0 and out and all(p.startswith("static/") for p in out.splitlines()):
            return "static-only"
        return "missing"


def _percentile(vals, pct):
    if not vals:
        return 0
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, math.ceil(pct / 100.0 * len(vals)) - 1))
    return vals[k]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--kind", required=True, help="perf-event kind, e.g. conv_open")
    ap.add_argument("--p95-ms", type=float, default=None)
    ap.add_argument("--p50-ms", type=float, default=None)
    ap.add_argument("--max-ms", type=float, default=None)
    ap.add_argument("--min-samples", type=int, default=20)
    ap.add_argument("--hours", type=float, default=24.0, help="look-back window")
    ap.add_argument("--exclude-status", action="append", default=[],
                    help="drop samples whose detail.status equals this (repeatable), "
                         "e.g. 304 for list reads that skipped the body")
    ap.add_argument("--commit", default=os.environ.get("WT_GATE_COMMIT", ""),
                    help="only count samples measured on code containing this commit")
    ap.add_argument("--repo", default=os.environ.get("WT_GATE_REPO") or ".",
                    help="git repo for the deploy check")
    ap.add_argument("--events", default=str(DEFAULT_EVENTS))
    args = ap.parse_args(argv)
    if args.p95_ms is None and args.p50_ms is None and args.max_ms is None:
        ap.error("give at least one budget: --p95-ms / --p50-ms / --max-ms")

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    rows = [r for r in _read_rows(args.events, since) if r.get("kind") == args.kind]
    excluded = {str(s) for s in args.exclude_status}
    if excluded:
        rows = [r for r in rows
                if str(((r.get("detail") or {}) if isinstance(r.get("detail"), dict) else {})
                       .get("status")) not in excluded]
    deployed = _Deployed(args.repo, args.commit)
    counted = [r for r in rows if deployed(r)]
    ms = [float(r.get("ms") or 0) for r in counted]

    label = f"{args.kind} over {args.hours:g}h"
    print(f"perf_gate: {label}: {len(rows)} samples, {len(counted)} on code with the fix"
          + (f" ({args.commit[:12]})" if args.commit else ""))
    if len(counted) < args.min_samples:
        print(f"FAIL: need >= {args.min_samples} post-fix samples, have {len(counted)}. "
              "Deploy the fix (restart the dashboard for server changes; static/ changes "
              "need only a page reload), use the product to produce samples, then retry "
              "-- `wt defer` the ticket while they accumulate.")
        return 1
    stats = {"p50": _percentile(ms, 50), "p95": _percentile(ms, 95), "max": max(ms)}
    print("perf_gate: p50=%dms p95=%dms max=%dms" % (stats["p50"], stats["p95"], stats["max"]))
    failures = []
    for key, budget in (("p50", args.p50_ms), ("p95", args.p95_ms), ("max", args.max_ms)):
        if budget is not None and stats[key] > budget:
            failures.append(f"{key} {stats[key]:.0f}ms > budget {budget:.0f}ms")
    if failures:
        worst = sorted(counted, key=lambda r: -float(r.get("ms") or 0))[:5]
        print("FAIL: " + "; ".join(failures))
        for r in worst:
            print(f"  worst: {r.get('ts')} {r.get('ms')}ms warm={r.get('warm')} "
                  f"load1={r.get('load1')} conv={r.get('conv_id') or '-'}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
