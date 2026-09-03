# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Perf-event telemetry sink + breach-pattern ticket filer.

The frontend beacons two kinds of client-measured timings here:

- ``archive_load`` — how long ``/api/conversations/all`` took to paint,
  labeled warm/cold from the archive-load snapshot (see
  ``_archive_load_snapshot`` in server.py).
- ``conv_open`` — how long opening a single conversation took.

Each POST (``/api/perf-event``, wired in server.py) appends one line to a
JSONL sink and returns whether that sample breached its threshold. A
background daemon thread (``perf_ticket_loop``) periodically looks at the
last 24h of events; if a real regression pattern shows up (repeated
breaches, or one wildly-over-threshold sample) it self-files a WatchTower
bug ticket in the CCC queue, with a same-day / already-open dedupe so it
never spams. Everything here is best-effort: file IO, `wt` subprocess
calls, and JSON parsing all degrade to safe defaults rather than raising
into a request handler or crashing the daemon thread.

Names still living in server.py (state dir root, the archive-load
snapshot) are reached via ``_core`` at call time, same convention as
every other ccc_server module."""

from __future__ import annotations

from datetime import datetime, timezone
import collections
import json
import math
import os
import re
import subprocess
import threading
import time

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# State paths
# ---------------------------------------------------------------------------

PERF_EVENTS_FILE = _core.COMMAND_CENTER_STATE_DIR / "perf-events.jsonl"
PERF_TICKET_STATE_FILE = _core.COMMAND_CENTER_STATE_DIR / "perf-ticket-state.json"

# Test hook: point every state file under a tempdir instead of
# ~/.claude/command-center by setting this to a Path.
_STATE_DIR_OVERRIDE = None


def _events_path():
    if _STATE_DIR_OVERRIDE is not None:
        return _STATE_DIR_OVERRIDE / "perf-events.jsonl"
    return PERF_EVENTS_FILE


def _ticket_state_path():
    if _STATE_DIR_OVERRIDE is not None:
        return _STATE_DIR_OVERRIDE / "perf-ticket-state.json"
    return PERF_TICKET_STATE_FILE


# ---------------------------------------------------------------------------
# Thresholds (env-overridable; module-level so tests can monkeypatch too)
# ---------------------------------------------------------------------------


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


ARCHIVE_COLD_MS = _env_int("CCC_PERF_ARCHIVE_COLD_MS", 5000)
ARCHIVE_WARM_MS = _env_int("CCC_PERF_ARCHIVE_WARM_MS", 1000)
CONV_OPEN_MS = _env_int("CCC_PERF_CONV_OPEN_MS", 5000)
WARM_WINDOW_S = _env_int("CCC_PERF_WARM_WINDOW_S", 3600)

_VALID_KINDS = ("archive_load", "conv_open")
_TAIL_READ_BYTES = 2 * 1024 * 1024  # the JSONL sink grows forever; only tail this much
_OPEN_STATUSES = {"open", "in_progress", "claimed", "blocked"}


# ---------------------------------------------------------------------------
# Warm/cold classification
# ---------------------------------------------------------------------------

# Ledger of archive-cache build/refresh timestamps (epoch seconds). Fed by
# `_archive_response_cache_put` in server.py on every put, and seeded from
# the persisted cache's `cached_at` values when that cache loads from disk
# after a restart. "Warm" for a page load means: some build landed BEFORE
# the page started loading and within the warm window — a build the page
# itself triggered (t > page_start) must not count, otherwise every cold
# load would read as warm the instant its own scan finished.
_ARCHIVE_BUILD_TS = collections.deque(maxlen=256)
_ARCHIVE_BUILD_LOCK = threading.Lock()


def note_archive_build(ts=None):
    """Record that the archive response cache was (re)built at `ts`."""
    try:
        t = float(time.time() if ts is None else ts)
    except (TypeError, ValueError):
        return
    if t <= 0:
        return
    with _ARCHIVE_BUILD_LOCK:
        _ARCHIVE_BUILD_TS.append(t)


def archive_is_warm(page_start_ts=None, now=None):
    """True iff an archive build completed before `page_start_ts` and less
    than WARM_WINDOW_S earlier. Never raises."""
    try:
        now = time.time() if now is None else float(now)
        page_start = now if page_start_ts is None else float(page_start_ts)
        with _ARCHIVE_BUILD_LOCK:
            builds = list(_ARCHIVE_BUILD_TS)
        for t in reversed(builds):
            if t <= page_start and (page_start - t) < WARM_WINDOW_S:
                return True
        return False
    except Exception:
        return False


def _threshold_for(kind, warm):
    if kind == "archive_load":
        return ARCHIVE_WARM_MS if warm else ARCHIVE_COLD_MS
    return CONV_OPEN_MS


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record_event(kind, ms, boot_id="", conv_id="", detail=None):
    """Validate + persist one perf sample; returns the breach verdict.

    Raises ValueError on a bad kind or non-numeric/negative ms (the route
    handler turns that into a 400). All other failure modes (disk full,
    permission denied) are swallowed — a perf beacon must never itself
    become the thing that's slow or broken.
    """
    if kind not in _VALID_KINDS:
        raise ValueError(f"invalid kind: {kind!r}")
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        raise ValueError(f"non-numeric ms: {ms!r}")
    ms_f = float(ms)
    if not math.isfinite(ms_f) or ms_f < 0:
        raise ValueError(f"invalid ms: {ms!r}")

    warm = False
    if kind == "archive_load":
        # The page started at least `ms` ago (the placeholder clock), and
        # `since_nav_ms` (navigation -> now) is longer still when present.
        now = time.time()
        since_ms = ms_f
        if isinstance(detail, dict):
            try:
                nav = float(detail.get("since_nav_ms") or 0)
                if math.isfinite(nav) and nav > since_ms:
                    since_ms = nav
            except (TypeError, ValueError):
                pass
        warm = archive_is_warm(page_start_ts=now - since_ms / 1000.0, now=now)
    threshold_ms = _threshold_for(kind, warm)
    ms_int = int(round(ms_f))
    breach = ms_f >= threshold_ms

    row = {
        "ts": _iso_now(),
        "kind": kind,
        "ms": ms_int,
        "boot_id": str(boot_id or ""),
        "conv_id": str(conv_id or ""),
        "warm": warm,
        "threshold_ms": threshold_ms,
        "breach": breach,
        "detail": detail if isinstance(detail, dict) else None,
    }
    try:
        path = _events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass

    print(
        "[PERF] %s %s %dms %s %s"
        % (
            time.strftime("%H:%M:%S"),
            kind,
            ms_int,
            "warm" if warm else "cold",
            "BREACH" if breach else "ok",
        ),
        flush=True,
    )
    return {"ok": True, "warm": warm, "threshold_ms": threshold_ms, "breach": breach}


def _iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_events(since_ts):
    """Return JSONL rows with ts >= since_ts, reading only the file's tail.

    The sink is append-only and never rotated/truncated, so a full parse
    on every summary request would be an unbounded read on a long-running
    install. Seeking to the last ~2MB and discarding one partial leading
    line bounds the cost regardless of how big the file has grown.
    """
    path = _events_path()
    try:
        size = path.stat().st_size
    except OSError:
        return []

    events = []
    try:
        with open(path, "rb") as f:
            if size > _TAIL_READ_BYTES:
                f.seek(size - _TAIL_READ_BYTES)
                f.readline()  # discard the partial line the seek landed inside
            for raw in f:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts_epoch = _parse_iso(row.get("ts"))
                if ts_epoch is None or ts_epoch < since_ts:
                    continue
                events.append(row)
    except OSError:
        return events
    return events


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return 0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = int(round(pct / 100.0 * (len(sorted_vals) - 1)))
    idx = max(0, min(idx, len(sorted_vals) - 1))
    return sorted_vals[idx]


def _worst_rows(rows, limit=5):
    ordered = sorted(rows, key=lambda r: r.get("ms") or 0, reverse=True)[:limit]
    return [
        {
            "ts": r.get("ts"),
            "ms": r.get("ms"),
            "warm": bool(r.get("warm")),
            "conv_id": r.get("conv_id") or "",
            "boot_id": r.get("boot_id") or "",
        }
        for r in ordered
    ]


def _kind_stats(rows):
    ms_vals = sorted(int(r.get("ms") or 0) for r in rows)
    return {
        "count": len(rows),
        "p50": _percentile(ms_vals, 50),
        "p95": _percentile(ms_vals, 95),
        "max": ms_vals[-1] if ms_vals else 0,
        "breaches": sum(1 for r in rows if r.get("breach")),
        "worst": _worst_rows(rows),
    }


def summarize(hours=24, now=None):
    now = time.time() if now is None else now
    try:
        hours_i = int(hours)
    except (TypeError, ValueError):
        hours_i = 24
    hours_i = max(1, min(720, hours_i))
    since_ts = now - hours_i * 3600
    since_iso = datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )

    events = read_events(since_ts)
    kinds = {}
    for kind in _VALID_KINDS:
        rows = [e for e in events if e.get("kind") == kind]
        kinds[kind] = _kind_stats(rows)

    return {
        "hours": hours_i,
        "since": since_iso,
        "thresholds": {
            "archive_load_cold_ms": ARCHIVE_COLD_MS,
            "archive_load_warm_ms": ARCHIVE_WARM_MS,
            "conv_open_ms": CONV_OPEN_MS,
        },
        "kinds": kinds,
        "ticket": _ticket_summary(_load_ticket_state()),
    }


# ---------------------------------------------------------------------------
# Breach-pattern detection
# ---------------------------------------------------------------------------


def evaluate_breach_pattern(events):
    """Pick the worst kind that looks like a real regression, or None.

    Qualifies when a kind has >= 2 breach events in the window, OR any
    single sample is >= 2x its own threshold (one truly awful sample is
    as worth a ticket as several borderline ones). Among qualifying
    kinds, the one with the higher p95 wins.
    """
    best = None
    for kind in _VALID_KINDS:
        rows = [e for e in events if e.get("kind") == kind]
        if not rows:
            continue
        breach_rows = [r for r in rows if r.get("breach")]
        single_2x = any(
            (r.get("ms") or 0) >= 2 * (r.get("threshold_ms") or 1) for r in rows
        )
        if len(breach_rows) < 2 and not single_2x:
            continue
        stats = _kind_stats(rows)
        candidate = {"kind": kind, **stats}
        candidate.pop("breaches", None)
        if best is None or candidate["p95"] > best["p95"]:
            best = candidate
    return best


# ---------------------------------------------------------------------------
# Ticket state
# ---------------------------------------------------------------------------


def _load_ticket_state():
    try:
        raw = _ticket_state_path().read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_ticket_state(state):
    try:
        path = _ticket_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def _ticket_summary(state):
    return {
        "last_ref": state.get("last_ref"),
        "last_filed_date": state.get("last_filed_date"),
        "last_status": state.get("last_status"),
        "last_checked_at": state.get("last_checked_at"),
    }


# ---------------------------------------------------------------------------
# `wt` subprocess boundary — only ever called from the daemon thread, never
# from a request handler.
# ---------------------------------------------------------------------------

# Test hook: callable(args, timeout) -> (rc, stdout). None means "use the
# real wt CLI".
_WT_RUNNER = None


def _wt_run(args, timeout=20):
    if _WT_RUNNER is not None:
        try:
            return _WT_RUNNER(args, timeout)
        except Exception:
            return (1, "")
    try:
        wt_path = _core._wt_cli_path()
    except Exception:
        wt_path = ""
    if not wt_path:
        return (127, "")  # shell "command not found" convention
    try:
        proc = subprocess.run(
            [wt_path] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return (proc.returncode, proc.stdout or "")
    except Exception:
        return (1, "")


_TICKET_REF_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,15}-\d+)\b")


def refresh_ticket_status(state):
    """Update state['last_status'] from `wt find <ref> --json`, if any ref
    is on file. No-op (never raises) when there's no ref yet, or `wt` is
    unavailable/errors — the caller falls back to whatever status is
    already cached."""
    ref = state.get("last_ref")
    if not ref:
        return state
    rc, out = _wt_run(["find", ref, "--json"])
    if rc != 0 or not (out or "").strip():
        return state
    try:
        data = json.loads(out)
    except ValueError:
        return state
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return state
    status = data.get("status")
    if status:
        state["last_status"] = status
    state["last_checked_at"] = _iso_now()
    return state


def _build_note(pattern, state):
    lines = [
        "Thresholds: archive_load_cold_ms=%d archive_load_warm_ms=%d conv_open_ms=%d"
        % (ARCHIVE_COLD_MS, ARCHIVE_WARM_MS, CONV_OPEN_MS),
        "kind=%s count=%d p50=%sms p95=%sms max=%sms"
        % (pattern["kind"], pattern["count"], pattern["p50"], pattern["p95"], pattern["max"]),
        "",
        "Worst samples (ts  ms  warm/cold  conv_id):",
    ]
    for row in pattern.get("worst") or []:
        lines.append(
            "%s  %sms  %s  %s"
            % (
                row.get("ts") or "?",
                row.get("ms") or 0,
                "warm" if row.get("warm") else "cold",
                row.get("conv_id") or "-",
            )
        )
    last_ref = state.get("last_ref")
    last_status = str(state.get("last_status") or "").lower()
    if last_ref and last_status == "closed":
        lines.append("")
        lines.append(
            "Regression: %s was closed on %s but breaches continue."
            % (last_ref, state.get("last_filed_date") or "an earlier date")
        )
    return "\n".join(lines)


def perf_ticket_check_once(now=None):
    """One pass of the self-filing check. Returns a short status string;
    never raises (the daemon loop would otherwise die on the first bug)."""
    now = time.time() if now is None else now
    try:
        events = read_events(now - 24 * 3600)
        pattern = evaluate_breach_pattern(events)
        if pattern is None:
            return "ok"

        state = _load_ticket_state()
        state = refresh_ticket_status(state)

        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")  # local calendar day
        if state.get("last_filed_date") == today:
            return "already-filed-today"

        if str(state.get("last_status") or "").lower() in _OPEN_STATUSES:
            return "open-ticket-exists"

        kind_label = pattern["kind"].replace("_", " ")
        title = "[perf] slow %s: p95 %sms over %s samples (24h)" % (
            kind_label,
            pattern["p95"],
            pattern["count"],
        )
        note = _build_note(pattern, state)

        rc, out = _wt_run(
            [
                "add",
                "-q",
                "CCC",
                "--type",
                "bug",
                "--priority",
                "p1",
                "--title",
                title,
                "--note",
                note,
            ]
        )
        if rc == 127:
            return "wt-unavailable"
        if rc != 0:
            return "error"

        m = _TICKET_REF_RE.search(out or "")
        ref = m.group(1) if m else ""
        if not ref:
            return "error"

        state["last_ref"] = ref
        state["last_filed_date"] = today
        state["last_status"] = "open"
        state["last_checked_at"] = _iso_now()
        state["last_kind"] = pattern["kind"]
        _save_ticket_state(state)
        print(f"[PERF] filed {ref}", flush=True)
        return f"filed:{ref}"
    except Exception:
        return "error"


_PERF_TICKET_INITIAL_DELAY_S = 120


def _perf_ticket_interval_s():
    return _env_int("CCC_PERF_TICKET_CHECK_INTERVAL_S", 900)


def perf_ticket_loop():
    """Daemon thread target. Mirrors `_telemetry_open_beacon_loop` in
    fleet_jobs.py: sleep past dashboard paint, then loop forever, treating
    every failure mode as "try again next interval" rather than crashing
    the thread."""
    try:
        time.sleep(_PERF_TICKET_INITIAL_DELAY_S)
    except Exception:
        return
    while True:
        try:
            perf_ticket_check_once()
        except Exception:
            pass
        try:
            time.sleep(_perf_ticket_interval_s())
        except Exception:
            return
