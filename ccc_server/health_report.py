# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Local health report: one JSON answer to "how is this install doing?".

Backs ``GET /api/telemetry/health-report``. Built only on request, from data
that is already on disk; it sends nothing anywhere. Someone who reports
"switching is slow" or "Decisions only shows sometimes" can open the URL and
paste the JSON into a bug report, and the maintainer gets numbers instead of
adjectives.

The shape follows the patterns open-source projects settled on for telemetry
they need to debug at scale (Go toolchain telemetry, Syncthing usage
reports):

- Latency is a fixed-bucket histogram plus p50/p75/p95. Unlike
  percentiles, histograms from many installs add up exactly, so fleet-wide
  percentiles can be computed from them later.
- Counts are bucketed by default ("21-50"), so the report can be shared
  without exposing exact fleet size. ``?exact=1`` returns raw numbers for
  private support.
- A closed allowlist: versions, platform, timings, counts. No session ids,
  paths, repo names, ticket text, queue names, user app ids, or prompt
  content. View ids outside the built-in set are reported as "custom".
- ``report_version`` changes whenever a field is added or changed. If
  uploading is ever added, that change re-asks for consent, the way
  Syncthing handles report-version bumps.

Cost: one tail read of perf-events.jsonl and of activity.log, the session
row list the dashboard already caches, decision-inbox cards, and two ``wt``
calls cached for an hour. It runs only when someone requests a report,
never on a poll.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import sys
import threading
import time

from ccc_server import core as _core
from ccc_server import perf_events as _perf

REPORT_VERSION = 1

# Upper edges in ms; the last count is everything at or above the final edge.
LATENCY_BUCKETS_MS = (100, 250, 500, 1000, 2000, 4000, 8000, 16000)

_COUNT_BUCKETS = ((0, 0), (1, 1), (2, 5), (6, 20), (21, 50), (51, 100),
                  (101, 500), (501, 2000))

# View ids that may appear by name; everything else (user apps, embeds)
# collapses to "custom" so a report never carries a user's own app names.
_BUILTIN_VIEWS = {"sessions", "queues", "morning", "decision-inbox",
                  "spawn-ledger", "savings", "pipeline-canvas"}

_ACTIVITY_TAIL_BYTES = 4 * 1024 * 1024
_WT_CACHE_TTL_S = 3600
_wt_cache = {"at": 0.0, "value": None}
_wt_lock = threading.Lock()


def bucket_count(n):
    """Coarse label for a count: "0", "1", "2-5", ... , "2000+"."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    for lo, hi in _COUNT_BUCKETS:
        if lo <= n <= hi:
            return str(lo) if lo == hi else "%d-%d" % (lo, hi)
    return "%d+" % (_COUNT_BUCKETS[-1][1] + 1)


def _count(n, exact):
    return n if exact else bucket_count(n)


def latency_stats(ms_values):
    vals = sorted(int(v) for v in ms_values)
    hist = [0] * (len(LATENCY_BUCKETS_MS) + 1)
    for v in vals:
        for i, edge in enumerate(LATENCY_BUCKETS_MS):
            if v < edge:
                hist[i] += 1
                break
        else:
            hist[-1] += 1
    return {
        "count": len(vals),
        "p50": _perf._percentile(vals, 50),
        "p75": _perf._percentile(vals, 75),
        "p95": _perf._percentile(vals, 95),
        "hist": hist,
    }


def _view_id(v):
    v = str(v or "")
    if not v:
        return "unknown"
    return v if v in _BUILTIN_VIEWS else "custom"


def _latency_section(events):
    by_kind = {}
    for e in events:
        kind = e.get("kind")
        if kind in _perf._VALID_KINDS:
            by_kind.setdefault(kind, []).append(e.get("ms") or 0)
    latency = {k: latency_stats(v) for k, v in sorted(by_kind.items())}

    switches = {}
    rail_ok = rail_failed = rail_retries = 0
    for e in events:
        d = e.get("detail") if isinstance(e.get("detail"), dict) else {}
        if e.get("kind") == "view_switch":
            key = "%s:%s" % (_view_id(d.get("to")),
                             "in_page" if d.get("mode") == "in_page" else "navigate")
            switches.setdefault(key, []).append(e.get("ms") or 0)
        elif e.get("kind") == "rail_apps":
            if d.get("ok"):
                rail_ok += 1
            else:
                rail_failed += 1
            if (d.get("attempt") or 0) > 0:
                rail_retries += 1
    return latency, {k: latency_stats(v) for k, v in sorted(switches.items())}, {
        "ok": rail_ok, "failed": rail_failed, "retries": rail_retries}


def _waiting_on_user(row):
    """A pending question/approval, a blocked end of turn, or a session-state
    footer whose NEXT_STEP_USER names a real action."""
    if row.get("question_waiting") or row.get("needs_approval") or row.get("ended_blocked"):
        return True
    state = row.get("session_state")
    step = str((state or {}).get("next_step_user") or "").strip() if isinstance(state, dict) else ""
    return bool(step) and step.rstrip(".").lower() != "none"


def _session_section(rows, cards, now, exact):
    since = now - 24 * 3600
    active = [r for r in rows if (r.get("mtime") or 0) >= since]
    active_ids = {r.get("session_id") for r in active if r.get("session_id")}
    waiting = sum(1 for r in active if _waiting_on_user(r))

    open_cards = [c for c in cards if c.get("status") == "open"]
    carded = set()
    for c in open_cards:
        src = c.get("source") if isinstance(c.get("source"), dict) else {}
        sids = [src.get("session_id")]
        sids += [((opt or {}).get("action") or {}).get("session_id")
                 for opt in c.get("options") or []]
        carded.update(s for s in sids if s)
    with_card = len(carded & active_ids)

    def pct(n):
        # Rounded to 5% so a small install cannot be reverse-engineered.
        return int(round(100.0 * n / len(active) / 5.0) * 5) if active else None

    return {
        "active_24h": _count(len(active), exact),
        "live": _count(sum(1 for r in active if r.get("is_live")), exact),
        "pct_with_open_decision": pct(with_card),
        "pct_waiting_on_user": pct(waiting),
        "decision_cards_open": _count(len(open_cards), exact),
    }


_INJECT_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC\s+\S+\s+INJECT\s.*?\bsource=(\S+)")


def _messages_section(path, now, exact):
    since = now - 24 * 3600
    by_source = {}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > _ACTIVITY_TAIL_BYTES:
                f.seek(size - _ACTIVITY_TAIL_BYTES)
                f.readline()
            for raw in f:
                m = _INJECT_RE.match(raw.decode("utf-8", errors="ignore"))
                if not m:
                    continue
                try:
                    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=timezone.utc).timestamp()
                except ValueError:
                    continue
                if ts < since:
                    continue
                # composer = typed by a person in the dashboard; api/wt = agents.
                src = m.group(2) if m.group(2) in ("composer", "api", "wt") else "other"
                by_source[src] = by_source.get(src, 0) + 1
    except OSError:
        pass
    return {
        "sent_24h": _count(sum(by_source.values()), exact),
        "by_source": {k: _count(v, exact) for k, v in sorted(by_source.items())},
    }


def _wt_counts():
    """(queues, open tickets, workers alive, workers known) or None."""
    with _wt_lock:
        if _wt_cache["value"] is not None and time.time() - _wt_cache["at"] < _WT_CACHE_TTL_S:
            return _wt_cache["value"]
    from ccc_server import decision_inbox as _di
    status = _di._di_wt_run(["status", "--json"], timeout=30)
    workers = _di._di_wt_run(["workers", "--json"], timeout=30)
    if not isinstance(status, list):
        return None
    workers = workers if isinstance(workers, list) else []
    value = (
        len(status),
        sum(int(q.get("depth") or 0) for q in status if isinstance(q, dict)),
        sum(1 for w in workers if isinstance(w, dict) and w.get("alive")),
        len(workers),
    )
    with _wt_lock:
        _wt_cache.update(at=time.time(), value=value)
    return value


def build_report(exact=False, now=None, rows=None, cards=None, wt_counts=None,
                 events=None, activity_path=None):
    """The report dict. Every source degrades to empty instead of raising."""
    now = time.time() if now is None else now
    if events is None:
        events = _perf.read_events(now - 24 * 3600)
    if rows is None:
        from ccc_server import decision_inbox as _di
        rows = _di._server_rows()
    if cards is None:
        try:
            from ccc_server import decision_inbox as _di
            cards = list((_di.load_cards() or {}).values())
        except Exception:
            cards = []
    if wt_counts is None:
        try:
            wt_counts = _wt_counts()
        except Exception:
            wt_counts = None
    if activity_path is None:
        from ccc_server import activity_log as _al
        activity_path = _al.ACTIVITY_LOG_FILE

    latency, view_switch, rail = _latency_section(events)
    wt = None
    if wt_counts:
        q, t, alive, known = wt_counts
        wt = {"queues": _count(q, exact), "tickets_open": _count(t, exact),
              "workers_alive": _count(alive, exact), "workers_known": _count(known, exact)}
    return {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "window_hours": 24,
        "exact": bool(exact),
        "ccc_version": str(getattr(_core, "__version__", "") or ""),
        "platform": sys.platform,
        "latency_buckets_ms": list(LATENCY_BUCKETS_MS),
        "latency": latency,
        "view_switch": view_switch,
        "rail_apps": rail,
        "sessions": _session_section(rows or [], cards or [], now, exact),
        "messages": _messages_section(activity_path, now, exact),
        "watchtower": wt,
    }
