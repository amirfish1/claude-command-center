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


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


ARCHIVE_COLD_MS = _env_int("CCC_PERF_ARCHIVE_COLD_MS", 5000)
ARCHIVE_WARM_MS = _env_int("CCC_PERF_ARCHIVE_WARM_MS", 1000)
CONV_OPEN_MS = _env_int("CCC_PERF_CONV_OPEN_MS", 5000)
WARM_WINDOW_S = _env_int("CCC_PERF_WARM_WINDOW_S", 3600)

# Process-start marker. Samples recorded during the post-restart startup
# storm (scratch-gc, lazy state loads, the detached archive-refresh
# subprocess all competing) measure boot contention, not steady-state perf —
# and archive_load ones also mislabel "warm" because the persisted response
# cache seeds _ARCHIVE_BUILD_TS, so a 9-13s first paint seconds after a
# restart met the 1000ms warm threshold and filed a p1 via the single-2x
# rule. That exact pattern produced CCC-1128, CCC-1138, and CCC-1159. Warmup
# samples are still recorded (the data is real) but flagged "warmup" and
# excluded from breach-pattern filing.
_PROCESS_STARTED_AT = time.time()
WARMUP_S = _env_int("CCC_PERF_WARMUP_S", 300)

# Machine-saturation attribution (CCC-1154 option B). The recurring p1 "slow
# archive load" stream (CCC-1128/1138/1159) was whole-machine saturation, not
# an archive regression: loadavg hit ~138 on a ~12-core box while every heavy
# poll queued behind the GIL. Each sample now records the 1-minute load and
# core count; breach rows measured while load1 >= SATURATED_LOAD_PER_CPU per
# core are downgraded — they no longer qualify a per-kind perf ticket. Two
# carve-outs keep the signal honest:
#   - a single sample >= _SINGLE_SAMPLE_BREACH_FACTOR x its threshold still
#     files, saturated or not (a truly extreme sample is real either way);
#   - downgraded breaches file/refresh ONE explicit "machine saturated"
#     ticket instead of silence, so the overload itself stays visible.
SATURATED_LOAD_PER_CPU = _env_float("CCC_PERF_SATURATED_LOAD_PER_CPU", 2.0)
_SINGLE_SAMPLE_BREACH_FACTOR = 3
# Gross saturation: load this far past the saturation line (per core) means
# every endpoint stalled at once (CCC-39: load1 ~173 on 8 cores, a 40s
# conv_open next to 60s /api/queue/context and 30s /api/repo/worktrees). No
# single sample is attributable to CCC then, so the 3x carve-out above does
# not apply — the row rolls into the "machine saturated" alert instead.
GROSS_SATURATION_LOAD_PER_CPU = _env_float("CCC_PERF_GROSS_SATURATION_LOAD_PER_CPU", 8.0)
# Minimum gap between "still saturated" comments on an already-open
# saturation ticket — one refresh per few hours, not one per check cycle.
SAT_COMMENT_MIN_INTERVAL_S = _env_int("CCC_PERF_SAT_COMMENT_INTERVAL_S", 6 * 3600)


def _machine_load():
    """(load1, ncpu) right now; (None, None) when unavailable."""
    try:
        load1 = float(os.getloadavg()[0])
    except (OSError, AttributeError, IndexError, TypeError, ValueError):
        load1 = None
    try:
        ncpu = int(os.cpu_count() or 0) or None
    except (TypeError, ValueError):
        ncpu = None
    return load1, ncpu


def _load_saturated(load1, ncpu):
    try:
        if load1 is None or ncpu is None:
            return False
        return float(load1) >= SATURATED_LOAD_PER_CPU * float(ncpu)
    except (TypeError, ValueError):
        return False


def _row_grossly_saturated(row):
    try:
        load1, ncpu = row.get("load1"), row.get("ncpu")
        if load1 is None or ncpu is None:
            return False
        return float(load1) >= GROSS_SATURATION_LOAD_PER_CPU * float(ncpu)
    except (TypeError, ValueError):
        return False


def _row_saturated(row):
    """True when a recorded event row was measured under machine saturation.
    Rows without load data (pre-instrumentation, or platforms with no
    getloadavg) read as not saturated — fail toward filing, same as before."""
    return _load_saturated(row.get("load1"), row.get("ncpu"))


def machine_saturated():
    """True when the box's 1-minute load is >= SATURATED_LOAD_PER_CPU per
    core. server.py uses this to gate the detached archive-refresh spawn —
    never raises."""
    try:
        load1, ncpu = _machine_load()
        return _load_saturated(load1, ncpu)
    except Exception:
        return False


def _in_warmup(now=None):
    """True while the process sits inside its post-boot warmup window."""
    try:
        t = time.time() if now is None else float(now)
    except (TypeError, ValueError):
        return False
    return (t - _PROCESS_STARTED_AT) < WARMUP_S


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

    now = time.time()
    warmup = _in_warmup(now)
    warm = False
    if kind == "archive_load":
        # The page started at least `ms` ago (the placeholder clock), and
        # `since_nav_ms` (navigation -> now) is longer still when present.
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
    load1, ncpu = _machine_load()

    row = {
        "ts": _iso_now(),
        "kind": kind,
        "ms": ms_int,
        "boot_id": str(boot_id or ""),
        "conv_id": str(conv_id or ""),
        "warm": warm,
        "warmup": warmup,
        "load1": load1,
        "ncpu": ncpu,
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
    return {
        "ok": True,
        "warm": warm,
        "warmup": warmup,
        "saturated": _load_saturated(load1, ncpu),
        "threshold_ms": threshold_ms,
        "breach": breach,
    }


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

    Qualifies when a kind has >= 2 breach events measured while the machine
    was NOT saturated, OR any single sample is >= 3x its own threshold (one
    truly awful sample is worth a ticket even under saturation). Among
    qualifying kinds, the one with the higher p95 wins.

    Warmup-flagged rows (recorded inside the post-boot window) never
    qualify: a giant first-paint outlier during startup is expected
    contention, and counting it here refiles the same false-positive
    ticket on every restart. Saturated breach rows are likewise downgraded:
    they measure machine contention, not CCC perf — _saturation_ticket_check
    surfaces those separately as one "machine saturated" alert.
    """
    best = None
    for kind in _VALID_KINDS:
        rows = [
            e for e in events if e.get("kind") == kind and not e.get("warmup")
        ]
        if not rows:
            continue
        breach_rows = [
            r for r in rows if r.get("breach") and not _row_saturated(r)
        ]
        single_3x = any(
            (r.get("ms") or 0)
            >= _SINGLE_SAMPLE_BREACH_FACTOR * (r.get("threshold_ms") or 1)
            and not _row_grossly_saturated(r)
            for r in rows
        )
        if len(breach_rows) < 2 and not single_3x:
            continue
        stats = _kind_stats(rows)
        candidate = {"kind": kind, **stats}
        candidate.pop("breaches", None)
        if best is None or candidate["p95"] > best["p95"]:
            best = candidate
    return best


def evaluate_saturation(events):
    """Summarize breach samples downgraded by machine saturation, or None.

    Runs only when evaluate_breach_pattern found nothing to file: breach
    rows recorded while the box was saturated are real symptoms and must
    not vanish silently — they roll up into the single "machine saturated"
    alert instead of a per-kind perf ticket.
    """
    saturated = [
        e
        for e in events
        if e.get("breach") and not e.get("warmup") and _row_saturated(e)
    ]
    if len(saturated) < 2 and not any(_row_grossly_saturated(r) for r in saturated):
        return None
    kind_counts = {}
    max_load1 = 0.0
    ncpu = None
    for row in saturated:
        kind_counts[row.get("kind")] = kind_counts.get(row.get("kind"), 0) + 1
        try:
            max_load1 = max(max_load1, float(row.get("load1") or 0))
        except (TypeError, ValueError):
            pass
        if ncpu is None and row.get("ncpu"):
            try:
                ncpu = int(row["ncpu"])
            except (TypeError, ValueError):
                pass
    return {
        "count": len(saturated),
        "kinds": kind_counts,
        "max_load1": max_load1,
        "ncpu": ncpu,
        "worst": _worst_rows(saturated),
    }


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


# `wt add` prints "FILED: <ref>" and then dispatches a worker for the new
# ticket (may spawn a whole session), so it can run well past the point the
# ticket exists. Give it a long leash, and on timeout still return whatever
# stdout was produced so the caller can recover the ref — otherwise a
# created-but-unrecorded ticket would be refiled every check (observed on
# the very first live run: CCC ticket filed, 20s timeout, no state saved).
_WT_ADD_TIMEOUT_S = 180
_WT_TIMEOUT_RC = 124  # coreutils `timeout` convention


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
        return (proc.returncode, (proc.stdout or "") + "\n" + (proc.stderr or ""))
    except subprocess.TimeoutExpired as e:
        partial = e.stdout or b""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="ignore")
        return (_WT_TIMEOUT_RC, partial)
    except Exception:
        return (1, "")


_TICKET_REF_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,15}-\d+)\b")
_FILED_REF_RE = re.compile(r"FILED:\s*([A-Z][A-Z0-9]{1,15}-\d+)")


def _ref_status(ref):
    """Best-effort `wt find <ref> --json` status lookup. Returns the status
    string ('' when the ticket reports none), or None when the lookup itself
    failed — distinguishing "open" from "wt unreachable" matters to callers
    deciding between refresh and refile."""
    if not ref:
        return None
    rc, out = _wt_run(["find", ref, "--json"])
    if rc != 0 or not (out or "").strip():
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return None
    return str(data.get("status") or "").lower()


def refresh_ticket_status(state):
    """Update state['last_status'] from `wt find <ref> --json`, if any ref
    is on file. No-op (never raises) when there's no ref yet, or `wt` is
    unavailable/errors — the caller falls back to whatever status is
    already cached."""
    status = _ref_status(state.get("last_ref"))
    if status is None:
        return state
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


def _build_saturation_note(sat):
    lines = [
        "Perf breach samples were recorded while the machine itself was",
        "saturated (load1 >= %.1f per core). The per-kind slow-load ticket was"
        % SATURATED_LOAD_PER_CPU,
        "downgraded into this single alert so the overload stays visible",
        "without refiling the false-positive slow-archive stream (CCC-1154).",
        "",
        "peak load1=%.1f  ncpu=%s  saturated breach samples (1h)=%d"
        % (sat["max_load1"], sat["ncpu"] or "?", sat["count"]),
        "kinds: %s"
        % "  ".join(
            "%s=%d" % (kind, sat["kinds"].get(kind, 0)) for kind in _VALID_KINDS
        ),
        "",
        "Worst samples (ts  ms  warm/cold  conv_id):",
    ]
    for row in sat.get("worst") or []:
        lines.append(
            "%s  %sms  %s  %s"
            % (
                row.get("ts") or "?",
                row.get("ms") or 0,
                "warm" if row.get("warm") else "cold",
                row.get("conv_id") or "-",
            )
        )
    lines.append("")
    lines.append(
        "Raw samples are still recorded with load1/ncpu; any single sample "
        "over %dx its threshold still files a normal perf ticket, unless load1 "
        "was >= %.0f per core (gross saturation)."
        % (_SINGLE_SAMPLE_BREACH_FACTOR, GROSS_SATURATION_LOAD_PER_CPU)
    )
    return "\n".join(lines)


def _saturation_ticket_check(events, now):
    """File or refresh the single machine-saturation alert.

    Runs only when no kind qualified for a normal perf ticket: breaches
    downgraded by machine saturation still need ONE explicit alert so the
    overload stays visible. An already-open saturation ticket gets a
    throttled 'still saturated' comment instead of a duplicate filing.
    Never raises (daemon-thread caller).
    """
    try:
        sat = evaluate_saturation(events)
        if sat is None:
            return "ok"
        state = _load_ticket_state()
        sat_ref = state.get("sat_ref")
        if sat_ref:
            status = _ref_status(sat_ref)
            if status is not None:
                state["sat_status"] = status
                state["sat_checked_at"] = _iso_now()
                _save_ticket_state(state)
            # Fall back to the cached status when `wt find` is unreachable —
            # refiling while a live ticket exists is the worse failure.
            effective = (
                status
                if status is not None
                else str(state.get("sat_status") or "").lower()
            )
            if effective in _OPEN_STATUSES:
                if status is not None:
                    last_comment = float(state.get("sat_last_comment_ts") or 0)
                    if now - last_comment >= SAT_COMMENT_MIN_INTERVAL_S:
                        text = (
                            "still saturated: peak load %.1f/ncpu %s; "
                            "%d downgraded breach samples in the last hour"
                            % (sat["max_load1"], sat["ncpu"] or "?", sat["count"])
                        )
                        _wt_run(["comment", sat_ref, text, "--by", "system"])
                        state["sat_last_comment_ts"] = now
                        _save_ticket_state(state)
                return f"saturated-open:{sat_ref}"
        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        if state.get("sat_filed_date") == today:
            return "saturated-filed-today"

        title = "[perf] machine saturated: load %.1f/ncpu %s" % (
            sat["max_load1"],
            sat["ncpu"] or "?",
        )
        rc, out = _wt_run(
            [
                "add",
                "-q",
                "CCC",
                "--type",
                "bug",
                # p2, not p1: the machine being overloaded is worth one
                # visible ticket, but it is not a CCC perf regression.
                "--priority",
                "p2",
                "--title",
                title,
                "--note",
                _build_saturation_note(sat),
            ],
            timeout=_WT_ADD_TIMEOUT_S,
        )
        if rc == 127:
            return "wt-unavailable"

        m = _FILED_REF_RE.search(out or "") or _TICKET_REF_RE.search(out or "")
        ref = m.group(1) if m else ""
        if not ref and rc != 0 and rc != _WT_TIMEOUT_RC:
            return "error"

        state["sat_ref"] = ref or state.get("sat_ref")
        state["sat_filed_date"] = today
        state["sat_status"] = "open"
        state["sat_checked_at"] = _iso_now()
        state["sat_last_comment_ts"] = now
        _save_ticket_state(state)
        print(f"[PERF] filed saturation alert {ref or '?'} (rc={rc})", flush=True)
        return f"saturated-filed:{ref or '?'}"
    except Exception:
        return "error"


def perf_ticket_check_once(now=None):
    """One pass of the self-filing check. Returns a short status string;
    never raises (the daemon loop would otherwise die on the first bug)."""
    now = time.time() if now is None else now
    try:
        # The 24-hour window supplies useful ticket context, but must not
        # resurrect an already-recovered incident after its old outliers keep
        # the aggregate above threshold. A ticket is actionable only when a
        # qualifying pattern is still present in the last hour.
        recent_events = read_events(now - _PERF_TICKET_RECENCY_S)
        recent_pattern = evaluate_breach_pattern(recent_events)
        if recent_pattern is None:
            return _saturation_ticket_check(recent_events, now)

        events = read_events(now - 24 * 3600)
        rows = [
            event
            for event in events
            if event.get("kind") == recent_pattern["kind"]
            and not event.get("warmup")
        ]
        if not rows:
            return "ok"
        pattern = {"kind": recent_pattern["kind"], **_kind_stats(rows)}
        pattern.pop("breaches", None)

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
            ],
            timeout=_WT_ADD_TIMEOUT_S,
        )
        if rc == 127:
            return "wt-unavailable"

        m = _FILED_REF_RE.search(out or "") or _TICKET_REF_RE.search(out or "")
        ref = m.group(1) if m else ""
        if not ref and rc != 0 and rc != _WT_TIMEOUT_RC:
            # Clean failure, nothing printed: safe to retry next check.
            return "error"

        # A ref, or a timeout / odd exit AFTER wt may have created the ticket:
        # arm the same-day dedupe either way. Refiling is the worse failure.
        state["last_ref"] = ref or state.get("last_ref")
        state["last_filed_date"] = today
        state["last_status"] = "open"
        state["last_checked_at"] = _iso_now()
        state["last_kind"] = pattern["kind"]
        _save_ticket_state(state)
        print(f"[PERF] filed {ref or '?'} (rc={rc})", flush=True)
        return f"filed:{ref or '?'}"
    except Exception:
        return "error"


_PERF_TICKET_INITIAL_DELAY_S = 120
_PERF_TICKET_RECENCY_S = 60 * 60


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
