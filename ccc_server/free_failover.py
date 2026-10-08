# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Limit-hit failover: keep working on a $0 model, with explicit approval.

When the usage-limit watcher (ccc_server/usage_limit.py) parks a session on a
rate-limit stop, this module owns the user's choices:

  * **Continue free** — resume the SAME session on a free model.
    Claude sessions resume via ``claude --resume`` (same transcript, full
    context) with the free-router env injected per the L01 contract
    (``ccc_server.free_router.spawn_env()``), falling back to a self-hosted
    router configured via ``CCC_FREE_ROUTER_BASE_URL`` /
    ``CCC_FREE_ROUTER_TOKEN`` / ``CCC_FREE_ROUTER_MODEL`` env vars.
    Devin sessions switch model via ACP ``session/set_config_option`` (or a
    ``devin --resume --model`` one-shot) onto another ``cost_tier == "Free"``
    catalog model, and the pick is persisted as the session's model override.
  * **Resume automatically at reset** — an explicitly approved auto-resume:
    the user clicks once, CCC sends "continue" the moment the limit clears.
    Many sessions can be parked on the same account-wide reset, so fires are
    staggered at most ``AUTO_RESUME_MAX_PER_MINUTE`` per minute — firing every
    lane at once is exactly how you hit the limit a second time.
  * **Switch back** — once the paid limit's reset time passes while a session
    runs free, CCC offers to move it back: retire the free warm process
    (deferred while a turn is in flight) or clear the Devin model override.
    No prompt is sent, so a switch-back never burns a turn just to swap env.

Detection additionally covers Devin's own free-model wall (the ACP transcript
``result/error`` shape, e.g. "Reached free model rate limit. ... Your limit
will reset in 52 minutes (at 06:22 UTC).") with a real parsed reset time.

Nothing here ever fires unattended: every send requires a prior user click
(or the per-session "always" opt-in that user granted for this session).

Names still living in server.py are reached via ``_core`` at call time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import fcntl
import json
import math
import os
import re
import threading
import time
import uuid

from ccc_server import core as _core
from ccc_server import limit_events as _limit_events


FREE_FAILOVER_FILENAME = "free-failover.json"

# Devin's free-model wall, as emitted to CCC's ACP transcript as a
# {"type":"result","subtype":"error"} row, e.g.:
#   "Reached free model rate limit. Upgrade to Max for higher limits, or
#    switch to a different model. Your limit will reset in 52 minutes
#    (at 06:22 UTC)."
_DEVIN_LIMIT_RE = re.compile(
    r"(Reached free model rate limit|rate.?limit|usage.?limit|quota|"
    r"too many requests|429)",
    re.IGNORECASE,
)
# "Your limit will reset in 52 minutes (at 06:22 UTC)" — the parenthesised
# UTC clock time is the precise one; the relative "N minutes" is the backup.
_DEVIN_RESET_AT_RE = re.compile(
    r"reset[^()]*\(at\s+(\d{1,2}):(\d{2})\s*UTC\)", re.IGNORECASE
)
_DEVIN_RESET_IN_RE = re.compile(
    r"reset\s+in\s+(\d+)\s*(minute|hour|min|hr|day|week)", re.IGNORECASE
)

# Reset-time parsing shared with the devin detector's candidate window.
_FREE_FAILOVER_CANDIDATE_WINDOW_SECS = 24 * 3600

# Auto-resume-at-reset: fire at most this many sessions per 60s slot so a
# whole fleet parked on one reset doesn't re-hit the limit as a burst.
AUTO_RESUME_MAX_PER_MINUTE = 5

# A failover record older than this is dropped on the next pass — sessions
# are archival forever, the "is running free" fact is not.
_FAILOVER_RECORD_TTL_S = 30 * 86400

# free_ready is a (possibly subprocess-touching) probe; cache briefly so row
# and status serialization stay cheap.
_FREE_READY_TTL_S = 15.0

_free_failover_lock = threading.Lock()
_free_failover_cache = {"data": None}
_free_ready_cache = {"ts": 0.0, "data": None}


def _free_failover_file():
    return _core.COMMAND_CENTER_STATE_DIR / FREE_FAILOVER_FILENAME


def _load_free_failovers():
    """Return {session_id: record} from the durable failover store.

    In-memory cache refreshed lazily on every write (own or a sibling
    process's, via the file). Tolerant of a missing/malformed file."""
    return _limit_events.load_store(
        _free_failover_file(), _free_failover_cache, _free_failover_lock,
    )


def _free_failover_cache_clear():
    """Tests + post-write sync: drop the in-memory copy so the next read
    re-loads from disk."""
    with _free_failover_lock:
        _free_failover_cache["data"] = None


def _free_failover_rewrite(mutate):
    """Read-modify-write the failover store under an flock (cross-process
    safe — dashboard and worker both run the watcher). `mutate` takes the
    current dict and returns (new_dict, retval)."""
    path = _free_failover_file()
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            try:
                existing = json.loads(path.read_text()) if path.exists() else {}
            except (OSError, json.JSONDecodeError):
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            new_data, retval = mutate(existing)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(new_data, indent=2))
            os.replace(tmp, path)
            with _free_failover_lock:
                _free_failover_cache["data"] = new_data
                _free_failover_cache["signature"] = _limit_events.signature(path)
            return retval
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def _free_failover_save(sid, fields):
    """Merge `fields` into the record for `sid`."""
    if not sid:
        return

    def _mutate(existing):
        cur = dict(existing.get(sid) or {})
        cur.update(fields)
        existing[sid] = cur
        return existing, None

    _free_failover_rewrite(_mutate)


def _normalize_sid(session_id):
    sid = str(session_id or "").strip()
    if sid.startswith("session_"):
        sid = sid[len("session_"):]
    return sid


# ---------------------------------------------------------------------------
# Free-model plumbing
# ---------------------------------------------------------------------------

def _free_spawn_env():
    """Env vars that route a spawned Claude child to a $0 model, or {}.

    Primary source: the L01 contract — ``ccc_server.free_router.spawn_env()``
    returns the unified-key env when the managed router is installed, running
    and healthy, else {}. Fallback: a self-hosted Anthropic-compatible router
    declared via env (also the dev/test seam for driving a failover without
    the managed install). Never invent Anthropic vars beyond what the source
    returns — a partial env that leaves ANTHROPIC_API_KEY inherited would
    leak the user's paid key into router-bound traffic.
    """
    env = {}
    try:
        from ccc_server import free_router  # L01; absent until that lane lands
        fn = getattr(free_router, "spawn_env", None)
        if callable(fn):
            env = fn() or {}
    except Exception:
        env = {}
    if isinstance(env, dict) and env.get("ANTHROPIC_BASE_URL"):
        return dict(env)
    base = str(os.environ.get("CCC_FREE_ROUTER_BASE_URL") or "").strip()
    if not base:
        return {}
    env = {"ANTHROPIC_BASE_URL": base}
    token = str(os.environ.get("CCC_FREE_ROUTER_TOKEN") or "").strip()
    if token:
        env["ANTHROPIC_AUTH_TOKEN"] = token
    model = str(os.environ.get("CCC_FREE_ROUTER_MODEL") or "").strip()
    if model:
        env["ANTHROPIC_MODEL"] = model
    return env


def _free_ready_info():
    """TTL-cached {ready, model, via} so status calls never stampede the
    free-router probe. Never exposes env values — only whether they exist."""
    now = time.monotonic()
    cached = _free_ready_cache["data"]
    if cached is not None and now - _free_ready_cache["ts"] < _FREE_READY_TTL_S:
        return cached
    env = _core._free_spawn_env()
    info = {"ready": bool(env), "model": None, "via": None}
    if env:
        info["model"] = env.get("ANTHROPIC_MODEL") or None
        info["via"] = "router" if "CCC_FREE_ROUTER_BASE_URL" not in os.environ else "env"
    _free_ready_cache["ts"] = now
    _free_ready_cache["data"] = info
    return info


def _free_ready_cache_clear():
    _free_ready_cache["ts"] = 0.0
    _free_ready_cache["data"] = None


def _devin_free_model_candidates(current_model=None):
    """Free-tier Devin model uids, best first, excluding `current_model`.

    Sourced from the live ``devin models list`` catalog (same cache the
    model picker reads). Preference order inside the free tier favours the
    strongest SWE generation, then anything else flagged Free."""
    current = str(current_model or "").strip().lower()
    try:
        data = _core._devin_model_list_json()
    except Exception:
        data = None
    free = []
    for fam in (data or {}).get("families") or []:
        for variant in fam.get("variants") or []:
            uid = str(variant.get("model_uid") or "").strip().lower()
            if not uid:
                continue
            tier = str(variant.get("cost_tier") or "").strip().lower()
            if tier == "free" and uid != current:
                free.append(uid)

    def _rank(uid):
        # Strongest free tiers first; unknowns sort last but stay eligible.
        if uid.startswith("swe-2"):
            return 0
        if uid.startswith("swe-"):
            return 1
        return 2

    free.sort(key=lambda u: (_rank(u), u))
    if not free:
        # Catalog unreachable (lapsed auth, no CLI) — still offer the known
        # free SWE-2 variants so the button isn't dead on a stale cache.
        free = [
            uid for uid in ("swe-2-medium", "swe-2-high", "swe-2-max")
            if uid != current
        ]
    return free


# ---------------------------------------------------------------------------
# Devin limit detection (CCC ACP transcript tail)
# ---------------------------------------------------------------------------

def _devin_reset_epoch(error_text, detected_at):
    """Parse the reset time out of Devin's free-model wall text.

    "Your limit will reset in 52 minutes (at 06:22 UTC)" → prefer the
    explicit UTC clock time (same UTC day as detection; if it already
    passed, the window rolls to tomorrow). Falls back to the relative
    "in N minutes|hours" count, then detected_at + 5h (estimated)."""
    blob = str(error_text or "")
    m = _DEVIN_RESET_AT_RE.search(blob)
    if m:
        try:
            hh, mm = int(m.group(1)), int(m.group(2))
            base = datetime.fromtimestamp(detected_at, tz=timezone.utc)
            reset = base.replace(hour=hh % 24, minute=mm, second=0, microsecond=0)
            if reset.timestamp() <= detected_at:
                reset = reset.timestamp() + 86400
                return reset, False
            return reset.timestamp(), False
        except (ValueError, OverflowError):
            pass
    m = _DEVIN_RESET_IN_RE.search(blob)
    if m:
        try:
            n = int(m.group(1))
            unit = m.group(2).lower()
            delta = n * (604800 if unit.startswith("w") else 86400 if unit.startswith("d") else 3600 if unit.startswith("h") else 60)
            if delta > 0:
                return detected_at + delta, False
        except (ValueError, OverflowError):
            pass
    return detected_at + 5 * 3600, True


def _free_failover_devin_candidates(now):
    """Recent Devin ACP transcripts → (ccc_session_id, path).

    Devin's limit event only lands on CCC's normalized ACP copy
    (acp/devin/<raw>.jsonl), not in the CLI's own sessions.db — so this is
    the only place a stopped lane is visible. Row sids carry the
    ``devincli-`` prefix; the file stem is the raw id."""
    out = []
    acp_dir = _core.COMMAND_CENTER_STATE_DIR / "acp" / "devin"
    try:
        for p in acp_dir.glob("*.jsonl"):
            try:
                if now - p.stat().st_mtime <= _FREE_FAILOVER_CANDIDATE_WINDOW_SECS:
                    out.append((f"devincli-{p.stem}", p))
            except OSError:
                continue
    except OSError:
        pass
    out += _limit_events.capture_candidates("devin", now)
    return list({(sid, str(path)): (sid, path) for sid, path in out}.values())


def _detect_devin_usage_limit_stop(session_id, path):
    """Tail a Devin ACP transcript for a free-model rate-limit stop.

    Shape (confirmed 2026-10-06 against the real overnight incident):
      {"type":"result","subtype":"error",
       "error":"Reached free model rate limit. ... will reset in N
                minutes (at HH:MM UTC)."}
    A non-error result or a non-limit error after it supersedes the stop."""
    try:
        tail_fn = getattr(_core, "_tail_read_lines", None)
        lines = tail_fn(path) if callable(tail_fn) else _tail_lines_local(path)
    except Exception:
        return None
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            if path.suffix != ".log" or not re.match(r"^(Reached free model rate limit|Error:.*(?:rate.?limit|usage.?limit|quota))", line, re.IGNORECASE):
                continue
            ev = {"type": "result", "subtype": "error", "error": line}
        if not isinstance(ev, dict):
            continue
        if ev.get("type") != "result":
            continue
        if ev.get("subtype") != "error":
            return None  # most recent result was a clean stop
        err = str(ev.get("error") or "")
        if not _DEVIN_LIMIT_RE.search(err):
            return None
        dt = _core._stats_parse_ts(ev.get("ts") or ev.get("timestamp"))
        detected_at = dt.timestamp() if dt is not None else path.stat().st_mtime
        resume_at, estimated = _devin_reset_epoch(err, detected_at)
        return _limit_events._attach({
            "engine": "devin",
            "detected_at": detected_at,
            "resume_at": resume_at,
            "resume_at_estimated": estimated,
            "source_text_snippet": err[:200],
        }, session_id, path)
    return None


def _tail_lines_local(path, max_bytes=32768):
    """Same contract as usage_limit._tail_read_lines; used only if that
    helper isn't reachable via _core (e.g. a stripped worker env)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            raw = f.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", "replace").split("\n")
    if len(lines) > 1 and size > max_bytes:
        lines = lines[1:]
    return [ln for ln in (x.strip() for x in lines) if ln]


def _free_failover_scan_devin(now):
    """Fold Devin limit stops into the shared usage-limit store so rows
    light up with usage_limit_resume_at exactly like claude/codex/kimi."""
    tracked = _core._load_usage_limit_resumes()
    for sid, path in _core._free_failover_devin_candidates(now):
        if not sid:
            continue
        existing = tracked.get(sid)
        if existing and existing.get("dismissed"):
            continue
        try:
            found = _limit_events.cached_stop("devin", sid, path, _core._detect_devin_usage_limit_stop)
        except Exception:
            continue
        if not found:
            if existing and existing.get("transcript_path") == str(path) and not existing.get("fired"):
                _core._clear_usage_limit_resume(sid)
            continue
        if existing and existing.get("detected_at") == found["detected_at"]:
            continue
        _core._save_usage_limit_resume_entry(sid, found)


# ---------------------------------------------------------------------------
# Transcript marker
# ---------------------------------------------------------------------------

def _free_failover_marker(session_id, event, text, model=None):
    """Append a small system marker to the session's transcript.

    Claude JSONL: a {"type":"system","subtype":"ccc_free_runtime"} event —
    rendered as a compact banner in the transcript view and ignored by
    `claude --resume` (unknown system subtypes are skipped, same tolerance
    that lets foreign tooling lines coexist). Devin ACP transcripts are
    CCC-owned normalized caches, so the same append is safe there."""
    engine, sid = _free_failover_engine_and_raw(session_id)
    path = None
    if engine == "claude":
        path = _core._usage_limit_session_path("claude", sid)
    elif engine == "devin":
        path = _core._acp_transcript_path("devin", sid)
    if not path:
        return False
    try:
        line = {
            "type": "system",
            "subtype": "ccc_free_runtime",
            "event": event,
            "text": text,
            "model": model or "",
            "timestamp": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "ts": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "uuid": str(uuid.uuid4()),
            "sessionId": session_id,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False


def _free_failover_engine_and_raw(session_id):
    """(engine, transport_id) for a tracked sid: devincli-* → ("devin", raw);
    everything else is keyed by the transcript id directly."""
    sid = _normalize_sid(session_id)
    if sid.startswith("devincli-"):
        return "devin", sid[len("devincli-"):]
    return "claude", sid


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def free_failover_continue(session_id, always=False, auto=False):
    """Resume a limit-stopped session on a free model (user-approved).

    Claude: retire the paid warm headless if one lingers, then resume the
    session with the free-router env. Devin: swap the session's model to a
    free catalog uid (ACP set_config on a live attach, else the
    ``devin --resume --model`` one-shot) and persist it as the session
    override so later resumes keep the free pick until switch-back."""
    sid_for_rows = _normalize_sid(session_id)
    if not sid_for_rows:
        return {"ok": False, "error": "missing session_id"}
    tracked = _core._load_usage_limit_resumes()
    entry = tracked.get(sid_for_rows) or {}
    failover = _load_free_failovers().get(sid_for_rows) or {}
    # Engine comes from the tracked stop first (codex/kimi/claude/devin are
    # unambiguous there); the sid prefix is only a fallback for a session
    # already mid-failover whose tracked entry was cleared.
    engine = (
        entry.get("engine")
        or failover.get("engine")
        or _free_failover_engine_and_raw(sid_for_rows)[0]
    )
    _engine_prefix, raw = _free_failover_engine_and_raw(sid_for_rows)
    if engine not in ("claude", "devin"):
        return {
            "ok": False, "code": "unsupported_engine",
            "error": "Free failover is only available for Claude and Devin sessions.",
        }

    already_free = failover.get("state") == "free"

    free_env = {}
    free_uid = None
    if engine == "claude":
        free_env = _core._free_spawn_env()
        if not free_env and not already_free:
            return {
                "ok": False, "code": "free_not_ready",
                "error": "No free model is set up yet. Add one in Settings, then try again.",
            }
    else:
        current_model = entry.get("model") or failover.get("origin_model")
        candidates = _core._devin_free_model_candidates(current_model)
        free_uid = candidates[0] if candidates else None
        if not free_uid and not already_free:
            return {
                "ok": False, "code": "free_not_ready",
                "error": "No free Devin model is available right now.",
            }

    if not already_free:
        if engine == "claude":
            # A warm paid headless would happily answer "continue" on the
            # still-limited paid model — retire it first so the resume
            # spawns a fresh process carrying the free env. Busy is refused,
            # never killed mid-turn.
            ret = _core._retire_idle_headless_for_session(
                sid_for_rows, reason="free-failover",
            )
            if not ret.get("retired") and ret.get("reason"):
                return {
                    "ok": False, "code": "busy",
                    "error": "This session is still finishing a step. Try again in a moment.",
                    "reason": ret.get("reason"),
                }
            resume_cwd = entry.get("cwd") or failover.get("cwd") or None
            if not resume_cwd:
                try:
                    resume_cwd = _core.find_session_cwd(sid_for_rows)
                except Exception:
                    resume_cwd = None
            result = _core.resume_session_headless(
                sid_for_rows, "continue",
                cwd=resume_cwd,
                extra_env=free_env,
            )
            if not result.get("ok"):
                return {
                    "ok": False,
                    "code": result.get("code") or "resume_failed",
                    "error": result.get("error") or "Could not resume the session.",
                }
            _free_failover_marker(
                sid_for_rows, "failover_start",
                "Continued on a free model ($0) — your Claude plan's limit "
                "was reached.",
            )
            free_pid = result.get("pid")
            free_label = free_env.get("ANTHROPIC_MODEL") or "free model"
        else:
            result = _free_failover_devin_continue(
                sid_for_rows, raw, free_uid, entry,
            )
            if not result.get("ok"):
                return result
            free_pid = result.get("pid")
            free_label = free_uid
            _free_failover_marker(
                sid_for_rows, "failover_start",
                f"Continued on free model {free_uid} — the previous model "
                "hit its limit.",
                model=free_uid,
            )
    else:
        free_pid = failover.get("free_pid")
        free_label = failover.get("free_model")

    now = time.time()
    if entry and not entry.get("fired"):
        # Consume the tracked stop so the countdown clears — the session is
        # running again, just on a different meter.
        _core._mark_usage_limit_resume_fired(sid_for_rows)
    _free_failover_save(sid_for_rows, {
        "state": "free",
        "always": bool(always or failover.get("always")),
        "free_since": failover.get("free_since") or now,
        "origin_detected_at": entry.get("detected_at")
            or failover.get("origin_detected_at") or now,
        "origin_resume_at": entry.get("resume_at")
            or failover.get("origin_resume_at"),
        "origin_model": entry.get("model") or failover.get("origin_model"),
        "free_pid": free_pid,
        "free_model": free_label,
        "engine": engine,
        "offer": None,
        "offer_dismissed_at": None,
        "switch_back_dismissed_at": None,
        "auto_resume": None,
        "auto_resume_fire_at": None,
        "last_auto_detected_at": (
            entry.get("detected_at") if auto
            else failover.get("last_auto_detected_at")
        ),
        "last_auto_attempt_at": now if auto else failover.get("last_auto_attempt_at"),
        "last_auto_error": None,
        "display_name": entry.get("display_name") or failover.get("display_name"),
        "cwd": entry.get("cwd") or failover.get("cwd"),
    })
    try:
        _core._log_activity(
            "free-failover", "CONTINUE_FREE",
            f"session={sid_for_rows} engine={engine} model={free_label} "
            f"always={bool(always or failover.get('always'))} auto={auto}",
        )
    except Exception:
        pass
    return {
        "ok": True,
        "session_id": sid_for_rows,
        "engine": engine,
        "free_model": free_label,
        "pid": free_pid,
        "resumed": True,
        "auto": bool(auto),
    }


def _free_failover_devin_continue(sid, raw_id, free_uid, tracked_entry):
    """Move a stopped Devin session onto `free_uid` and send "continue".

    Preferred path is the live ACP attach (session/load → set model →
    prompt), which keeps CCC's session state and history. Fallback is the
    one-shot ``devin --resume --model <uid> -p continue``. The override is
    persisted either way so the *next* resume keeps the free pick."""
    cwd = (
        tracked_entry.get("cwd")
        or _core._devin_cli_session_cwd(raw_id)
        or ""
    )
    via = None
    if _core._devin_acp_steer_capable():
        attach_err = _core._acp_ensure_session_loaded("devin", raw_id)
        if attach_err is None:
            set_res = _core._acp_set_config("devin", raw_id, "model", free_uid)
            if set_res.get("ok"):
                via = "acp"
    if via is None:
        result = _core.resume_session_devin(sid, "continue", model=free_uid)
        if not result.get("ok"):
            return {
                "ok": False,
                "code": result.get("code") or "resume_failed",
                "error": result.get("error") or "Could not resume the session.",
            }
        via = "resume"
    else:
        result = _core._acp_prompt(
            "devin", raw_id, "continue", mode="send", cwd=cwd or None,
        )
        if not result.get("ok"):
            # ACP attached but the prompt didn't land — the one-shot resume
            # owns the session lock briefly, so it's still a valid fallback.
            result = _core.resume_session_devin(sid, "continue", model=free_uid)
            if not result.get("ok"):
                return {
                    "ok": False,
                    "code": result.get("code") or "resume_failed",
                    "error": result.get("error") or "Could not resume the session.",
                }
            via = "resume"
    try:
        _core._set_session_override(sid, free_uid, False, "devin")
    except Exception:
        pass
    return {
        "ok": True,
        "via": via,
        "pid": result.get("pid"),
        "free_model": free_uid,
    }


def free_failover_arm(session_id, armed=True):
    """Approve (or cancel) an automatic "continue" at the limit's reset.

    This is the user-consented version of the killed unattended auto-send:
    it only fires because the human clicked, per session, per stop."""
    sid = _normalize_sid(session_id)
    if not sid:
        return {"ok": False, "error": "missing session_id"}
    tracked = _core._load_usage_limit_resumes()
    entry = tracked.get(sid)
    if not entry:
        return {
            "ok": False, "code": "no_limit_stop",
            "error": "This session isn't parked on a limit reset.",
        }
    if entry.get("dismissed"):
        return {
            "ok": False, "code": "dismissed",
            "error": "The limit countdown for this session was dismissed.",
        }
    if entry.get("fired"):
        return {
            "ok": False, "code": "already_resolved",
            "error": "This limit stop already resolved.",
        }

    def _mutate(existing):
        cur = dict(existing.get(sid) or {})
        if armed:
            cur["auto_resume"] = True
            cur["auto_resume_armed_at"] = time.time()
            cur["auto_resume_done"] = False
            cur.pop("auto_resume_fire_at", None)
            cur["engine"] = entry.get("engine") or cur.get("engine")
            cur["auto_resume_detected_at"] = entry.get("detected_at")
            cur["auto_resume_at"] = entry.get("resume_at")
            cur["display_name"] = entry.get("display_name") or cur.get("display_name")
        else:
            cur["auto_resume"] = False
            cur.pop("auto_resume_fire_at", None)
            cur.pop("auto_resume_done", None)
        existing[sid] = cur
        return existing, None

    _free_failover_rewrite(_mutate)
    try:
        _core._log_activity(
            "free-failover", "ARM" if armed else "DISARM",
            f"session={sid} resume_at={entry.get('resume_at')}",
        )
    except Exception:
        pass
    return {"ok": True, "session_id": sid, "armed": bool(armed)}


def free_failover_dismiss(session_id, offer="failover"):
    """Dismiss a card without acting. `offer` = "failover" hides the
    continue-free card for THIS stop (a fresh stop re-offers); "switch_back"
    suppresses the reset offer for this failover cycle."""
    sid = _normalize_sid(session_id)
    if not sid:
        return {"ok": False, "error": "missing session_id"}
    fields = {}
    if offer == "switch_back":
        fields["switch_back_dismissed_at"] = time.time()
    else:
        fields["offer_dismissed_at"] = time.time()
    _free_failover_save(sid, fields)
    return {"ok": True, "session_id": sid, "dismissed": offer}


def free_failover_switch_back(session_id):
    """Move a free-running session back to its paid plan/model.

    Claude: retire the warm free headless (deferred while a turn is in
    flight — the next resume then spawns with the normal env). Devin: the
    resume is a one-shot, so "switching" only clears the model override and
    best-effort restores the ACP model — nothing to kill, no wasted turn.
    """
    sid = _normalize_sid(session_id)
    if not sid:
        return {"ok": False, "error": "missing session_id"}
    failover = _load_free_failovers().get(sid) or {}
    engine = (
        failover.get("engine")
        or _free_failover_engine_and_raw(sid)[0]
    )
    _ep, raw = _free_failover_engine_and_raw(sid)
    if failover.get("state") not in ("free", "switch_back_pending"):
        return {
            "ok": False, "code": "not_free",
            "error": "This session isn't running on a free model.",
        }

    if engine == "devin":
        try:
            _core._clear_session_override(sid)
        except Exception:
            pass
        origin = failover.get("origin_model")
        if origin and _core._devin_acp_steer_capable():
            try:
                if _core._acp_ensure_session_loaded("devin", raw) is None:
                    _core._acp_set_config("devin", raw, "model", origin)
            except Exception:
                pass
        _free_failover_finalize_switch_back(sid)
        return {"ok": True, "session_id": sid, "pending": False}

    ret = _core._retire_idle_headless_for_session(
        sid, reason="free-switch-back", defer_if_busy=True,
    )
    if ret.get("deferred"):
        _free_failover_save(sid, {
            "state": "switch_back_pending",
            "offer": None,
            "pending_reason": ret.get("reason") or "busy",
        })
        return {
            "ok": True, "session_id": sid, "pending": True,
            "reason": ret.get("reason") or "busy",
        }
    _free_failover_finalize_switch_back(sid)
    return {"ok": True, "session_id": sid, "pending": False}


def _free_failover_finalize_switch_back(session_id):
    sid = _normalize_sid(session_id)
    _free_failover_marker(
        sid, "failover_back",
        "Switched back to your plan — free-model failover ended.",
    )

    def _mutate(existing):
        cur = dict(existing.get(sid) or {})
        for key in (
            "state", "offer", "free_pid", "free_model", "pending_reason",
            "origin_model", "origin_resume_at", "origin_detected_at",
        ):
            cur.pop(key, None)
        if cur.get("always"):
            cur["state"] = "done"  # keep `always` for the next stop
        existing[sid] = cur
        return existing, None

    _free_failover_rewrite(_mutate)
    try:
        _core._log_activity(
            "free-failover", "SWITCH_BACK", f"session={sid}",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Watcher pass (hooked at the end of _usage_limit_scan_once)
# ---------------------------------------------------------------------------

def _free_failover_auto_pass(now=None):
    """One pass over the failover store + detected stops. Runs on the
    usage-limit watcher's 45s cadence (same loop, dashboard AND worker —
    the atomic _mark_usage_limit_resume_fired and the flock'd store make a
    double-fire impossible)."""
    now = now if now is not None else time.time()
    try:
        _free_failover_scan_devin(now)
    except Exception:
        pass

    tracked = _core._load_usage_limit_resumes()
    failovers = _load_free_failovers()
    if not isinstance(failovers, dict):
        return

    # 1. Prune records older than the TTL.
    stale = [
        sid for sid, rec in failovers.items()
        if not isinstance(rec, dict)
        or (
            not rec.get("state") in ("free", "switch_back_pending")
            and not rec.get("auto_resume")
            and not rec.get("always")
            and now - float(
                rec.get("offer_dismissed_at") or rec.get("free_since")
                or rec.get("last_switch_back_at") or 0
            ) > _FAILOVER_RECORD_TTL_S
        )
    ]
    if stale:
        def _prune(existing):
            for s in stale:
                existing.pop(s, None)
            return existing, None
        _free_failover_rewrite(_prune)
        failovers = _load_free_failovers()

    # 2. "Always" sessions: a newly detected stop auto-continues free.
    for sid, entry in tracked.items():
        if entry.get("fired") or entry.get("dismissed"):
            continue
        engine, _raw = _free_failover_engine_and_raw(sid)
        rec = failovers.get(sid) or {}
        if engine == "devin" and entry.get("engine") != "devin":
            continue  # never auto-apply a devin pref to a different engine
        if not rec.get("always"):
            continue
        detected = entry.get("detected_at") or 0
        if rec.get("state") == "free" and rec.get("last_auto_detected_at") == detected:
            continue
        if rec.get("last_auto_detected_at") == detected:
            # Already attempted this stop — retry at most every 10 minutes.
            if now - float(rec.get("last_auto_attempt_at") or 0) < 600:
                continue
        res = _core.free_failover_continue(sid, always=True, auto=True)
        if not res.get("ok"):
            _free_failover_save(sid, {
                "last_auto_detected_at": detected,
                "last_auto_attempt_at": now,
                "last_auto_error": res.get("error") or "failed",
            })
        failovers = _load_free_failovers()

    # 3. Armed auto-resumes: compute each session's staggered fire slot and
    #    fire those whose time has come.
    armed = []
    for sid, rec in failovers.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("auto_resume") and not rec.get("auto_resume_done"):
            entry = tracked.get(sid)
            if entry is None or entry.get("dismissed"):
                # The tracked stop vanished (session resumed on its own or
                # was dismissed) — drop the arming with it.
                _free_failover_save(sid, {"auto_resume": False})
                continue
            armed.append((sid, rec, entry))
    armed.sort(key=lambda t: str(t[0]))  # deterministic slot order
    scheduled = _free_failover_schedule_armed(tracked, now) if armed else {}
    for sid, rec, entry in armed:
        fire_at = scheduled.get(sid)
        if fire_at is None or now < fire_at:
            continue
        _free_failover_fire_auto_resume(sid, entry)

    # 4. Pending switch-backs finalize once the free process is gone.
    for sid, rec in (_load_free_failovers() or {}).items():
        if not isinstance(rec, dict) or rec.get("state") != "switch_back_pending":
            continue
        engine, _raw = _free_failover_engine_and_raw(sid)
        if engine != "claude":
            continue
        try:
            live = _core._find_live_spawn_entry_for_session(sid)
        except Exception:
            live = None
        if live is None:
            _free_failover_finalize_switch_back(sid)


def _free_failover_schedule_armed(tracked, now):
    def _num(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) else None

    def mutate(existing):
        slots = []
        for rec in existing.values():
            if not isinstance(rec, dict):
                continue
            fired_at = _num(rec.get("auto_resume_fired_at"))
            if fired_at is not None and fired_at > now - 60:
                slots.append(fired_at)
        eligible = []
        for sid, rec in existing.items():
            if not isinstance(rec, dict) or not rec.get("auto_resume") or rec.get("auto_resume_done"):
                continue
            entry = tracked.get(sid)
            if (
                not isinstance(entry, dict)
                or entry.get("fired")
                or entry.get("dismissed")
                or rec.get("auto_resume_detected_at") != entry.get("detected_at")
            ):
                rec["auto_resume"] = False
                continue
            reset = _num(entry.get("resume_at"))
            if reset is None:
                continue
            eligible.append((sid, rec, reset, _num(rec.get("auto_resume_fire_at"))))
        eligible.sort(key=lambda t: (
            t[3] if t[3] is not None else max(t[2], now), str(t[0])))
        schedule = {}
        for sid, rec, reset, old in eligible:
            candidate = max(reset, now) if old is None else max(reset, now, old)
            while True:
                occupied = [s for s in slots if candidate - 60 < s < candidate + 60]
                if len(occupied) < AUTO_RESUME_MAX_PER_MINUTE:
                    break
                candidate = max(occupied) + 60
            rec["auto_resume_fire_at"] = candidate
            slots.append(candidate)
            schedule[sid] = candidate
        return existing, schedule
    return _free_failover_rewrite(mutate)


def _free_failover_is_armed(sid):
    """Does this session have an approved-but-unfired auto-resume? Called
    from _usage_limit_scan_once so the disabled auto-send pass leaves the
    entry un-fired for the staggered slot to claim."""
    rec = (_load_free_failovers() or {}).get(_normalize_sid(sid))
    return bool(
        isinstance(rec, dict)
        and rec.get("auto_resume")
        and not rec.get("auto_resume_done")
        and rec.get("auto_resume_detected_at") == (_core._load_usage_limit_resumes().get(_normalize_sid(sid)) or {}).get("detected_at")
    )


def _free_failover_fire_auto_resume(sid, entry):
    """Fire one approved auto-resume: atomic claim first (cross-process
    safe), then a freshness re-check so we never "continue" a session that
    already moved on by itself, then the same injector a manual send uses."""
    rec = (_load_free_failovers() or {}).get(sid) or {}
    if not rec.get("auto_resume") or rec.get("auto_resume_detected_at") != entry.get("detected_at"):
        return
    if not _core._mark_usage_limit_resume_fired(sid):
        # Someone else claimed the entry (e.g. a stale pass that ran before
        # the armed-skip landed). Stop retrying.
        _free_failover_save(sid, {
            "auto_resume": False,
            "auto_resume_done": True,
            "auto_resume_error": "already_claimed",
        })
        return
    engine = (entry.get("engine") or "").lower()
    detected_at = entry.get("detected_at") or 0
    try:
        path = Path(entry["transcript_path"]) if entry.get("transcript_path") else _free_failover_session_path(engine, sid)
        newest_mtime = path.stat().st_mtime if path else 0
    except OSError:
        newest_mtime = 0
    if newest_mtime and newest_mtime > detected_at + 5:
        _free_failover_save(sid, {
            "auto_resume": False, "auto_resume_done": True,
            "auto_resume_note": "session_resumed_on_its_own",
        })
        return
    try:
        result = _core._inject_text_into_session(
            sid, "continue", mode="send", source="free-failover-reset",
        )
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    ok = bool(isinstance(result, dict) and result.get("ok"))
    _free_failover_save(sid, {
        "auto_resume": False,
        "auto_resume_done": True,
        "auto_resume_fired_at": time.time(),
        "auto_resume_error": None if ok else (result.get("error") if isinstance(result, dict) else str(result)),
    })
    if ok:
        _free_failover_marker(
            sid, "auto_resume",
            "Resumed automatically — your limit reset (you approved this).",
        )
    try:
        _core._log_activity(
            "free-failover", "AUTO_RESUME" if ok else "AUTO_RESUME_FAIL",
            f"session={sid} engine={engine} "
            f"via={(result.get('via') if isinstance(result, dict) else None)} "
            f"error={(result.get('error') if isinstance(result, dict) else result)}",
        )
    except Exception:
        pass


def _free_failover_session_path(engine, sid):
    """Transcript path for the freshness re-check — devin sessions live in
    the ACP cache dir; everything else defers to the usage-limit locator."""
    if engine == "devin":
        raw = sid[len("devincli-"):] if sid.startswith("devincli-") else sid
        return _core.COMMAND_CENTER_STATE_DIR / "acp" / "devin" / f"{raw}.jsonl"
    return _core._usage_limit_session_path(engine, sid)


# ---------------------------------------------------------------------------
# Status payload
# ---------------------------------------------------------------------------

def free_failover_status():
    """Everything the UI needs in one cheap call: free-model readiness plus
    per-session limit/failover state. No file I/O beyond the two cached
    stores and the TTL'd free-ready probe."""
    now = time.time()
    tracked = _core._load_usage_limit_resumes()
    failovers = _load_free_failovers() or {}
    sessions = {}
    for sid, entry in (tracked or {}).items():
        if not isinstance(entry, dict):
            continue
        engine = entry.get("engine") or ""
        detected_at = entry.get("detected_at") or 0
        resume_at = entry.get("resume_at")
        rec = failovers.get(sid) or {}
        state = rec.get("state")
        if entry.get("dismissed") and not state:
            continue
        fired = bool(entry.get("fired"))
        if fired and state not in ("free", "switch_back_pending"):
            continue  # consumed stop, nothing left to offer
        if not isinstance(resume_at, (int, float)) and not state:
            continue
        if resume_at and now - resume_at > 3600 and not state and not rec.get("auto_resume"):
            continue  # stale defensive cutoff, mirrors the row-side one

        item = {
            "engine": engine,
            "detected_at": detected_at or None,
            "resume_at": resume_at if isinstance(resume_at, (int, float)) else None,
            "resume_at_estimated": bool(entry.get("resume_at_estimated")),
            "display_name": rec.get("display_name") or entry.get("display_name"),
            "model": entry.get("model"),
            "offer_dismissed": bool(
                (rec.get("offer_dismissed_at") or 0) >= detected_at and detected_at
            ),
            "auto_resume_armed": bool(rec.get("auto_resume") and rec.get("auto_resume_detected_at") == detected_at),
            "limit_window": entry.get("limit_window"),
            "auto_resume_fire_at": rec.get("auto_resume_fire_at"),
            "auto_resume_done": bool(rec.get("auto_resume_done")),
            "auto_resume_error": rec.get("auto_resume_error"),
            "supports_continue_free": engine in ("claude", "devin"),
            "supports_auto_resume": engine in ("claude", "devin", "codex", "kimi"),
        }
        if engine == "devin":
            item["free_model_uid"] = (
                rec.get("free_model") if state == "free"
                else (_core._devin_free_model_candidates(
                    entry.get("model")) or [None])[0]
            )
        if state in ("free", "switch_back_pending"):
            item["state"] = state
            item["free_model"] = rec.get("free_model")
            item["free_since"] = rec.get("free_since")
            item["always"] = bool(rec.get("always"))
            item["pending_reason"] = rec.get("pending_reason")
            # Offer to switch back once the paid reset has passed and the
            # user hasn't already said "keep free" for this stop.
            reset_ref = rec.get("origin_resume_at") or resume_at
            dismissed_this_stop = (
                (rec.get("switch_back_dismissed_at") or 0)
                >= (rec.get("origin_detected_at") or 0)
            )
            item["switch_back_offered"] = bool(
                state == "free" and reset_ref and now >= reset_ref
                and not dismissed_this_stop
            )
        else:
            item["state"] = "limited" if not fired else "resolved"
            item["always"] = bool(rec.get("always"))
        sessions[sid] = item

    # Free-running sessions whose tracked entry was cleared still surface.
    for sid, rec in failovers.items():
        if not isinstance(rec, dict):
            continue
        if sid in sessions:
            continue
        if rec.get("state") not in ("free", "switch_back_pending"):
            continue
        engine, _raw = _free_failover_engine_and_raw(sid)
        reset_ref = rec.get("origin_resume_at")
        dismissed_this_stop = (
            (rec.get("switch_back_dismissed_at") or 0)
            >= (rec.get("origin_detected_at") or 0)
        )
        sessions[sid] = {
            "engine": rec.get("engine") or engine,
            "state": rec.get("state"),
            "free_model": rec.get("free_model"),
            "free_since": rec.get("free_since"),
            "always": bool(rec.get("always")),
            "pending_reason": rec.get("pending_reason"),
            "display_name": rec.get("display_name"),
            "supports_continue_free": True,
            "supports_auto_resume": False,
            "switch_back_offered": bool(
                rec.get("state") == "free" and reset_ref and now >= reset_ref
                and not dismissed_this_stop
            ),
        }

    ready = _free_ready_info()
    return {
        "ok": True,
        "now": now,
        "free_ready": bool(ready.get("ready")),
        "free_model": ready.get("model"),
        "free_via": ready.get("via"),
        "auto_resume_max_per_minute": AUTO_RESUME_MAX_PER_MINUTE,
        "sessions": sessions,
    }
