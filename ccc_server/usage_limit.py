"""Extracted from server.py (originally lines 41720-44651).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import base64
import fcntl
import federation
import json
import os
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from ccc_server import core as _core

# Usage-limit auto-resume (CCC-863)
#
# When a claude/codex/kimi session stops because it hit a usage/rate-limit
# wall, detect it, compute the reset time, show a countdown, and send the
# literal message "continue" the moment the limit clears -- fully
# unattended, no confirmation dialog (explicit product decision; no manual
# fallback button by design).
#
# Shared "does this look like an exhaustion stop" regex -- kept byte-for-byte
# in sync with _resultOutcomeInfo()'s `exhausted` pattern in static/app.js
# (~line 45665) so client and server never disagree about what counts.
# ---------------------------------------------------------------------------
_USAGE_LIMIT_EXHAUSTED_RE = re.compile(
    r"(rate.?limit|usage.?limit|quota|exhaust|insufficient.?quota|"
    r"resource_exhausted|no tokens?\b|out of (tokens|credit)|credit balance|"
    r"reached your[^.]*limit|429|too many requests|subscription)",
    re.IGNORECASE,
)
_ROLLOUT_SID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)

_usage_limit_resume_lock = threading.Lock()
_usage_limit_resume_cache = {"data": None}


def _load_usage_limit_resumes():
    """Return {session_id: entry} from the durable resume-tracking file.

    In-memory cache; refreshed lazily on every write (own or a sibling
    process's, via the file). Tolerant of a missing/malformed file (both
    yield {})."""
    with _core._usage_limit_resume_lock:
        cached = _core._usage_limit_resume_cache["data"]
        if cached is not None:
            return cached
    try:
        data = (
            json.loads(_core.USAGE_LIMIT_RESUME_FILE.read_text())
            if _core.USAGE_LIMIT_RESUME_FILE.exists() else {}
        )
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    with _core._usage_limit_resume_lock:
        _core._usage_limit_resume_cache["data"] = data
    return data


def _is_session_auto_resume_disabled(session_id):
    """Read the durable per-session kill switch without trusting a cache.

    This check is only used for the exact unattended ``continue`` marker, so
    the extra tiny JSON read is preferable to letting a sibling process's
    stale in-memory opt-in resurrect a cancelled session.
    """
    sid = str(session_id or "").strip()
    if sid.startswith("session_"):
        sid = sid[len("session_"):]
    if not sid:
        return True
    if not _core.USAGE_LIMIT_RESUME_FILE.exists():
        return False
    try:
        data = json.loads(_core.USAGE_LIMIT_RESUME_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(data, dict):
        return True
    entry = data.get(sid)
    return bool(
        isinstance(entry, dict)
        and (entry.get("auto_resume_disabled") or entry.get("dismissed"))
    )


def _usage_limit_resume_lock_path():
    return _core.USAGE_LIMIT_RESUME_FILE.with_suffix(".lock")


def _usage_limit_resume_rewrite(mutate):
    """Read-modify-write the durable store under an flock (cross-process
    safe -- both the dashboard and worker process run the watcher). `mutate`
    takes the current dict and returns (new_dict, retval)."""
    lock_path = _usage_limit_resume_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            try:
                existing = (
                    json.loads(_core.USAGE_LIMIT_RESUME_FILE.read_text())
                    if _core.USAGE_LIMIT_RESUME_FILE.exists() else {}
                )
            except (OSError, json.JSONDecodeError):
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            new_data, retval = mutate(existing)
            tmp = _core.USAGE_LIMIT_RESUME_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(new_data, indent=2))
            os.replace(tmp, _core.USAGE_LIMIT_RESUME_FILE)
            with _core._usage_limit_resume_lock:
                _core._usage_limit_resume_cache["data"] = new_data
            return retval
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def _save_usage_limit_resume_entry(session_id, entry):
    """Merge `entry` into the durable store under `session_id`."""
    if not session_id:
        return

    def _mutate(existing):
        current = existing.get(str(session_id))
        if isinstance(current, dict) and (
            current.get("auto_resume_disabled") or current.get("dismissed")
        ):
            # A detector may have computed `entry` from a snapshot taken
            # before the user's X. Permanent disable wins atomically.
            return existing, None
        existing[str(session_id)] = entry
        return existing, None

    _usage_limit_resume_rewrite(_mutate)


def _mark_usage_limit_resume_fired(session_id):
    """Atomically flip an entry's `fired` flag True. Returns True only for
    the caller that actually made the transition -- the guard that prevents
    two watcher threads (or two processes) from double-sending "continue"
    for the same detected stop."""
    if not session_id:
        return False

    def _mutate(existing):
        entry = existing.get(str(session_id))
        if not entry or entry.get("fired"):
            return existing, False
        entry["fired"] = True
        entry["fired_at"] = time.time()
        existing[str(session_id)] = entry
        return existing, True

    return _usage_limit_resume_rewrite(_mutate)


def _clear_usage_limit_resume(session_id):
    """Drop a tracked entry (fired-and-done, or superseded by fresh
    activity showing the session already moved on without our help)."""
    if not session_id:
        return

    def _mutate(existing):
        existing.pop(str(session_id), None)
        return existing, None

    _usage_limit_resume_rewrite(_mutate)


def _dismiss_usage_limit_resume(session_id):
    """User hit the banner's cancel (x): mark the tracked stop dismissed
    rather than deleting it outright (CCC-887). The transcript that
    triggered detection doesn't change, so a plain delete let the next
    _usage_limit_scan_once pass re-detect the same stop and re-arm the
    countdown -- the banner "kept coming back" after being dismissed.
    Keeping a dismissed marker around (until the candidate window ages it
    out) suppresses re-detection for that stop."""
    if not session_id:
        return

    def _mutate(existing):
        entry = existing.get(str(session_id))
        if entry is None:
            entry = {}
        entry["dismissed"] = True
        entry["auto_resume_disabled"] = True
        entry["dismissed_at"] = time.time()
        existing[str(session_id)] = entry
        return existing, None

    _usage_limit_resume_rewrite(_mutate)


def _purge_pending_auto_resume_handoffs(session_id):
    """Remove not-yet-ingested bare-continue handoffs for one session."""
    removed = 0
    with _core._pending_input_handoff_ingest_lock:
        try:
            paths = list(_core.PENDING_INPUT_HANDOFF_DIR.glob("*.json"))
        except OSError:
            return removed
        for path in paths:
            event = _core._read_pending_input_handoff(path)
            if not event or event["session_id"] != session_id:
                continue
            if not _core._is_unattended_auto_continue(event["text"]):
                continue
            path.unlink(missing_ok=True)
            _core._pending_terminal_handoff_ids.pop(event["id"], None)
            removed += 1
    return removed


def _disable_session_auto_resume(session_id):
    """Permanently disable unattended auto-resume for one session.

    Clearing the durable opt-in prevents future bare ``continue`` pokes;
    purging any already-queued copies closes the race where one was accepted
    before the user clicked X.  The usage-limit dismissal keeps the watcher
    from rebuilding a countdown for the same (or a later) stop in this
    session.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}

    try:
        with _core._codex_queue_pump_lock(sid), \
             _core._auto_resume_exclusive_lock():
            # Persist the negative marker first. Every queue/write/delivery
            # path rechecks it while holding this same barrier.
            _core._dismiss_usage_limit_resume(sid)
            removed = _purge_pending_auto_resume_handoffs(sid)
            with _core._auto_resume_opt_in_lock:
                _core._auto_resume_opt_in.pop(sid, None)

            handoff_cleanup_failed = False
            for queue, lock in (
                (_core._pending_resume_queue, _core._pending_resume_lock),
                (_core._pending_terminal_input_queue, _core._pending_terminal_input_lock),
            ):
                with lock:
                    items = queue.get(sid) or []
                    kept = []
                    for item in items:
                        if not _core._is_unattended_auto_continue(item):
                            kept.append(item)
                            continue
                        if (
                            isinstance(item, _core._PendingInputHandoff)
                            and not _core._complete_pending_input_handoff(item)
                        ):
                            kept.append(item)
                            handoff_cleanup_failed = True
                            continue
                        removed += 1
                    if kept:
                        queue[sid] = kept
                    else:
                        queue.pop(sid, None)

            if not _core._save_pending_inputs(
                {sid}, include_auto_resume=True,
            ):
                return {
                    "ok": False,
                    "error": "failed to persist auto-resume disable",
                }
            if handoff_cleanup_failed:
                return {
                    "ok": False,
                    "error": "failed to remove queued auto-resume handoff",
                }
    except OSError:
        return {
            "ok": False,
            "error": "failed to persist permanent auto-resume disable",
        }
    return {
        "ok": True,
        "session_id": sid,
        "cancelled_queued": removed,
    }


def usage_limit_resume_at_for_session(session_id):
    """Cheap in-memory lookup for the row-serialization path: the epoch a
    stopped session will auto-resume at, or None if it isn't tracked, is
    already fired, or the time has passed (stale defensive cutoff so a
    countdown never sits frozen forever if the watcher missed a beat).

    Row session_id for kimi always carries CCC's own "session_" display
    prefix (e.g. "session_1a72..."), but the durable store's kimi entries
    are keyed by the bare id (matching the kimi cache filename convention
    stripped in _usage_limit_kimi_candidates / rebuilt in
    _usage_limit_session_path) -- strip it here so the two agree. Codex/
    claude ids never carry this prefix, so this is a no-op for them."""
    sid = str(session_id or "")
    if sid.startswith("session_"):
        sid = sid[len("session_"):]
    entry = _core._load_usage_limit_resumes().get(sid)
    if not entry or entry.get("fired") or entry.get("dismissed"):
        return None
    resume_at = entry.get("resume_at")
    if not isinstance(resume_at, (int, float)):
        return None
    if time.time() - resume_at > 3600:
        return None
    return resume_at


def _attach_usage_limit_resume_fields(rows):
    """Mutate `rows` in place, adding usage_limit_resume_at to any row whose
    session is tracked as parked on a usage-limit auto-resume. Cheap: one
    in-memory dict lookup per row, no file I/O (see
    usage_limit_resume_at_for_session)."""
    if not rows:
        return
    tracked = _core._load_usage_limit_resumes()
    if not tracked:
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id") or row.get("id")
        if not sid:
            continue
        resume_at = _core.usage_limit_resume_at_for_session(sid)
        if resume_at:
            row["usage_limit_resume_at"] = resume_at


def _tail_read_lines(path, max_bytes=32768):
    """Read the last `max_bytes` of `path` and return complete lines (the
    first partial line, if any, is dropped)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            raw = f.read()
    except OSError:
        return []
    text = raw.decode("utf-8", "replace")
    lines = text.split("\n")
    if len(lines) > 1 and size > max_bytes:
        lines = lines[1:]  # drop the (likely truncated) first partial line
    return [ln for ln in (l.strip() for l in lines) if ln]


# ---------------------------------------------------------------------------
# Usage-limit detection still arms a countdown banner. Unattended auto-send
# is disabled: the scan pass must not inject "continue" or spawn a
# continuation session. Manual Continue / Continue-in-a-new-session (F2)
# is unchanged. Helpers below stay so the banner can show origin
# model/effort/cwd if the user resumes by hand.
# ---------------------------------------------------------------------------
_USAGE_LIMIT_CONTEXT_THRESHOLD = 120_000

_USAGE_LIMIT_ENGINE_LOCATE = {
    "claude": (
        "Claude",
        "Its transcript is a JSONL under ~/.claude/projects/. Locate it with:",
        lambda sid: f"  ls ~/.claude/projects/*/{sid}.jsonl",
    ),
    "codex": (
        "Codex",
        "Its transcript is a rollout JSONL under ~/.codex/sessions/. Locate it with:",
        lambda sid: f'  find ~/.codex/sessions -name "*{sid}.jsonl"',
    ),
    "kimi": (
        "Kimi",
        "Its transcript path is recorded in ~/.kimi-code/session_index.jsonl. Locate it with:",
        lambda sid: f"  grep {sid} ~/.kimi-code/session_index.jsonl",
    ),
}


def _usage_limit_row_for_session(sid, engine=None):
    """One-shot conversation-row lookup by session id, from the same cached
    archive rows every other view reads (no fresh scan). Rare call -- once
    per newly-detected exhaustion stop, not a hot path. Kimi rows carry
    CCC's own "session_" display prefix on session_id (see CCC-861's
    lookup-side normalization fix); codex/claude ids never do -- try both
    so this works regardless of engine."""
    try:
        rows, _ = _core._archive_all_rows_cached({})
    except Exception:
        return None
    sid = str(sid or "")
    candidates = {sid, "session_" + sid}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("session_id") or r.get("id") or "")
        if rid in candidates:
            return r
    return None


def _usage_limit_context_tokens(engine, path, row):
    """Best-available "current context size" estimate. Claude/codex rows
    already carry a live per-turn figure (live_context_tokens /
    latest_input_tokens -- the same fields the composer's own large-context
    gate reads, static/app.js ~2767 _contextFieldsFromRow). Kimi never
    populates those (its tail meta only tracks turn-shape, not tokens) --
    fall back to the last assistant event's own token_usage on the same
    normalized cache file already tailed for detection, which is exactly
    what feeds each message's token chip (_apply_kimi_turn_usage)."""
    if engine == "kimi":
        for line in reversed(_tail_read_lines(path)):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "assistant":
                continue
            usage = ev.get("token_usage")
            if isinstance(usage, dict):
                return (
                    int(usage.get("input_tokens") or 0)
                    + int(usage.get("cache_read_input_tokens") or 0)
                    + int(usage.get("cache_creation_input_tokens") or 0)
                )
            tin = ev.get("tokens_in")
            if isinstance(tin, (int, float)):
                return int(tin)
        return int((row or {}).get("lifetime_tokens") or 0)
    return max(
        int((row or {}).get("live_context_tokens") or 0),
        int((row or {}).get("latest_input_tokens") or 0),
    )


def _usage_limit_attach_continuation_fields(found, session_id, path):
    """Called once at detection time by each _detect_*_usage_limit_stop, so
    the fire-time decision (in-place "continue" vs spawn a new session) and
    the new session's own model/effort/cwd never need a second, possibly
    stale, row lookup hours later when resume_at finally arrives."""
    engine = found.get("engine")
    row = _usage_limit_row_for_session(session_id, engine=engine) or {}
    found["context_tokens"] = _usage_limit_context_tokens(engine, path, row)
    found["model"] = row.get("model") or None
    found["effort"] = row.get("reasoning_effort") or None
    found["cwd"] = row.get("session_cwd") or row.get("folder_path") or None
    found["display_name"] = row.get("display_name") or row.get("ai_title") or None
    return found


def _usage_limit_retrieval_prompt(engine, sid, context_tokens):
    """Python port of f2RetrievalPrompt's large-context branch (static/
    app.js ~3058) -- must run unattended with no browser, so this can't
    call the client's own function. Only that one branch is needed: this
    is only ever called once context_tokens has already cleared
    _USAGE_LIMIT_CONTEXT_THRESHOLD, so worthSelectiveRetrieval is always
    true here. Kept in sync with the JS version by hand."""
    label, note, cmd_fn = _USAGE_LIMIT_ENGINE_LOCATE.get(
        engine, _USAGE_LIMIT_ENGINE_LOCATE["claude"]
    )
    tokens_label = f"{context_tokens / 1000:.0f}k" if context_tokens >= 1000 else str(context_tokens)
    lines = [
        f"You are continuing a task from an earlier {label} session, which ran long.",
        "",
        f"Origin session id: {sid}",
        note,
        cmd_fn(sid),
        "",
        "Task: Continue the work from where it left off. This session was",
        "auto-resumed after hitting a usage-limit wall; there is no new",
        "instruction beyond continuing.",
        "",
        "Retrieve context SELECTIVELY. Never open or Read the whole transcript",
        f"(it is ~{tokens_label} tokens). Pull only the slice you need:",
        '  - Total Recall:  total-recall recall --query "<terms>" --limit 10',
        "  - grep/rg the transcript for specific strings",
        "  - tail the transcript for the most recent turns",
        "",
        "Load the minimum slice that answers the task, then proceed.",
    ]
    return "\n".join(lines)


def _usage_limit_spawn_continuation(entry, sid):
    """Server-side equivalent of the manual "Continue in a new session"
    button's spawn (f2RunContinue, static/app.js ~3175) for the unattended
    auto-resume path. Same model/effort as the origin session (captured at
    detection time by _usage_limit_attach_continuation_fields), same cwd,
    parent_session_id set for continuation lineage."""
    engine = entry.get("engine")
    prompt = _usage_limit_retrieval_prompt(engine, sid, entry.get("context_tokens") or 0)
    cwd = entry.get("cwd")
    name = "Continue " + str(entry.get("display_name") or sid)[:60]
    model = entry.get("model") or None
    effort = entry.get("effort") or None
    kwargs = dict(name=name, cwd=cwd, repo_path=cwd, parent_session_id=sid, model=model)
    if engine == "kimi":
        return _core.spawn_session_kimi(prompt, effort=effort, **kwargs)
    if engine == "codex":
        return _core.spawn_session_codex(prompt, reasoning_effort=effort or "", **kwargs)
    if engine == "claude":
        return _core.spawn_session(prompt, reasoning_effort=effort or "", **kwargs)
    return None


def _detect_kimi_usage_limit_stop(session_id, path):
    """Tail `path` (CCC's normalized kimi cache, session_<id>.jsonl) for the
    most recent exhaustion stop. Real fixture confirmed on this machine:
    session_15de1a69-ee07-4a22-ba60-63561a5d544f.jsonl line 271. Kimi's own
    error text carries no parseable reset time ("refreshed in the next
    cycle") -- but CCC already tracks the real one, zero-config, via the
    same usages-API mechanism that powers the Throughput page's Kimi
    provider (_read_kimi_usage(), ccc_server/recall_usage.py): it reads the
    access token kimi-code CLI already manages at
    ~/.kimi-code/credentials/kimi-code.json (no separate setup), fetches
    https://api.kimi.com/coding/v1/usages, and falls back to the last
    persisted usage-snapshots.jsonl entry on failure. `session` in its
    return shape IS the 5h (300-minute) window with a precise `resets_at`.
    Prefer that; only fall back to stop_ts+5h if the fetch/cache fails or
    returns a resets_at that isn't even after the stop (a stale snapshot
    from a PRIOR exhaustion cycle, not this one)."""
    for line in reversed(_tail_read_lines(path)):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "result":
            continue
        if ev.get("subtype") != "error":
            return None  # most recent result was a clean stop; not exhausted
        err = str(ev.get("error") or "")
        if not _USAGE_LIMIT_EXHAUSTED_RE.search(err):
            return None
        dt = _core._stats_parse_ts(ev.get("ts"))
        if dt is None:
            return None
        detected_at = dt.timestamp()
        resume_at = detected_at + 5 * 3600
        estimated = True
        try:
            session_window = (_core._read_kimi_usage() or {}).get("session") or {}
            resume_dt = _core._stats_parse_ts(session_window.get("resets_at"))
            if resume_dt is not None and resume_dt.timestamp() > detected_at:
                resume_at = resume_dt.timestamp()
                estimated = False
        except Exception:
            pass
        return _usage_limit_attach_continuation_fields({
            "engine": "kimi",
            "detected_at": detected_at,
            "resume_at": resume_at,
            "resume_at_estimated": estimated,
            "source_text_snippet": err[:200],
        }, session_id, path)
    return None


def _detect_codex_usage_limit_stop(session_id, path):
    """Tail a Codex rollout.jsonl for the `task_complete` shape Codex emits
    on usage-limit exhaustion:
      {"type":"event_msg","payload":{"type":"task_complete",
       "last_agent_message":null,
       "error":{"message":"...","codex_error_info":"usage_limit_exceeded"}}}
    Confirmed against real fixtures on this machine (CCC-863 research; see
    rollout-2026-08-06T17-13-36-*.jsonl). resume_at prefers the nearest
    preceding `rate_limits.primary.resets_at` (primary = the ~5h short
    window, window_minutes==300; secondary = weekly, window_minutes==10080
    -- confirmed from a real populated example). That field is already a
    Unix epoch. When both primary/secondary are null (seen for a
    credits-exhausted account on this machine, no window to read), fall
    back to detected_at + 5h, flagged estimated."""
    lines = _tail_read_lines(path)
    last_rate_limits = None
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        ptype = payload.get("type")
        if ptype == "token_count":
            rl = payload.get("rate_limits")
            if isinstance(rl, dict):
                last_rate_limits = rl
            continue
        if ptype != "task_complete":
            continue
        if payload.get("last_agent_message") is not None:
            last_rate_limits = None
            continue  # a completed turn with real output supersedes any stop
        err = payload.get("error")
        if not isinstance(err, dict):
            last_rate_limits = None
            continue
        info = str(err.get("codex_error_info") or "")
        msg = str(err.get("message") or "")
        if info != "usage_limit_exceeded" and not _USAGE_LIMIT_EXHAUSTED_RE.search(msg):
            last_rate_limits = None
            continue
        dt = _core._stats_parse_ts(ev.get("timestamp"))
        detected_at = dt.timestamp() if dt is not None else time.time()
        resume_at = None
        estimated = True
        primary = (last_rate_limits or {}).get("primary") or {}
        secondary = (last_rate_limits or {}).get("secondary") or {}
        if isinstance(primary.get("resets_at"), (int, float)):
            resume_at = float(primary["resets_at"])
            estimated = False
        elif isinstance(secondary.get("resets_at"), (int, float)):
            resume_at = float(secondary["resets_at"])
            estimated = False
        if resume_at is None:
            resume_at = detected_at + 5 * 3600
        return _usage_limit_attach_continuation_fields({
            "engine": "codex",
            "detected_at": detected_at,
            "resume_at": resume_at,
            "resume_at_estimated": estimated,
            "source_text_snippet": (msg or info)[:200],
        }, session_id, path)
    return None


def _detect_claude_usage_limit_stop(session_id, path):
    """Tail a Claude Code transcript for a rate/usage-limit stop.

    No local example of a terminal stop event existed on this machine when
    this was written -- only transient, auto-retried 429/529 `api_error`
    system events were found, and Claude Code retries those internally, so
    they are deliberately NOT matched here (matching them would fire on a
    session that recovers on its own). This also checks for the literal
    marker string other tools use ("Claude AI usage limit reached", per
    ccusage's own parser). Claude's limit is account-wide, so resume_at
    comes from the shared `_live_weekly_usage()` session_resets_at, not
    anything in this file -- every blocked Claude session resolves to the
    same account-level reset time."""
    for line in reversed(_tail_read_lines(path)):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type")
        if etype == "result":
            if not ev.get("is_error"):
                return None  # most recent result was a clean stop
            blob = str(ev.get("result") or ev.get("error") or "")
        else:
            continue  # system/api_error (incl. transient 429/529) intentionally skipped
        if "usage limit reached" not in blob.lower() and not _USAGE_LIMIT_EXHAUSTED_RE.search(blob):
            return None
        dt = _core._stats_parse_ts(ev.get("ts") or ev.get("timestamp"))
        detected_at = dt.timestamp() if dt is not None else time.time()
        live = _core._live_weekly_usage() or {}
        resets_at_raw = live.get("session_resets_at")
        resume_dt = _core._stats_parse_ts(resets_at_raw) if isinstance(resets_at_raw, str) else None
        if resume_dt is not None:
            resume_at = resume_dt.timestamp()
            estimated = False
        elif isinstance(resets_at_raw, (int, float)):
            resume_at = float(resets_at_raw)
            estimated = False
        else:
            resume_at = detected_at + 5 * 3600
            estimated = True
        return _usage_limit_attach_continuation_fields({
            "engine": "claude",
            "detected_at": detected_at,
            "resume_at": resume_at,
            "resume_at_estimated": estimated,
            "source_text_snippet": blob[:200],
        }, session_id, path)
    return None


# A stopped session's mtime freezes the moment it stops -- a short window
# only catches a stop while the watcher happens to already be polling right
# then. Anything already stuck (or the watcher restarting after the fact)
# ages out of a short window and is never discovered. 24h is still bounded
# (each candidate is a cheap 32KB tail read, not a full parse; observed on
# this machine: ~160 kimi cache files total, ~20 codex rollouts/day, ~769
# claude transcripts/day) and covers "stuck since earlier today", which is
# the actual failure mode reported (CCC-863 follow-up).
_USAGE_LIMIT_CANDIDATE_WINDOW_SECS = 24 * 3600


def _usage_limit_kimi_candidates(now):
    kimi_dir = _core.COMMAND_CENTER_STATE_DIR / "acp" / "kimi"
    out = []
    try:
        for p in kimi_dir.glob("*.jsonl"):
            try:
                if now - p.stat().st_mtime <= _USAGE_LIMIT_CANDIDATE_WINDOW_SECS:
                    sid = p.stem[len("session_"):] if p.stem.startswith("session_") else p.stem
                    out.append((sid, p))
            except OSError:
                continue
    except OSError:
        pass
    return out


def _usage_limit_codex_candidates(now):
    # Rollout files are organized YYYY/MM/DD -- only today's (and, near
    # midnight, yesterday's) directory can hold anything inside the
    # candidacy window, so this never scans the full multi-thousand-file
    # corpus under CODEX_SESSIONS_ROOT.
    out = []
    for days_back in (0, 1):
        day = datetime.fromtimestamp(now - days_back * 86400)
        day_dir = _core.CODEX_SESSIONS_ROOT / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        try:
            for p in day_dir.glob("rollout-*.jsonl"):
                try:
                    if now - p.stat().st_mtime <= _USAGE_LIMIT_CANDIDATE_WINDOW_SECS:
                        m = _ROLLOUT_SID_RE.search(p.stem)
                        sid = m.group(1) if m else p.stem
                        out.append((sid, p))
                except OSError:
                    continue
        except OSError:
            continue
    return out


def _usage_limit_claude_candidates(now):
    out = []
    try:
        for p in Path.home().joinpath(".claude", "projects").glob("*/*.jsonl"):
            try:
                if now - p.stat().st_mtime <= _USAGE_LIMIT_CANDIDATE_WINDOW_SECS:
                    out.append((p.stem, p))
            except OSError:
                continue
    except OSError:
        pass
    return out


def _usage_limit_session_path(engine, sid):
    """Locate the same transcript file the detector for `engine` reads, for
    the post-detection freshness re-check in _usage_limit_scan_once."""
    if engine == "kimi":
        return _core.COMMAND_CENTER_STATE_DIR / "acp" / "kimi" / f"session_{sid}.jsonl"
    if engine == "codex":
        for days_back in range(0, 3):
            day = datetime.fromtimestamp(time.time() - days_back * 86400)
            day_dir = _core.CODEX_SESSIONS_ROOT / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
            try:
                for p in day_dir.glob(f"rollout-*{sid}.jsonl"):
                    return p
            except OSError:
                continue
        return None
    if engine == "claude":
        try:
            for p in Path.home().joinpath(".claude", "projects").glob(f"*/{sid}.jsonl"):
                return p
        except OSError:
            return None
    return None


def _usage_limit_scan_once(now=None):
    """One pass: discover fresh exhaustion stops among plausible candidates
    (recently-touched session files only, see _USAGE_LIMIT_CANDIDATE_WINDOW_SECS
    -- never an O(all sessions) scan), persist any newly-detected stop, then
    fire "continue" for any tracked entry whose resume_at has passed and
    which has shown no new activity since we detected the stop."""
    now = now if now is not None else time.time()
    detectors = (
        ("kimi", _core._usage_limit_kimi_candidates, _detect_kimi_usage_limit_stop),
        ("codex", _core._usage_limit_codex_candidates, _detect_codex_usage_limit_stop),
        ("claude", _core._usage_limit_claude_candidates, _detect_claude_usage_limit_stop),
    )
    tracked = _core._load_usage_limit_resumes()
    for engine, candidates_fn, detect_fn in detectors:
        try:
            candidates = candidates_fn(now)
        except Exception:
            continue
        for sid, path in candidates:
            if not sid:
                continue
            existing = tracked.get(sid)
            if existing and not existing.get("fired") and not existing.get("dismissed"):
                continue  # already tracking an unresolved stop for this session
            if existing and existing.get("dismissed"):
                continue  # user cancelled this stop; don't re-arm from the same transcript
            try:
                found = detect_fn(sid, path)
            except Exception:
                continue
            if not found:
                continue
            if existing and existing.get("detected_at") == found["detected_at"]:
                continue  # same stop already recorded (incl. already fired)
            _core._save_usage_limit_resume_entry(sid, found)

    tracked = _core._load_usage_limit_resumes()
    for sid, entry in list(tracked.items()):
        if entry.get("fired") or entry.get("dismissed"):
            continue
        resume_at = entry.get("resume_at")
        if not isinstance(resume_at, (int, float)) or now < resume_at:
            continue
        # Re-check the session hasn't already resumed on its own (new
        # activity after the stop we detected) before firing.
        try:
            path = _core._usage_limit_session_path(entry.get("engine"), sid)
            newest_mtime = path.stat().st_mtime if path else 0
        except OSError:
            newest_mtime = 0
        if newest_mtime and newest_mtime > entry.get("detected_at", 0) + 5:
            _clear_usage_limit_resume(sid)
            continue
        if not _mark_usage_limit_resume_fired(sid):
            continue  # a sibling thread/process won the race
        # Auto-send is killed. Detection and the countdown banner remain so
        # a parked session is visible, but this pass must not inject
        # "continue" or spawn a continuation — that unattended poke, plus
        # Codex re-queueing an accepted-but-unconfirmed turn, burned a
        # weekly quota overnight. Marking fired (above) makes the stop
        # inert across a CCC restart.
        try:
            _core._log_activity(
                "usage-limit-resume", "AUTO-RESUME-DISABLED",
                f"session={sid} engine={entry.get('engine')} "
                f"resume_at={resume_at}",
            )
        except Exception:
            pass


_USAGE_LIMIT_WATCHER_LOCK = threading.Lock()
_USAGE_LIMIT_WATCHER_STARTED = False


def _start_usage_limit_watcher():
    """daemon=True background poller modeled on
    _start_headless_staleness_watcher(): idempotent per-process start,
    polls every 45s. Cross-process double-fire is guarded by the atomic
    flock in _mark_usage_limit_resume_fired, not by this in-process flag --
    both the dashboard and worker process running their own copy of this
    loop is safe, just mildly redundant."""
    global _USAGE_LIMIT_WATCHER_STARTED
    with _USAGE_LIMIT_WATCHER_LOCK:
        if _USAGE_LIMIT_WATCHER_STARTED:
            return
        _USAGE_LIMIT_WATCHER_STARTED = True

    def _watcher():
        while True:
            time.sleep(45)
            try:
                _core._usage_limit_scan_once()
            except Exception:
                continue

    threading.Thread(target=_watcher, daemon=True, name="usage-limit-resume-watcher").start()


@_core._ttl_memo(3.0)
def _scan_process_states():
    """Return one shared pid -> process-state snapshot.

    Reattached children have no useful ``Popen`` handle after a CCC restart,
    so every hot-path poll must still distinguish a live process from a
    zombie.  A per-PID ``ps`` call made that poll O(number of reattached
    children), which could hold inject-input requests for minutes on a busy
    host.  One short-TTL bulk snapshot keeps the zombie guard without the
    subprocess fan-out.
    """
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,stat="],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}
    if out.returncode != 0:
        return {}
    states = {}
    for line in (out.stdout or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            states[int(parts[0])] = parts[1]
        except ValueError:
            continue
    return states


def _pid_process_state(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return ""
    return _scan_process_states().get(pid, "")


def _pid_is_zombie(pid):
    return _pid_process_state(pid).upper().startswith("Z")


def _live_spawn_registry_entry_for_session(session_id, engine):
    """Return the live spawned-process registry entry for a session/engine."""
    if not session_id or engine not in ("codex", "gemini", "cursor", "antigravity"):
        return None
    for entry in _core._load_spawn_registry():
        if entry.get("engine") != engine or entry.get("session_id") != session_id:
            continue
        try:
            pid = int(entry.get("pid"))
        except (TypeError, ValueError):
            continue
        if not _core._pid_is_engine_process(pid, engine):
            continue
        return {**entry, "pid": pid}
    return None


def _spawn_registry_has_session(session_id, engine):
    if not session_id or engine not in ("codex", "gemini", "cursor", "antigravity"):
        return False
    return any(
        entry.get("engine") == engine and entry.get("session_id") == session_id
        for entry in _core._load_spawn_registry()
    )


@_core._ttl_memo_keyed(3.0)
def _process_tty(pid):
    """Return a process's controlling tty, or None. Memoised per pid (forks ps)."""
    try:
        ps_out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "tty="],
            capture_output=True, text=True, timeout=1,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    tty = (ps_out.stdout or "").strip()
    return _core._normalized_tty(tty)


def _save_spawn_registry_unlocked(entries):
    """Atomically rewrite the registry while its exclusive lock is held."""
    tmp_path = None
    try:
        _core.COMMAND_CENTER_STATE_DIR.mkdir(parents=True, exist_ok=True)
        _core.SPAWNED_PIDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False,
            dir=_core.SPAWNED_PIDS_FILE.parent,
            prefix=f".{_core.SPAWNED_PIDS_FILE.name}.", suffix=".tmp",
        ) as tmp_fh:
            tmp_path = Path(tmp_fh.name)
            json.dump(entries, tmp_fh, indent=2)
            tmp_fh.flush()
            os.fsync(tmp_fh.fileno())
        os.replace(tmp_path, _core.SPAWNED_PIDS_FILE)
        tmp_path = None
    except OSError as e:
        print(f"  [spawn-registry] could not write {_core.SPAWNED_PIDS_FILE} ({e})")
    finally:
        if tmp_path is not None:
            _core._unlink_quiet(tmp_path)


def _save_spawn_registry(entries):
    """Atomically replace the registry under a cross-process lock."""
    with _core._spawn_registry_exclusive_lock():
        _save_spawn_registry_unlocked(entries)


def _mutate_spawn_registry(mutator):
    """Apply one locked read/modify/write transaction."""
    with _core._spawn_registry_exclusive_lock():
        entries = _core._load_spawn_registry()
        changed = bool(mutator(entries))
        if changed:
            _save_spawn_registry_unlocked(entries)
        return changed


def _record_spawn_to_registry(
    pid, name, log_path, cwd, spawned_at, command_summary,
    fifo=None, engine="claude", session_id=None, model=None, repo_path=None,
    parent_session_id=None, prewarm=False, prewarm_id=None, client_id=None,
    reasoning_effort="", auto_compact_k=None, created_at_epoch=None,
    input_result_target=None, input_accepted_at=None, input_command_uuids=None,
):
    """Append a freshly-spawned session to the on-disk registry. The
    session_id is provided for known resume calls and otherwise filled in
    lazily by the reattach sweep (it isn't known at fork time for fresh
    spawns — Claude emits it in the first stream-json event, Codex emits it
    in its `--json` event stream, Gemini emits it in its init stream-json
    event).
    The fifo path is persisted so a fresh CCC instance can reopen the
    write side after a restart and continue injecting messages (Claude
    only — Codex/Gemini/Cursor headless runs are one-shot).
    `engine` ("claude", "codex", "gemini", "cursor", or "antigravity") tells the boot-time reattach sweep
    which ps-grep to use and which JSONL ingestion path to skip.
    `created_at_epoch`, when given, is persisted alongside a prewarm record as
    `expires_at_epoch` (deadline = created_at_epoch + _CLAUDE_PREWARM_TTL_S).
    A prewarm can be owned by a different process than whichever one later
    serves the System status panel's process list (the worker vs. the
    dashboard), so the kill deadline has to live on disk, not just in the
    owning process's in-memory _CLAUDE_PREWARMS dict."""
    record = {
        "pid": pid,
        "session_id": session_id,
        "name": name,
        "log": str(log_path),
        "fifo": str(fifo) if fifo else None,
        "cwd": str(cwd),
        "repo_path": str(repo_path or ""),
        "spawned_at": spawned_at,
        "command_summary": command_summary,
        "engine": engine,
        "model": model or "",
        # The level this process launched with, so the spawned-sessions list
        # still knows it after a restart. Older entries simply lack the key.
        "reasoning_effort": str(reasoning_effort or ""),
        "auto_compact_k": int(auto_compact_k) if auto_compact_k is not None else None,
        "parent_session_id": parent_session_id or "",
    }
    input_state = {
        "input_result_target": input_result_target,
        "input_accepted_at": input_accepted_at,
        "input_command_uuids": input_command_uuids,
    }
    valid_target = _core._valid_input_result_target(input_state)
    valid_accepted_at = _core._valid_input_accepted_at(input_state)
    if valid_target is not None:
        record["input_result_target"] = valid_target
    if valid_accepted_at is not None:
        record["input_accepted_at"] = valid_accepted_at
    valid_command_uuids = _core._valid_input_command_uuids(input_state)
    if valid_command_uuids is not None:
        record["input_command_uuids"] = valid_command_uuids
    if prewarm:
        record.update({
            "prewarm": True,
            "prewarm_id": str(prewarm_id or ""),
            "client_id": str(client_id or ""),
            "created_at_epoch": created_at_epoch,
            "expires_at_epoch": (
                created_at_epoch + _core._CLAUDE_PREWARM_TTL_S
                if created_at_epoch is not None else None
            ),
        })
    def _append_record(entries):
        entries[:] = [
            entry for entry in entries
            if str(entry.get("pid") or "") != str(pid)
        ]
        entries.append(record)
        return True

    _core._mutate_spawn_registry(_append_record)
    # Feed the unified session graph so family-tree queries see this edge
    # immediately. session_id may be None for fresh spawns (filled in lazily
    # by _update_spawn_session_id_in_registry, which also feeds the graph).
    if parent_session_id and session_id:
        _core._session_graph_add_edge(
            parent_session_id, session_id,
            source="ccc-spawn", engine=engine, resumable=True,
        )


def _reap_orphaned_claude_prewarms():
    """Terminate disposable reservations left behind by a dead owner."""
    reaped = 0
    def _reap(entries):
        nonlocal reaped
        survivors = []
        for entry in entries:
            if entry.get("engine") != "claude" or not entry.get("prewarm"):
                survivors.append(entry)
                continue
            pid = entry.get("pid")
            if pid is not None:
                try:
                    os.killpg(int(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except (PermissionError, OSError, ValueError):
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError, ValueError):
                        pass
            _core._unlink_quiet(entry.get("fifo"))
            _core._unlink_quiet(entry.get("log"))
            reaped += 1
        if reaped:
            entries[:] = survivors
            return True
        return False

    _core._mutate_spawn_registry(_reap)
    return reaped


def _remove_spawn_from_registry(pid):
    """Drop a PID from the registry — called when a session exits gracefully
    or is explicitly torn down. Safe to call when the entry isn't present."""
    def _remove(entries):
        pruned = [e for e in entries if e.get("pid") != pid]
        if len(pruned) == len(entries):
            return False
        entries[:] = pruned
        return True

    _core._mutate_spawn_registry(_remove)


def _update_spawn_session_id_in_registry(pid, session_id):
    """Dynamically backfill the session_id for a spawned session in the on-disk registry."""
    if not pid or not session_id:
        return
    try:
        def _update(entries):
            updated = False
            for entry in entries:
                if entry.get("pid") == pid and entry.get("session_id") != session_id:
                    entry["session_id"] = session_id
                    updated = True
            return updated

        _core._mutate_spawn_registry(_update)
    except Exception as e:
        print(f"  [spawn-registry] could not update session_id for pid {pid} ({e})")
    # Feed the unified session graph: the session_id was unknown at spawn
    # time, so the edge wasn't added in _record_spawn_to_registry. Look up
    # the parent_session_id from the registry entry we just updated.
    try:
        for entry in _core._load_spawn_registry():
            if str(entry.get("pid") or "") != str(pid):
                continue
            parent = str(entry.get("parent_session_id") or "").strip()
            engine = str(entry.get("engine") or "claude").strip()
            if parent and parent != session_id:
                _core._session_graph_add_edge(
                    parent, session_id,
                    source="ccc-spawn", engine=engine, resumable=True,
                )
            break
    except Exception:
        pass


def _update_spawn_input_state_in_registry(
    pid, input_result_target, input_accepted_at, input_command_uuids=None,
):
    """Persist one spawn's owned-input state without replacing other fields."""
    if pid is None:
        return
    try:
        def _update(entries):
            updated = False
            for entry in entries:
                if str(entry.get("pid") or "") != str(pid):
                    continue
                before = dict(entry)
                desired_commands = _core._valid_input_command_uuids({
                    "input_command_uuids": input_command_uuids,
                })
                if input_result_target is None:
                    entry.pop("input_result_target", None)
                else:
                    entry["input_result_target"] = input_result_target
                if input_accepted_at is None:
                    entry.pop("input_accepted_at", None)
                else:
                    entry["input_accepted_at"] = input_accepted_at
                if desired_commands is None:
                    entry.pop("input_command_uuids", None)
                else:
                    entry["input_command_uuids"] = desired_commands
                if before != entry:
                    updated = True
            return updated

        _core._mutate_spawn_registry(_update)
    except Exception as e:
        print(
            f"  [spawn-registry] could not update input state for pid {pid} ({e})"
        )


def _recover_spawn_parent_session_id(entry):
    """Recover a legacy spawned-session parent from durable launch evidence."""
    if not isinstance(entry, dict):
        return "", "", "invalid_entry"

    parent = _core._parent_session_id_from_return_address_text(
        entry.get("command_summary") or ""
    )
    if parent:
        return parent, "command_summary", ""

    sid = str(entry.get("session_id") or "").strip()
    if not sid:
        return "", "", "missing_session_id"

    transcript = _core._claude_session_jsonl_path(sid)
    if not transcript:
        return "", "", "missing_transcript"

    parent = _core._parent_session_id_from_transcript_return_address(transcript)
    if parent:
        return parent, "transcript", ""
    return "", "", "unresolved"


def backfill_spawn_parent_session_ids(dry_run=False):
    """Persist recoverable legacy spawn hierarchy into the spawn registry.

    Older CCC versions wrote the report-back footer into the spawned prompt but
    did not persist `parent_session_id`. This backfill trusts only that durable
    footer, either in the registry's command summary or in the child's first
    transcript prompt.
    """
    entries = _core._load_spawn_registry()
    result = {
        "ok": True,
        "dry_run": bool(dry_run),
        "scanned": 0,
        "already_linked": 0,
        "updated": 0,
        "invalid_entry": 0,
        "missing_session_id": 0,
        "missing_transcript": 0,
        "unresolved": 0,
        "updates": [],
    }
    changed = False

    for entry in entries:
        if not isinstance(entry, dict):
            result["invalid_entry"] += 1
            continue
        result["scanned"] += 1
        if str(entry.get("parent_session_id") or "").strip():
            result["already_linked"] += 1
            continue

        parent, source, reason = _recover_spawn_parent_session_id(entry)
        if not parent:
            if reason in result:
                result[reason] += 1
            else:
                result["unresolved"] += 1
            continue

        result["updated"] += 1
        update = {
            "pid": entry.get("pid"),
            "session_id": str(entry.get("session_id") or "").strip(),
            "parent_session_id": parent,
            "source": source,
        }
        result["updates"].append(update)
        if dry_run:
            continue

        sid = update["session_id"]
        for live_entry in _core._spawned_sessions:
            if (
                isinstance(live_entry, dict)
                and sid
                and live_entry.get("session_id") == sid
                and not live_entry.get("parent_session_id")
            ):
                live_entry["parent_session_id"] = parent
        changed = True

    if changed:
        updates_by_pid = {
            str(item.get("pid") or ""): item["parent_session_id"]
            for item in result["updates"] if item.get("pid") is not None
        }

        def _persist_updates(current_entries):
            updated = False
            for current in current_entries:
                parent = updates_by_pid.get(str(current.get("pid") or ""))
                if parent and not current.get("parent_session_id"):
                    current["parent_session_id"] = parent
                    updated = True
            return updated

        _core._mutate_spawn_registry(_persist_updates)
    return result



def _pid_is_engine_process(pid, engine):
    """Verify a PID is actually a process for the given engine before
    treating it as one of ours. PIDs get reused, so a bare `os.kill(pid, 0)`
    isn't enough — we could end up trying to inject into someone's vim.
    Uses `ps -p <pid> -o command=` (works on macOS + Linux) and matches
    strictly on argv[0] basename — substring matching is too lenient
    (any python process whose argv mentions the engine name would otherwise
    pass).

    `engine` is one of "claude", "codex", "gemini", "cursor", "antigravity",
    or "devin" — the basename we expect at argv[0] (Gemini's npm wrapper may
    appear as a node process whose argv includes the gemini script path)."""
    if engine not in ("claude", "codex", "gemini", "cursor", "antigravity", "devin"):
        return False
    if _core._pid_is_zombie(pid):
        return False
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    if out.returncode != 0:
        return False
    cmd = out.stdout.strip()
    if not cmd:
        return False
    parts = cmd.split()
    if not parts:
        return False
    first = parts[0].rsplit("/", 1)[-1]
    if first == engine:
        return True
    # Native Claude builds run as the versioned binary
    # (~/.local/share/claude/versions/2.1.173) — basename never equals
    # "claude", which made bg-pty daemon sessions invisible (CCC-104).
    if engine == "claude" and _core._process_comm_is_claude(parts[0]):
        return True
    if engine == "cursor" and first == "cursor-agent":
        return True
    if engine == "antigravity" and first == "agy":
        return True
    if engine == "gemini":
        return any(p.rsplit("/", 1)[-1] == "gemini" for p in parts[1:4])
    if engine == "cursor":
        return any(p.rsplit("/", 1)[-1] == "cursor-agent" for p in parts[1:4])
    if engine == "antigravity":
        return any(p.rsplit("/", 1)[-1] in ("agy", "antigravity") for p in parts[1:4])
    return False


def _reattach_spawned_orphans(skip_engines=None, only_engines=None):
    """Reattach under the same lock used by registry mutations."""
    with _core._spawn_registry_exclusive_lock():
        return _reattach_spawned_orphans_locked(
            skip_engines=skip_engines, only_engines=only_engines,
        )


def _reattach_spawned_orphans_locked(skip_engines=None, only_engines=None):
    """Boot-time sweep that re-populates `_spawned_sessions` from the on-disk
    registry. Verifies every entry's PID is alive AND is still a process of
    the recorded engine (PIDs can be reused), drops dead/reused ones, and rewrites the
    registry. Never kills anything — just makes live orphans visible to the
    dashboard again."""
    raw_entries = _core._load_spawn_registry()
    skip_engines = {
        str(engine).strip().lower() for engine in (skip_engines or ())
    }
    only_engines = (
        {str(engine).strip().lower() for engine in only_engines}
        if only_engines is not None else None
    )
    if not raw_entries:
        # Still touch the file so a stale corrupt blob is replaced with a
        # known-good empty list on first boot after upgrade.
        if _core.SPAWNED_PIDS_FILE.exists():
            _save_spawn_registry_unlocked([])
        return

    reattached = 0
    dropped = 0
    survivors = []
    for entry in raw_entries:
        engine = str(entry.get("engine") or "claude").lower()
        if engine in skip_engines or (
            only_engines is not None and engine not in only_engines
        ):
            # The persistent worker owns this entry's protocol connection,
            # subprocess handle, and FIFO. Preserve the registry record for
            # dashboard visibility without opening a competing writer.
            survivors.append(dict(entry))
            continue
        pid = entry.get("pid")
        if pid is not None and any(
            str(current.get("pid") or "") == str(pid)
            for current in _core._spawned_sessions
        ):
            survivors.append(dict(entry))
            continue
        if not isinstance(pid, int):
            dropped += 1
            continue
        # Step 1: is the PID alive at all?
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            # Different user owns the PID — we'd never be able to signal it
            # anyway. Drop from registry rather than confuse the UI.
            alive = False
        if not alive:
            dropped += 1
            continue
        if _core._pid_is_zombie(pid):
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass
            dropped += 1
            continue
        # Step 2: is it actually a process of the engine we recorded?
        # Older registry entries pre-date the `engine` field — default
        # them to "claude" since that's all CCC spawned before Codex
        # support landed. PID reuse defence.
        engine = entry.get("engine", "claude")
        if not _core._pid_is_engine_process(pid, engine):
            dropped += 1
            continue
        # Step 3: try to backfill session_id from the log file if we don't
        # have it yet. Claude emits stream-json session headers; Codex emits a
        # thread.started event in its --json stream.
        # Best-effort — failures don't block reattach.
        session_id = entry.get("session_id")
        log_path = entry.get("log")
        if engine == "claude" and not session_id and log_path:
            try:
                session_id = _core.extract_session_id(log_path)
            except Exception:
                session_id = None
        elif engine == "codex" and not session_id and log_path:
            try:
                session_id = _core._extract_codex_thread_id_from_log(log_path)
            except Exception:
                session_id = None
        elif engine == "gemini" and not session_id and log_path:
            try:
                session_id = _core._extract_gemini_session_id_from_log(log_path)
            except Exception:
                session_id = None
        elif engine == "cursor" and not session_id:
            try:
                session_id = (
                    _core._extract_cursor_chat_id_from_log(log_path)
                    or _core._cursor_session_id_for_spawn_entry(entry)
                )
            except Exception:
                session_id = None
        elif engine == "devin" and not session_id:
            try:
                session_id = _core._devin_cli_session_id_for_spawn_entry(entry)
            except Exception:
                session_id = None
        # Looks legit — re-add to the in-memory map with a stub proc.
        # Reopen the FIFO writer if the entry has one. This is the whole
        # point of FIFOs over PIPE: the child is still reading from its
        # stdin (RDWR-on-the-FIFO), so we can dial back in by opening a
        # fresh write fd and start injecting messages again.
        stub = _core._ReattachedProc(pid)
        fifo_path = entry.get("fifo")
        stdin_fd = _core._open_fifo_writer(fifo_path) if fifo_path else None
        synthetic = {
            "pid": pid,
            "name": entry.get("name") or f"reattached-{pid}",
            "log": log_path or "",
            "prompt": entry.get("command_summary", "") or "",
            "started": entry.get("spawned_at", ""),
            "proc": stub,
            "log_fh": None,
            "fifo": fifo_path,
            "stdin_fd": stdin_fd,
            "reattached": True,
            "engine": engine,
            "cwd": entry.get("cwd") or "",
            "repo_path": entry.get("repo_path") or "",
            "model": entry.get("model") or "",
            "parent_session_id": entry.get("parent_session_id") or "",
        }
        valid_input_target = _core._valid_input_result_target(entry)
        valid_input_accepted_at = _core._valid_input_accepted_at(entry)
        valid_input_commands = _core._valid_input_command_uuids(entry)
        if valid_input_target is not None:
            synthetic["input_result_target"] = valid_input_target
        if valid_input_accepted_at is not None:
            synthetic["input_accepted_at"] = valid_input_accepted_at
        if valid_input_commands is not None:
            synthetic["input_command_uuids"] = valid_input_commands
        if session_id:
            synthetic["session_id"] = session_id
            synthetic["resumed_sid"] = session_id
        _core._spawned_sessions.append(synthetic)
        survivor = {
            "pid": pid,
            "session_id": session_id,
            "name": entry.get("name"),
            "log": log_path,
            "fifo": fifo_path,
            "cwd": entry.get("cwd"),
            "repo_path": entry.get("repo_path") or "",
            "spawned_at": entry.get("spawned_at"),
            "command_summary": entry.get("command_summary", ""),
            "engine": engine,
            "model": entry.get("model") or "",
            "parent_session_id": entry.get("parent_session_id") or "",
        }
        if valid_input_target is not None:
            survivor["input_result_target"] = valid_input_target
        if valid_input_accepted_at is not None:
            survivor["input_accepted_at"] = valid_input_accepted_at
        if valid_input_commands is not None:
            survivor["input_command_uuids"] = valid_input_commands
        survivors.append(survivor)
        reattached += 1

    _save_spawn_registry_unlocked(survivors)
    print(f"  [spawn-registry] reattached {reattached} orphans, dropped {dropped} dead/reused entries")


def list_spawned_sessions():
    """Return spawned sessions with running/finished status. Also opportunistically
    drops finished sessions from the on-disk spawn registry so it doesn't grow
    forever (the in-memory list keeps them so the UI can still show 'finished'
    state, but persistence only needs the live ones)."""
    result = []
    worker_owned_registry = []
    if _core._control_plane_routes_engines():
        active_work_response = _core._control_plane_request("work.list", {
            "states": ["dispatching", "running"],
            "limit": 2000,
        })
        active_work = (
            active_work_response.get("work")
            if isinstance(active_work_response.get("work"), list) else []
        )
        active_session_ids = {
            str(item.get("session_id") or "")
            for item in active_work
            if item.get("session_id")
        }
        active_pids = {
            str((item.get("result") or {}).get("pid") or "")
            for item in active_work
            if isinstance(item.get("result"), dict)
            and (item.get("result") or {}).get("pid")
        }
        local_pids = {str(item.get("pid")) for item in _core._spawned_sessions}
        worker_owned_registry = [
            entry for entry in _core._load_spawn_registry()
            if (entry.get("engine") or "claude") in ("claude", "codex", "kimi")
            and str(entry.get("pid")) not in local_pids
        ]
        for entry in worker_owned_registry:
            pid = entry.get("pid")
            sid = (
                entry.get("session_id")
                or entry.get("resumed_sid")
                or ""
            )
            running = bool(
                (pid and _core._pid_alive(pid))
                or str(pid or "") in active_pids
                or str(sid or "") in active_session_ids
            )
            result.append({
                "pid": pid,
                "spawn_id": str(entry.get("spawn_id") or pid or ""),
                "session_id": sid,
                "session_id_pending": not bool(sid),
                "name": entry.get("name") or "",
                "log": entry.get("log") or "",
                "prompt": entry.get("command_summary") or "",
                "started": entry.get("spawned_at") or "",
                "spawned_at": entry.get("spawned_at") or "",
                "engine": entry.get("engine") or "claude",
                "cwd": entry.get("cwd") or "",
                "repo_path": entry.get("repo_path") or "",
                "model": entry.get("model") or "",
                "reasoning_effort": entry.get("reasoning_effort") or "",
                "parent_session_id": entry.get("parent_session_id") or "",
                "command_summary": entry.get("command_summary") or "",
                "running": running,
                "exit_code": None,
                "status": "running" if running else "finished",
                "prewarm": bool(entry.get("prewarm")),
                "prewarm_id": entry.get("prewarm_id") or "",
                "created_at_epoch": entry.get("created_at_epoch"),
                "expires_at_epoch": entry.get("expires_at_epoch"),
            })
    for s in _core._spawned_sessions:
        poll = _core._poll_spawn_entry(s)
        sid = _core._spawn_session_id_from_entry(s)
        pid = s.get("pid")
        spawn_id = str(s.get("spawn_id") or pid or "")
        result.append({
            "pid": pid,
            "spawn_id": spawn_id,
            "session_id": sid,
            "session_id_pending": not bool(sid),
            "name": s.get("name", ""),
            "log": s.get("log", ""),
            "prompt": s.get("prompt", ""),
            "started": s.get("started", ""),
            "spawned_at": s.get("started", ""),
            "engine": s.get("engine", "claude"),
            "cwd": s.get("cwd") or "",
            "repo_path": s.get("repo_path") or "",
            "model": s.get("model") or "",
            "reasoning_effort": s.get("reasoning_effort") or "",
            "parent_session_id": s.get("parent_session_id") or "",
            "command_summary": s.get("prompt", ""),
            "running": poll is None,
            "exit_code": poll,
            "status": "running" if poll is None else f"finished (exit {poll})",
        })
    # Prewarm reservations: their kill deadline (expires_at_epoch) is only
    # durable in the on-disk registry (see _record_spawn_to_registry), since
    # the process that owns a prewarm (the worker, under control-plane
    # routing) can differ from whichever process serves this call. Merge
    # them in unconditionally, deduped against pids already surfaced above.
    # A claimed prewarm graduates into a normal entry via _spawned_sessions
    # (or the worker-owned branch above) and its registry record loses the
    # "prewarm" flag at claim time (_record_spawn_to_registry is called
    # again without prewarm=True, which overwrites by pid) — so it naturally
    # drops out of this branch once it's a real working session.
    seen_pids = {str(r.get("pid")) for r in result}
    for entry in _core._load_spawn_registry():
        if not entry.get("prewarm"):
            continue
        pid = entry.get("pid")
        if pid is None or str(pid) in seen_pids:
            continue
        seen_pids.add(str(pid))
        running = bool(_core._is_pid_alive(pid))
        result.append({
            "pid": pid,
            "spawn_id": str(entry.get("spawn_id") or pid or ""),
            "session_id": entry.get("session_id") or "",
            "session_id_pending": not bool(entry.get("session_id")),
            "name": entry.get("name") or "",
            "log": entry.get("log") or "",
            "prompt": entry.get("command_summary") or "",
            "started": entry.get("spawned_at") or "",
            "spawned_at": entry.get("spawned_at") or "",
            "engine": entry.get("engine") or "claude",
            "cwd": entry.get("cwd") or "",
            "repo_path": entry.get("repo_path") or "",
            "model": entry.get("model") or "",
            "reasoning_effort": entry.get("reasoning_effort") or "",
            "parent_session_id": entry.get("parent_session_id") or "",
            "command_summary": entry.get("command_summary") or "",
            "running": running,
            "exit_code": None,
            "status": "running" if running else "finished",
            "prewarm": True,
            "prewarm_id": entry.get("prewarm_id") or "",
            "created_at_epoch": entry.get("created_at_epoch"),
            "expires_at_epoch": entry.get("expires_at_epoch"),
        })
    return result


def _group_chat_normalize_whitespace(real_path):
    """Collapse runs of blank lines inside / between group-chat posts.

    Agents writing to the chat via the Edit tool routinely leave dozens
    of trailing blank lines at the end of their post (most-extreme
    observed: 230+ blank lines under a 9-line post). The reader UI hides
    them but every other agent re-reading the .md file pays tokens for
    each blank — wasted context.

    Algorithm: walk lines; for each post body (between `## ` headers)
    strip trailing blank lines, then guarantee exactly one blank line
    between the body and the next `## ` header. Idempotent — no-op when
    the file is already clean. Returns True if the file was rewritten.

    Caller's responsibility to bump the sidecar baseline mtime so the
    group-chat watcher doesn't treat the normalize-write as participant
    activity (which would feed a re-nudge loop).
    """
    try:
        with open(real_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return False
    lines = content.split("\n")
    cleaned = []
    body = []

    def flush_body():
        while body and body[-1].strip() == "":
            body.pop()
        cleaned.extend(body)
        body.clear()

    for line in lines:
        if line.startswith("## "):
            flush_body()
            if cleaned and cleaned[-1].strip() != "":
                cleaned.append("")
            cleaned.append(line)
        else:
            body.append(line)
    flush_body()
    new_content = "\n".join(cleaned).rstrip() + "\n"
    if new_content == content:
        return False
    try:
        with open(real_path, "w", encoding="utf-8") as fh:
            fh.write(new_content)
        return True
    except OSError:
        return False


def _shorten_display_name(raw, session_id=""):
    """Return a short, heading-safe display name for a group-chat participant.

    Auto-derived names are often a session's whole first message — a codex
    task description, sometimes with a leading ``##`` — which renders as an
    ugly multi-line blob in the chat heading and the wake-status list. Collapse
    whitespace, drop ``#``/markdown, cut at the first clause, and cap to a few
    words / 28 chars so the label stays a single tidy token. Falls back to the
    8-char session hash when nothing usable remains. Pure string work — no
    session/cwd lookups (this runs in per-participant loops; keep it cheap).
    """
    s = re.sub(r"\s+", " ", str(raw or "")).strip()
    s = s.lstrip("#").strip().replace("#", "").strip()
    hash8 = (str(session_id or "")[:8]) or "agent"
    if not s:
        return hash8
    if len(s) <= 28:
        return s[:80]
    # Too long: first clause, then cap to the first few words / 28 chars.
    s = re.split(r"[.:;|]\s", s, maxsplit=1)[0].strip()
    out = ""
    for w in s.split(" "):
        if out and len(out) + 1 + len(w) > 28:
            break
        out = (out + " " + w).strip()
    return (out or s[:28]).strip() or hash8


def _group_chat_hex_label(raw, session_id=""):
    """True when a label is just a session id / UUID prefix, not a name."""
    s = re.sub(r"\s+", " ", str(raw or "")).strip().strip("`")
    if not s:
        return False
    sid = str(session_id or "").strip()
    low = s.lower()
    if sid and low in (sid.lower(), sid[:8].lower()):
        return True
    compact = low.replace("-", "")
    return bool(len(compact) >= 8 and re.fullmatch(r"[0-9a-f]+", compact))


def _group_chat_storeable_display_name(raw, session_id=""):
    """Return a sidecar-safe participant name, or "" when unresolved.

    Group chat clients assign Agent-N fallbacks. The sidecar should contain
    only actual display names, never raw UUID prefixes that look like names.
    """
    if _group_chat_hex_label(raw, session_id):
        return ""
    name = _core._shorten_display_name(raw, session_id)
    if _group_chat_hex_label(name, session_id):
        return ""
    return name


def _group_chat_resolve_session_display_name(session_id):
    """Best-effort lookup mirroring /api/sessions display-name precedence.

    This is used only to backfill legacy group-chat sidecars. It checks the
    same durable/live sources the session builders use, but targets one sid so
    the 3s /read poll does not have to rebuild the whole sessions payload.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    try:
        overrides = _core._load_session_name_overrides()
    except Exception:
        overrides = {}
    name = _group_chat_storeable_display_name(overrides.get(sid), sid)
    if name:
        return name

    try:
        registry_meta = (_core._load_session_registry() or {}).get(sid) or {}
    except Exception:
        registry_meta = {}
    try:
        spawn_entry = _core._spawn_registry_entry_for_session(sid) or {}
    except Exception:
        spawn_entry = {}
    for raw in (registry_meta.get("name"), spawn_entry.get("name")):
        name = _group_chat_storeable_display_name(raw, sid)
        if name:
            return name

    try:
        jsonl = _core._find_session_jsonl_any_project(sid)
    except Exception:
        jsonl = None
    if jsonl:
        try:
            tail_meta = _core._extract_tail_meta(jsonl) or {}
        except Exception:
            tail_meta = {}
        for raw in (
            tail_meta.get("custom_title"),
            tail_meta.get("agent_name"),
            tail_meta.get("ai_title"),
        ):
            name = _group_chat_storeable_display_name(raw, sid)
            if name:
                return name

    try:
        codex_row = _core._codex_thread_row(sid)
    except Exception:
        codex_row = None
    if codex_row:
        title = _core._strip_ccc_session_state_instruction(
            (codex_row.get("title") or "").strip()
        ).strip()
        first_message = _core._strip_ccc_session_state_instruction(
            (codex_row.get("first_user_message") or "").strip()
        ).strip()
        name = _group_chat_storeable_display_name(
            _core._codex_display_name(codex_row, title=title, first_message=first_message),
            sid,
        )
        if name:
            return name

    return ""


def _group_chat_enrich_name_map(chat_path, meta):
    """Fill real display-name gaps in a chat sidecar and return the map."""
    session_ids = list((meta or {}).get("session_ids") or [])
    current = dict((meta or {}).get("name_map") or {})
    enriched = {}
    changed = False

    for sid in session_ids:
        existing = _group_chat_storeable_display_name(current.get(sid), sid)
        if existing:
            enriched[sid] = existing
            if existing != current.get(sid):
                changed = True
            continue
        if sid in current:
            changed = True
        resolved = _group_chat_resolve_session_display_name(sid)
        if resolved:
            enriched[sid] = resolved
            changed = True

    for sid, raw in current.items():
        if sid in session_ids:
            continue
        kept = _group_chat_storeable_display_name(raw, sid)
        if kept:
            enriched[sid] = kept
            if kept != raw:
                changed = True
        else:
            changed = True

    if changed:
        try:
            _core._update_group_chat_sidecar(chat_path, name_map=enriched)
        except Exception:
            pass
    return enriched


def _group_chat_fallback_agent_name(session_id, session_ids):
    try:
        idx = list(session_ids or []).index(session_id) + 1
    except ValueError:
        idx = 1
    return f"Agent-{idx}"


def _group_chat_participant_label(session_id, name_map, session_ids=None):
    name = _group_chat_storeable_display_name((name_map or {}).get(session_id), session_id)
    return name or _group_chat_fallback_agent_name(session_id, session_ids or [])


def _group_chat_nudge_log_by_message(meta, limit=200):
    entries = (meta or {}).get("nudge_log") or []
    if not isinstance(entries, list):
        return {}
    grouped = {}
    for entry in entries[-int(limit):]:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("message_key") or "").strip()
        if not key:
            continue
        grouped.setdefault(key, []).append({
            "sid": entry.get("sid") or entry.get("session_id") or "",
            "name": entry.get("name") or "",
            "ok": bool(entry.get("ok")),
            "at": entry.get("at") or "",
        })
    return grouped


def _group_chat_latest_author_matches(content):
    pat = re.compile(
        r'^##\s+.+?—\s+(?:([0-9a-fA-F]{8})\b|(Human)\b)',
        re.MULTILINE,
    )
    return pat, list(pat.finditer(content or ""))


def _group_chat_native_sid(sid):
    """A participant entry may be a global ref ("<node>:<sid>") for a
    session owned by another CCC node. Author headings and @mentions always
    use the NATIVE session id, so match on that part."""
    return (sid or "").split(":", 1)[-1]


def _group_chat_addressed_sids(body, session_ids, name_map):
    addressed = set()
    lower_body = (body or "").lower()
    mentioned_hashes = {h.lower() for h in re.findall(r'@([0-9a-fA-F]{8})\b', body or "")}
    for sid in session_ids or []:
        if _group_chat_native_sid(sid)[:8].lower() in mentioned_hashes:
            addressed.add(sid)
            continue
        label = _group_chat_participant_label(sid, name_map, session_ids)
        if label and ("@" + label.lower()) in lower_body:
            addressed.add(sid)
    return addressed


def _group_chat_auto_nudge_selection(content, session_ids, name_map):
    """Return auto-nudge targets for the latest real post."""
    pat, matches = _group_chat_latest_author_matches(content)
    if not matches:
        return {"targets": [], "reminder_key": "", "skipped": "no recent author",
                "last_author_hash": None, "last_author_is_human": False}
    tail = (content or "")[-12000:]
    tail_start = max(0, len(content or "") - len(tail))
    recent_matches = [m for m in matches if m.start() >= tail_start]
    if not recent_matches:
        return {"targets": [], "reminder_key": "", "skipped": "no recent author",
                "last_author_hash": None, "last_author_is_human": False}
    last_match = matches[-1]
    if last_match.start() < tail_start:
        return {"targets": [], "reminder_key": "", "skipped": "no recent author",
                "last_author_hash": None, "last_author_is_human": False}

    author = last_match.group(1) or last_match.group(2)
    reminder_key = f"{len(matches)}:{last_match.group(0).strip()}"
    exclude_sid = None
    if author != "Human":
        author_hash = author.lower()
        for sid in session_ids or []:
            if _group_chat_native_sid(sid).lower().startswith(author_hash):
                exclude_sid = sid
                break
        targets = [sid for sid in (session_ids or []) if sid != exclude_sid]
        return {
            "targets": targets,
            "reminder_key": reminder_key,
            "skipped": "" if targets else "no targets",
            "last_author_hash": author_hash,
            "last_author_is_human": False,
        }

    body_start = last_match.end()
    next_heading = pat.search(content or "", body_start)
    body = (content or "")[body_start:next_heading.start() if next_heading else None]
    body = re.sub(r'^>\s*_[^\n]*system:[^\n]*\n?', '', body, flags=re.MULTILINE)
    addressed = _group_chat_addressed_sids(body, session_ids, name_map)
    targets = [sid for sid in (session_ids or []) if not addressed or sid in addressed]
    return {
        "targets": targets,
        "reminder_key": reminder_key,
        "skipped": "" if targets else "no targets",
        "last_author_hash": None,
        "last_author_is_human": True,
        "addressed_sids": addressed,
    }


def _group_chat_post(path, text, chat_uuid="", session_id="", name="", emoji=""):
    """Append an entry to a group-chat file.

    Default (no ``session_id``) writes a **Human** entry. When ``session_id`` is
    given, write a canonical **agent** entry — ``## <ts> — <8hex>: <name>
    <emoji>`` — so a participant can post through the API instead of
    hand-editing the file. Hand-edited blocks are the source of CCC-133: a
    free-handed heading that doesn't match the dashboard parser gets absorbed
    into the previous message and silently vanishes. The server-built heading is
    guaranteed to match the same regex the reader and snapshot use.
    """
    real_path = _core._resolve_group_chat_ref(path, chat_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    now = datetime.now()
    day_name = now.strftime("%A")
    try:
        tz_name = now.astimezone().strftime("%Z")
    except Exception:
        tz_name = "local"
    full_ts = now.strftime(f"%Y-%m-%d {day_name} %H:%M:%S") + f" {tz_name}"
    sid = str(session_id or "").strip()
    m = re.match(r"([0-9a-fA-F]{8})", sid)
    if m:
        # Agent post. Derive the display name from the sidecar name_map when the
        # caller didn't supply one. Sanitize the name to a single heading-safe
        # line — no newlines or '#' — so a participant's title can't inject a
        # second heading or break the splitter (the other half of CCC-133).
        hash8 = m.group(1).lower()
        sidecar = _core._load_group_chat_sidecar(real_path) or {}
        nm = _group_chat_enrich_name_map(real_path, sidecar)
        disp = (
            _group_chat_storeable_display_name(name, sid)
            or nm.get(sid)
            or _group_chat_resolve_session_display_name(sid)
            or _group_chat_fallback_agent_name(sid, sidecar.get("session_ids") or [])
        )
        marker = str(emoji or "").strip() or "💬"
        speaker = f"{hash8}: {disp} {marker}"
    else:
        speaker = "Human"
    entry = f"\n---\n\n## {full_ts} — {speaker}\n\n{text}\n"
    try:
        with open(real_path, "a", encoding="utf-8") as fh:
            fh.write(entry)
        # Strip trailing blank lines that prior agent posts left behind
        # so the file stays lean for the next round of agent reads.
        _group_chat_normalize_whitespace(real_path)
        # Track the real-message timestamp separately from the .md mtime —
        # system writes (pause/resume/nudge-log lines via _group_chat_log_system)
        # also bump the file mtime, which made the sidebar's "Xm ago" row label
        # reflect the last administrative touch instead of the last actual
        # message (CCC-487).
        _core._update_group_chat_sidecar(real_path, last_message_at=time.time())
        sidecar = _core._load_group_chat_sidecar(real_path)
        if sid:
            read_state = dict(sidecar.get("read_state") or {})
            read_state[sid] = datetime.now().astimezone().isoformat()
            _core._update_group_chat_sidecar(real_path, read_state=read_state)
        if not sidecar.get("archived"):
            _core._update_group_chat_sidecar(real_path, closed_at=None)
            _core._register_coordination(real_path)
        _core._group_chat_update_header_if_changed(real_path, force_write=True)
        # Bust the coalescing read cache so the poster sees their own message
        # on the next poll instead of a ≤TTL-old snapshot from before the post.
        _invalidate_group_chat_read_cache(real_path)
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


# Coalescing cache for group-chat reads. The sidebar polls /api/group-chat/read
# every ~3s per open chat, and each build fans out to per-participant liveness
# probes (ps/lsof forks) plus a waiting-summary pass — the same concurrent
# pile-up the live-activity snapshot already guards against. Serve a ≤TTL-old
# result per chat path; single-flight so at most one build runs per path at a
# time. Invalidated immediately on post so a participant always sees their own
# message without waiting out the TTL.
_GROUP_CHAT_READ_TTL = 3.5  # > poll interval (3 s) so consecutive polls share one build
_group_chat_read_cache = {}  # real_path -> {"ts": float, "data": (result, err)}
_group_chat_read_cache_lock = threading.Lock()


def _invalidate_group_chat_read_cache(real_path=None):
    """Drop the coalescing cache for one chat path (or all paths)."""
    with _group_chat_read_cache_lock:
        if real_path is None:
            _group_chat_read_cache.clear()
        else:
            _group_chat_read_cache.pop(real_path, None)


def _group_chat_read(path, chat_uuid=""):
    """Read a group-chat file. Returns (result_dict, None) or (None, 'forbidden').

    Coalesced: concurrent / rapid polls within _GROUP_CHAT_READ_TTL share one
    build (see _group_chat_read_cache). The expensive per-participant liveness
    probing happens only in the uncached build below."""
    real_path = _core._resolve_group_chat_ref(path, chat_uuid)
    if not real_path:
        return None, "forbidden"
    now = time.time()
    ent = _group_chat_read_cache.get(real_path)
    if ent is not None and now - ent["ts"] < _GROUP_CHAT_READ_TTL:
        return ent["data"]
    with _group_chat_read_cache_lock:
        now = time.time()
        ent = _group_chat_read_cache.get(real_path)
        if ent is not None and now - ent["ts"] < _GROUP_CHAT_READ_TTL:
            return ent["data"]
        data = _group_chat_read_uncached(real_path)
        _group_chat_read_cache[real_path] = {"ts": time.time(), "data": data}
        return data


def _group_chat_read_uncached(real_path):
    """The real group-chat read build. Does NOT rewrite the file: whitespace
    normalisation and wake-status header updates happen on post and on the 30s
    watcher tick, not on this read hot path (a sidebar poll every 3s must not
    trigger a 58KB read+rewrite plus a duplicate per-participant probe pass)."""
    try:
        stat_result = os.stat(real_path)
        with open(real_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        
        meta = _core._load_group_chat_sidecar(real_path)
        sids = meta.get("session_ids") or []
        nm = _group_chat_enrich_name_map(real_path, meta)
        
        is_paused = bool(meta.get("paused"))
        with _core._coord_lock:
            active_entry = _core._active_coordinations.get(real_path) or {}
            in_watcher = real_path in _core._active_coordinations
        if meta.get("archived"):
            status = "archived"
        elif is_paused:
            status = "paused"
        elif in_watcher:
            status = "active"
        else:
            status = "closed"

        # Take the freshest of (in-memory last_nudge, persisted
        # last_reminder_at) so the "Last nudge: X ago" row stays
        # accurate across server restarts AND across manual targeted
        # nudges that bump the sidecar but skip the watcher's
        # _active_coordinations entry.
        in_mem_nudge = active_entry.get("last_nudge") or 0
        persisted_nudge = meta.get("last_reminder_at") or 0
        last_nudge_at = max(in_mem_nudge, persisted_nudge)
        last_reminder_at = persisted_nudge
        last_activity = active_entry.get("last_activity") or 0

        waiting = _core._group_chat_compute_waiting(real_path, sids, nm)
        # Parallelise per-participant liveness probes. On a cold _ttl_memo_keyed
        # cache each call does find_session_cwd + session_live_status (up to 20
        # ps forks). Sequential over 4 participants blocked the first load for
        # several seconds. Threads are safe here: each key is independent.
        if sids:
            with ThreadPoolExecutor(max_workers=min(len(sids), 4)) as _pm_ex:
                _pm_futs = {sid: _pm_ex.submit(_core._group_chat_participant_meta, sid) for sid in sids}
                participant_meta = {sid: fut.result() for sid, fut in _pm_futs.items()}
        else:
            participant_meta = {}
        # Count participant sessions CCC currently considers live — these are the
        # ones a nudge would wake (the actual token cost).
        live_count = sum(1 for m in participant_meta.values() if m and m.get("is_live"))

        _qr = _core._queue_replay_events()
        _queue_replay = _qr.get("events", [])
        _queue_replay_truncated = _qr.get("truncated", False)

        return {
            "ok": True,
            "content": content,
            "mtime": stat_result.st_mtime,
            "topic": meta.get("topic", ""),
            "mode": meta.get("mode", "topic"),
            "session_ids": sids,
            "name_map": nm,
            "nudge_log": _group_chat_nudge_log_by_message(meta),
            # Queue/ticket-appearance events for the replay timeline (W22/B4).
            # Derived from the durable ticket store, cached by (mtime,size);
            # the client interleaves them into the message-ordered replay.
            "queue_events": _queue_replay,
            "queue_events_truncated": _queue_replay_truncated,
            "read_state": meta.get("read_state") or {},
            "status": status,
            "paused": is_paused,
            "paused_at": meta.get("paused_at"),
            "waiting": waiting,
            "participant_meta": participant_meta,
            "orchestrator_timer_active": status == "active",
            "orchestrator_last_nudge_at": last_nudge_at,
            "orchestrator_last_reminder_at": last_reminder_at,
            "orchestrator_last_activity_at": last_activity,
            "orchestrator_last_reminder_targets": meta.get("last_reminder_targets") or [],
            # Watcher cadence — lets the panel say "checks every 30s, nudges at
            # most every Ns" instead of leaving the loop opaque.
            "orchestrator_poll_interval": _core._COORD_POLL_INTERVAL,
            "orchestrator_nudge_interval": _core._COORD_NUDGE_INTERVAL,
            "orchestrator_idle_timeout": _core._COORD_DEATH_TIMEOUT,
            "participant_count": len(sids),
            "participant_live_count": live_count,
        }, None
    except FileNotFoundError:
        return {"ok": False, "error": "not found"}, None
    except OSError as exc:
        return {"ok": False, "error": str(exc)}, None


_GROUP_CHAT_SNAPSHOT_TAIL_BYTES = 24000
_GROUP_CHAT_SNAPSHOT_BODY_LIMIT = 2000


def _group_chat_latest_message_snapshot(chat_path: str) -> str:
    """Return an advisory snapshot of the latest real group-chat post.

    The chat file remains authoritative. This only gives a nudged participant
    enough immediate context to know why it was woken up before it re-reads
    the file.
    """
    try:
        with open(chat_path, "r", encoding="utf-8") as fh:
            tail = fh.read()[-_GROUP_CHAT_SNAPSHOT_TAIL_BYTES:]
    except OSError:
        return ""

    headings = list(re.finditer(
        r"^##\s+.+?—\s+(?:[0-9a-fA-F]{8}\b|Human\b).*$",
        tail,
        re.MULTILINE,
    ))
    if not headings:
        return ""

    last = headings[-1]
    heading = last.group(0).strip()
    body = tail[last.end():]
    body_lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("> _") and "system:" in stripped:
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip()

    truncated = False
    if len(body) > _GROUP_CHAT_SNAPSHOT_BODY_LIMIT:
        body = body[:_GROUP_CHAT_SNAPSHOT_BODY_LIMIT].rstrip()
        truncated = True
    if truncated:
        body += "\n\n[Snapshot truncated by CCC.]"

    if body:
        return f"{heading}\n\n{body}"
    return heading


def _group_chat_inject_text(chat_path: str, topic: str, mode: str, sid: str,
                            chat_uuid: str = "", remote_host_node: str = "") -> str:
    """Build the /group-chat injection with a tiny latest-post pointer.

    Previously inlined up to ~2KB of the last message body, which the
    agent then re-reads from the chat file anyway — pure token waste at
    every nudge. Now we send only the heading line (author + timestamp)
    as a "there's a new message; here's whose" pointer. The agent reads
    the chat file for actual content.

    When the participant lives on ANOTHER CCC node (``remote_host_node`` =
    this chat's host node id), the local file path is useless to it — the
    check-in references the chat by stable uuid + host node instead, and
    the participant reads/posts through its own CCC, which proxies to the
    host.
    """
    safe_topic = (topic or "").replace('"', '\\"')
    native_sid = _group_chat_native_sid(sid)
    if remote_host_node:
        text = (
            "Group-chat check-in (cross-machine): you are a participant in a "
            f'group chat titled "{safe_topic}" (mode={mode}) hosted on another '
            f"CCC node. Your sid is \"{native_sid}\". Use YOUR OWN CCC's API "
            "(base URL: http://127.0.0.1:$(cat ~/.claude/command-center/port.txt "
            "2>/dev/null || echo 8090)) with these calls:\n"
            f"- read: GET /api/group-chat/read?id={chat_uuid}&host_node={remote_host_node}\n"
            f"- post: POST /api/group-chat/post with JSON "
            f"{{\"id\": \"{chat_uuid}\", \"host_node\": \"{remote_host_node}\", "
            f"\"session_id\": \"{native_sid[:8]}\", \"name\": \"<your name>\", "
            "\"text\": \"<your message>\"}\n"
            "Read the chat first, then reply once if you have something to add."
        )
        return text
    # No leading "/" (CCC-108): the slash form only dispatches in a live
    # Claude TUI. Codex and headless Claude receive it as literal text —
    # Codex's router used to bounce it outright, and headless models read
    # it as a malformed command rather than an instruction. Both engines
    # have the group-chat-checkin skill installed (~/.claude/skills and
    # ~/.codex/skills), so an explicit invoke-the-skill instruction works
    # on every transport, TUI included.
    text = (
        "Group-chat check-in: invoke your group-chat-checkin skill with "
        f'chat="{chat_path}" topic="{safe_topic}" mode={mode} sid="{sid}". '
        "If you cannot invoke skills, read the chat file at that path and "
        "follow its instructions."
    )
    snapshot = _core._group_chat_latest_message_snapshot(chat_path)
    if snapshot:
        # Keep just the first line — the `## <ts> — <author>` heading —
        # so the agent knows what just landed without re-injecting its
        # body on every nudge.
        heading = snapshot.split("\n", 1)[0].strip()
        if heading:
            text += (
                "\n\n"
                "CCC pointer: a new post just landed — "
                f"{heading}\n"
                "Read the chat file before posting; the body is there."
            )
    return text


def _group_chat_foreign_host(raw_path="", raw_uuid=""):
    """When a locally-resolvable chat's sidecar says another node hosts it
    (ownership was moved), return that host's node_id — reads/posts must
    proxy there instead of touching the stale local copy."""
    real_path = _core._resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return None
    host = (_core._load_group_chat_sidecar(real_path) or {}).get("host_node")
    if host and host != federation.node_id():
        return host
    return None


def _group_chat_import_payload(data, peer):
    """Peer-facing: receive chat ownership (markdown + sidecar). The chat's
    identity is its uuid — the filename is re-derived locally, never trusted
    from the wire."""
    sidecar = data.get("sidecar")
    md_b64 = data.get("md_b64")
    if not isinstance(sidecar, dict) or not md_b64:
        return {"ok": False, "error": "bad_request",
                "detail": "sidecar and md_b64 required"}, 400
    chat_uuid = str(sidecar.get("uuid") or "").strip().lower()
    if not _core._valid_group_chat_uuid(chat_uuid):
        return {"ok": False, "error": "bad_request",
                "detail": "sidecar carries no valid uuid"}, 400
    try:
        md_bytes = base64.b64decode(str(md_b64), validate=True)
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": "bad_request",
                "detail": f"md_b64 invalid: {e}"}, 400
    if len(md_bytes) > 8 * 1024 * 1024:
        return {"ok": False, "error": "bad_request",
                "detail": "chat transcript too large"}, 400
    existing = _core._resolve_group_chat_ref("", chat_uuid)
    if existing and not data.get("overwrite"):
        return {"ok": False, "error": "chat_exists",
                "detail": "chat with this uuid already exists here"}, 409
    group_chats_dir = os.path.expanduser("~/.claude/group-chats")
    os.makedirs(group_chats_dir, exist_ok=True)
    if existing:
        md_path = existing
    else:
        slug = re.sub(r"[^a-z0-9]+", "-",
                      str(sidecar.get("topic") or "chat").lower()).strip("-")[:60] or "chat"
        md_path = os.path.join(
            group_chats_dir,
            f"{slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md")
    tmp = md_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(md_bytes)
    os.replace(tmp, md_path)
    new_sidecar = dict(sidecar)
    new_sidecar["host_node"] = federation.node_id()
    new_sidecar.pop("moved_to", None)
    sidecar_path = md_path[:-3] + ".json"
    with open(sidecar_path, "w", encoding="utf-8") as fh:
        json.dump(new_sidecar, fh)
    if not new_sidecar.get("archived") and not new_sidecar.get("closed_at"):
        _core._register_coordination(md_path)
    _core._group_chat_log_system(md_path, "chat ownership moved to this node")
    return {"ok": True, "uuid": chat_uuid, "host_node": federation.node_id(),
            "path": md_path}, 200


def _group_chat_move_host_payload(data):
    """Local: hand this chat's ownership to a paired peer. The uuid stays
    the chat's identity; the local copy becomes a proxy stub."""
    chat_uuid = str(data.get("id") or data.get("uuid") or "").strip()
    dest_node = str(data.get("node_id") or data.get("dest_node_id") or "").strip()
    real_path = _core._resolve_group_chat_ref(str(data.get("path") or ""), chat_uuid)
    if not real_path:
        return {"ok": False, "error": "not_found"}, 404
    peer = federation.get_peer(dest_node)
    if not peer:
        return {"ok": False, "error": "unpaired_peer",
                "detail": f"no paired peer {dest_node!r}"}, 404
    meta = _core._load_group_chat_sidecar(real_path) or {}
    if meta.get("host_node") not in (None, "", federation.node_id()):
        return {"ok": False, "error": "not_owner",
                "owner_node": meta.get("host_node"),
                "detail": "this node does not host the chat"}, 409
    try:
        md_b64 = base64.b64encode(Path(real_path).read_bytes()).decode("ascii")
    except OSError as e:
        return {"ok": False, "error": "read_failed", "detail": str(e)}, 500
    try:
        result = federation.PeerClient(peer).request(
            "POST", "/api/federation/v1/group-chat/import",
            {"sidecar": meta, "md_b64": md_b64,
             "overwrite": bool(data.get("overwrite"))}, timeout=60)
    except federation.PeerError as e:
        return {"ok": False, "error": e.kind, "detail": str(e)}, 502
    if not result.get("ok"):
        return result, 502
    # Local copy becomes a stub: host_node points at the new owner; the
    # watcher stops nudging from here; reads/posts proxy transparently.
    _core._update_group_chat_sidecar(real_path, host_node=peer["node_id"],
                               moved_at=time.time())
    with _core._coord_lock:
        _core._active_coordinations.pop(real_path, None)
    _core._group_chat_log_system(real_path,
                           f"chat ownership moved to node {peer['node_id'][:8]}")
    return {"ok": True, "uuid": chat_uuid, "host_node": peer["node_id"]}, 200


def _group_chat_checkin_text(real_path, topic, mode, sid, meta=None):
    """Check-in text for one participant — remote participants (global-ref
    sids owned by another node) get the uuid+host_node API variant instead
    of a machine-local file path."""
    if meta is None:
        meta = _core._load_group_chat_sidecar(real_path)
    owner, _native = federation.parse_session_ref(sid)
    remote = bool(owner and owner != federation.node_id())
    return _core._group_chat_inject_text(
        real_path, topic, mode, sid,
        chat_uuid=(meta or {}).get("uuid") or "",
        remote_host_node=((meta or {}).get("host_node") or federation.node_id())
        if remote else "")


def _coordinate_sessions(payload):
    """Create a group-chat file and inject /group-chat into selected sessions."""
    session_ids = payload.get("session_ids")
    if not isinstance(session_ids, list):
        session_ids = []
    topic = (payload.get("topic") or "").strip()
    mode = (payload.get("mode") or "topic").strip()
    sessions_meta = payload.get("sessions_meta") or []
    include_human = bool(payload.get("include_human", True))
    lane = (payload.get("lane") or "").strip() or None
    keywords = payload.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(k).strip() for k in keywords if str(k).strip()]

    if not topic:
        return {"ok": False, "error": "missing topic"}
    if mode not in ("topic", "git"):
        mode = "topic"
    # session_ids can be empty — the chat file + sidecar are still created
    # so the user can drag sessions in later or post into it as a solo room.
    # The /group-chat injection loop below is a no-op for an empty list.

    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60] or "chat"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    group_chats_dir = os.path.expanduser("~/.claude/group-chats")
    try:
        os.makedirs(group_chats_dir, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": f"cannot create group-chats dir: {exc}"}

    chat_path = os.path.join(group_chats_dir, f"{slug}-{ts}.md")
    chat_uuid = str(uuid.uuid4())

    name_map = {}
    for m in sessions_meta:
        if not isinstance(m, dict) or not m.get("session_id"):
            continue
        sid = str(m.get("session_id") or "").strip()
        label = _group_chat_storeable_display_name(m.get("display_name"), sid)
        if label:
            name_map[sid] = label
    participant_names = [
        name_map.get(sid) or _group_chat_fallback_agent_name(sid, session_ids)
        for sid in session_ids
    ]
    if include_human:
        participant_names.append("human")
    participants_str = ", ".join(f"`{n}`" for n in participant_names)

    now = datetime.now()
    day_name = now.strftime("%A")
    try:
        tz_name = datetime.now().astimezone().strftime("%Z")
    except Exception:
        tz_name = "local"
    full_ts = now.strftime(f"%Y-%m-%d {day_name} %H:%M:%S") + f" {tz_name}"

    header = (
        f"# Group Chat — {topic}\n"
        f"**Started:** {full_ts}\n"
        f"**Mode:** {mode}\n"
        f"**Participants:** {participants_str}\n"
    )
    try:
        with open(chat_path, "w", encoding="utf-8") as fh:
            fh.write(header)
    except OSError as exc:
        return {"ok": False, "error": f"cannot write chat file: {exc}"}

    self_node = federation.node_id()

    # Write the sidecar before injecting participants. An injected participant
    # can begin its check-in immediately, and that workflow needs name_map and
    # the session list from the sidecar to be available already.
    # name_map stored for loop detection: display_name → session_id reverse lookup.
    # archived/closed_at stay absent at registration time; the watcher fills
    # closed_at on idle/done drop and the archive endpoint flips archived.
    sidecar_path = chat_path[:-3] + ".json"
    try:
        with open(sidecar_path, "w", encoding="utf-8") as fh:
            json.dump({
                "uuid": chat_uuid,
                # The node that owns this chat's files. Reads/posts/nudges
                # for the chat proxy to the host; participants may live on
                # other nodes (global-ref session ids).
                "host_node": self_node,
                "session_ids": session_ids,
                "topic": topic,
                "mode": mode,
                "name_map": name_map,
                "include_human": include_human,
                "started_at": time.time(),
                "archived": False,
                "closed_at": None,
                "lane": lane,
                "keywords": keywords,
                "nudge_log": [],
                "read_state": {},
                "nudged_at": {},
            }, fh)
    except OSError as exc:
        return {"ok": False, "error": f"cannot write group-chat sidecar: {exc}"}

    # Register with the background watcher only after the sidecar is complete.
    _core._register_coordination(chat_path)

    results = []
    for sid in session_ids:
        owner, _native = federation.parse_session_ref(sid)
        remote = owner and owner != self_node
        text = _core._group_chat_inject_text(
            chat_path, topic, mode, sid, chat_uuid=chat_uuid,
            remote_host_node=self_node if remote else "")
        inject_result = _core._inject_text_into_session(sid, text, source="group-chat-coordinate")
        results.append({
            "session_id": sid,
            "ok": bool(inject_result.get("ok")),
            "error": inject_result.get("error", ""),
        })

    # System log line for the chat creation. Includes the initial
    # participants if there were any so the chat reads as a self-
    # contained timeline.
    if session_ids:
        added_labels = [
            f"`{_group_chat_participant_label(s, name_map, session_ids)}` ({s[:8]})"
            for s in session_ids
        ]
        _core._group_chat_log_system(
            chat_path,
            f"created chat with topic `{topic}` and added {', '.join(added_labels)}",
        )
    else:
        _core._group_chat_log_system(
            chat_path,
            f"created empty chat with topic `{topic}`",
        )

    _core._group_chat_update_header_if_changed(chat_path, force_write=True)
    chat_path_tilde = "~/.claude/group-chats/" + f"{slug}-{ts}.md"
    return {
        "ok": True,
        "chat_path": chat_path_tilde,
        "id": chat_uuid,
        "uuid": chat_uuid,
        "results": results,
    }


def _first_group_chat_content_boundary(content: str) -> int:
    """Return the first separator/message marker after the managed header."""
    positions = [idx for marker in ("\n---", "\n## ") if (idx := content.find(marker)) != -1]
    return min(positions) if positions else -1


def _is_group_chat_wake_status_line(line: str) -> bool:
    """Return True for lines written by the managed wake-status block."""
    if line == "- (no participants)":
        return True
    # Session ids are not uniformly UUIDs: Kimi uses a ``session_`` prefix.
    # Restrict the tail to statuses emitted by the writer, rather than assuming
    # the eight-character display prefix is hexadecimal, so each refresh can
    # replace the whole managed block instead of preserving its later rows.
    return bool(re.match(
        r"^- `.*` \([^)]+\): (?:online|offline(?: \(last active \d{4}-\d{2}-\d{2}\))?)$",
        line,
    ))


def _split_group_chat_header_for_rewrite(content: str) -> tuple[str, str, str]:
    """Split chat markdown into managed header, pre-boundary text, and body.

    Older chat files can have agent/system text before the first message
    separator. Header repair must not treat that text as disposable header.
    """
    boundary_idx = _first_group_chat_content_boundary(content)
    if boundary_idx != -1:
        header_candidate = content[:boundary_idx]
        rest_part = content[boundary_idx:]
    else:
        header_candidate = content
        rest_part = ""

    lines = header_candidate.splitlines()
    idx = 0
    if idx < len(lines) and lines[idx].startswith("# Group Chat"):
        idx += 1

    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            idx += 1
            continue
        if (
            line.startswith("**Started:**")
            or line.startswith("**Mode:**")
            or line.startswith("**Participants:**")
        ):
            idx += 1
            continue
        if line.startswith("**Wake-status:**"):
            idx += 1
            while idx < len(lines):
                wake_line = lines[idx]
                if not wake_line.strip() or _is_group_chat_wake_status_line(wake_line):
                    idx += 1
                    continue
                break
            continue
        break

    preserved_part = "\n".join(lines[idx:]).strip()
    return header_candidate, preserved_part, rest_part


def _group_chat_update_header_if_changed(chat_path, force_write=False):
    """Read the chat markdown file, compute the current wake status of all
    agent participants, and update the markdown header if it differs from
    what's on disk.
    """
    try:
        real_path = os.path.realpath(os.path.expanduser(chat_path))
    except Exception:
        return
    try:
        with open(real_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return

    sidecar_path = real_path[:-3] + ".json" if real_path.endswith(".md") else real_path + ".json"
    try:
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except Exception:
        return

    session_ids = meta.get("session_ids") or []
    name_map = _group_chat_enrich_name_map(real_path, meta)

    header_part, preserved_part, rest_part = _core._split_group_chat_header_for_rewrite(content)

    # Build the wake status block.
    wake_status_lines = ["**Wake-status:**"]
    for sid in session_ids:
        label = _group_chat_participant_label(sid, name_map, session_ids)
        pmeta = _core._group_chat_participant_meta(sid)
        if pmeta.get("is_live"):
            status_str = "online"
        else:
            last_activity = pmeta.get("last_activity")
            if last_activity and last_activity > 0:
                last_active_date = datetime.fromtimestamp(last_activity).strftime("%Y-%m-%d")
                status_str = f"offline (last active {last_active_date})"
            else:
                status_str = "offline"
        wake_status_lines.append(f"- `{label}` ({sid[:8]}): {status_str}")

    if not session_ids:
        wake_status_lines.append("- (no participants)")

    started_line = ""
    for line in header_part.splitlines():
        if line.startswith("**Started:**"):
            started_line = line
            break

    topic = (meta.get("topic") or "").strip()
    mode = (meta.get("mode") or "topic").strip() or "topic"
    header_lines = [f"# Group Chat — {topic}" if topic else "# Group Chat"]
    if started_line:
        header_lines.append(started_line)
    header_lines.append(f"**Mode:** {mode}")
    header_lines.append(f"**Participants:** {_core._group_chat_participants_str(meta, header_part)}")
    header_lines.extend(wake_status_lines)
    header_part = "\n".join(header_lines)

    content_parts = [header_part.rstrip()]
    if preserved_part:
        content_parts.append(preserved_part)
    if rest_part.strip():
        content_parts.append(rest_part.lstrip())
    new_content = "\n".join(content_parts) + "\n"

    if force_write or new_content != content:
        try:
            with open(real_path, "w", encoding="utf-8") as fh:
                fh.write(new_content)
        except OSError:
            return

        # Update the cached mtime to prevent a self-nudge loop
        try:
            new_mtime = os.stat(real_path).st_mtime
            with _core._coord_lock:
                entry = _core._active_coordinations.get(real_path)
                if entry is not None:
                    entry["mtime"] = new_mtime
        except Exception:
            pass


def _group_chat_nudge(path, chat_uuid="", target_sid=""):
    """Re-inject /group-chat into participant sessions, skipping the last writer.

    When `target_sid` is given, the auto-selection (last-writer detect,
    only-most-recent-mentioned, "no recent author → skip" guard) is
    bypassed and the function nudges exactly that participant. Used for
    the UI's per-participant Nudge button — "wake this specific agent
    up regardless of who spoke last".
    """
    real_path = _core._resolve_group_chat_ref(path, chat_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    # Honor the disable knob even if a stray caller reaches here.
    if _core._group_chat_is_paused(real_path):
        return {"ok": False, "error": "paused"}
    # Server-side debounce for non-targeted (auto) nudges: the reader polls
    # every 3s and fires /api/group-chat/nudge every 15s on content change,
    # bypassing the watcher's _COORD_NUDGE_INTERVAL debounce. Without this
    # check the reader can fire 4 nudges/minute — each one spawning a
    # claude --resume or keystrokes — causing the CPU-hog symptom on chat
    # creation. Targeted nudges (UI "Nudge" button with an explicit
    # target_sid) are exempt so the user can manually ping any participant.
    if not target_sid:
        with _core._coord_lock:
            _entry = _core._active_coordinations.get(real_path)
        if _entry is not None:
            _elapsed = time.time() - _entry.get("last_nudge", 0)
            if _elapsed < _core._COORD_NUDGE_INTERVAL:
                return {"ok": True, "debounced": True,
                        "reason": f"last nudge {_elapsed:.0f}s ago (interval {_core._COORD_NUDGE_INTERVAL}s)"}
    sidecar_path = real_path[:-3] + ".json" if real_path.endswith(".md") else real_path + ".json"
    try:
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"sidecar not found or invalid: {exc}"}
    session_ids = meta.get("session_ids") or []
    topic = meta.get("topic", "")
    mode = meta.get("mode", "topic")
    name_map = _group_chat_enrich_name_map(real_path, meta)  # session_id → display_name
    if not session_ids:
        return {"ok": False, "error": "no session_ids in sidecar"}

    reminder_key = ""
    # Targeted nudge from the UI — bypass the last-writer auto-select
    # entirely. The user explicitly picked which participant to wake.
    if target_sid:
        if target_sid not in session_ids:
            return {"ok": False, "error": f"target_sid {target_sid[:8]} not in chat participants"}
        results = []
        now_iso = datetime.now().astimezone().isoformat()
        message_key = ""
        try:
            with open(real_path, "r", encoding="utf-8") as fh:
                content = fh.read()
            message_key = _group_chat_auto_nudge_selection(
                content, session_ids, name_map
            ).get("reminder_key") or ""
        except OSError:
            pass
        if not message_key:
            message_key = f"manual:{time.time():.3f}"
        text = _group_chat_checkin_text(real_path, topic, mode, target_sid)
        r = _core._inject_text_into_session(target_sid, text, source="group-chat-manual-nudge")
        results.append({"session_id": target_sid, "ok": bool(r.get("ok")), "error": r.get("error", "")})
        # Reflect the manual nudge in the orchestrator panel: bump
        # both the in-memory last_nudge (used by the auto-nudge watcher
        # debounce) AND the sidecar last_reminder_at + targets so the
        # "Last nudge: X ago" row updates immediately and survives a
        # server restart.
        label = _group_chat_participant_label(target_sid, name_map, session_ids)
        latest_meta = _core._load_group_chat_sidecar(real_path)
        nudge_log = list(latest_meta.get("nudge_log") or [])
        nudge_log.append({
            "message_key": message_key,
            "sid": target_sid,
            "name": label,
            "ok": bool(r.get("ok")),
            "at": now_iso,
        })
        nudge_log = nudge_log[-500:]
        nudged_at = dict(latest_meta.get("nudged_at") or {})
        if r.get("ok"):
            nudged_at[target_sid] = now_iso
            with _core._coord_lock:
                entry = _core._active_coordinations.get(real_path)
                if entry is not None:
                    entry["last_nudge"] = time.time()
            try:
                _core._update_group_chat_sidecar(
                    real_path,
                    last_reminder_at=time.time(),
                    last_reminder_targets=[target_sid[:8]],
                    nudge_log=nudge_log,
                    nudged_at=nudged_at,
                )
            except Exception:
                pass
            _core._group_chat_log_system(real_path, f"pinged `{label}` ({target_sid[:8]})")
        else:
            try:
                _core._update_group_chat_sidecar(real_path, nudge_log=nudge_log)
            except Exception:
                pass
        return {"ok": True, "results": results, "targeted": True}
    try:
        with open(real_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        selection = _group_chat_auto_nudge_selection(content, session_ids, name_map)
        reminder_key = selection.get("reminder_key") or ""
        target_sids = list(selection.get("targets") or [])
        if selection.get("skipped") == "no recent author":
            return {"ok": True, "results": [], "skipped": "no recent author"}
    except OSError:
        target_sids = []

    results = []
    pinged_labels = []
    target_set = set(target_sids)
    addressed_sids = selection.get("addressed_sids") or set() if 'selection' in locals() else set()
    for sid in session_ids:
        if sid not in target_set:
            reason = "not addressed" if addressed_sids else "last writer"
            results.append({"session_id": sid, "ok": True, "skipped": reason})
            continue

    if reminder_key and target_sids:
        with _core._coord_lock:
            latest_meta = _core._load_group_chat_sidecar(real_path)
            if latest_meta.get("last_reminder_key") == reminder_key:
                for sid in target_sids:
                    results.append({"session_id": sid, "ok": True, "skipped": "already reminded"})
                return {"ok": True, "results": results, "skipped": "already reminded"}
            _core._update_group_chat_sidecar(
                real_path,
                last_reminder_key=reminder_key,
                last_reminder_at=time.time(),
                last_reminder_targets=[sid[:8] for sid in target_sids],
            )

    failed_labels = []
    log_entries = []
    nudged_at = {}
    for sid in target_sids:
        now_iso = datetime.now().astimezone().isoformat()
        label = _group_chat_participant_label(sid, name_map, session_ids)
        text = _group_chat_checkin_text(real_path, topic, mode, sid)
        r = _core._inject_text_into_session(sid, text, source="group-chat-auto-nudge")
        results.append({"session_id": sid, "ok": bool(r.get("ok")), "error": r.get("error", "")})
        log_entries.append({
            "message_key": reminder_key or f"auto:{time.time():.3f}",
            "sid": sid,
            "name": label,
            "ok": bool(r.get("ok")),
            "at": now_iso,
        })
        if r.get("ok"):
            pinged_labels.append(f"`{label}` ({sid[:8]})")
            nudged_at[sid] = now_iso
        else:
            # Surface the failure in-chat. A silently-dropped nudge (offline
            # Codex routed through resume, a tty-less Claude session, a dead
            # spawn) used to leave NO trace at all — the chat looked idle
            # while CCC quietly failed to wake anyone. Log a short reason so
            # "pinging doesn't work" is observable instead of invisible.
            reason = str(r.get("code") or r.get("error") or "delivery failed").strip()
            reason = re.sub(r"\s+", " ", reason)[:120]
            failed_labels.append(f"`{label}` ({sid[:8]}): {reason}")
    if log_entries:
        latest_meta = _core._load_group_chat_sidecar(real_path)
        existing_log = list(latest_meta.get("nudge_log") or [])
        existing_nudged_at = dict(latest_meta.get("nudged_at") or {})
        existing_log.extend(log_entries)
        existing_nudged_at.update(nudged_at)
        _core._update_group_chat_sidecar(
            real_path,
            nudge_log=existing_log[-500:],
            nudged_at=existing_nudged_at,
        )
    # Log AFTER the inject so the baseline-mtime bump doesn't race
    # with the inject's own potential file changes. The bump prevents
    # the watcher from seeing this admin write as a real activity tick
    # — without it, the watcher would re-fire a nudge after the
    # debounce window, this function would log it again, and the
    # chat would gain a "pinged" line every minute even when nothing
    # else is happening. The last_reminder_key dedup above caps this at
    # one pinged/failed line per new post, so failures don't spam either.
    if pinged_labels:
        _core._group_chat_log_system(real_path, f"pinged {', '.join(pinged_labels)}")
    if failed_labels:
        _core._group_chat_log_system(real_path, f"nudge FAILED — {'; '.join(failed_labels)}")
    return {"ok": True, "results": results}
