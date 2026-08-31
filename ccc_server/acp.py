"""Extracted from server.py (originally lines 40161-42525).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import collections
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid

from ccc_server import core as _core

# ===========================================================================
# Generic ACP (Agent Client Protocol) engine bridge
#
# Drives ACP-speaking agent CLIs (``kimi acp`` first; new harnesses join by
# adding one ``_ACP_HARNESSES`` entry) over newline-delimited JSON-RPC 2.0 on
# stdio. One lazily-started subprocess per harness, multiplexed by sessionId.
# Design mirrors the Codex app-server layer with two latency-driven
# deviations:
#   - prompt RPCs are async (per-id pending Events) and never serialize the
#     connection: ACP's ``session/prompt`` response only lands at turn end.
#   - SSE feeds are woken directly by notification folding (Condition), not
#     by transcript mtime polling, so chunks reach the browser in ~ms.
# CCC-owned persisted artifacts (harness-agnostic):
#   - transcripts: COMMAND_CENTER_STATE_DIR/acp/<harness>/<sid>.jsonl — one
#     finalized conversation event per line; CCC's replay source, NOT the
#     harness's own session store.
#   - state snapshot: acp-<harness>-state.json — last-known registry,
#     ``"authoritative": False`` (same posture as the codex state file).
# ===========================================================================

_ACP_PROTOCOL_VERSION = 1
_ACP_TRANSCRIPT_DIR = _core.COMMAND_CENTER_STATE_DIR / "acp"
_ACP_EVENT_MAX = 500          # finalized conv events kept in memory per session
_ACP_DELTA_MAX = 400          # in-flight bubble deltas kept for late SSE attach

_ACP_HARNESSES = {
    "kimi": {
        "label": "Kimi",
        "bin_env": "CCC_KIMI_BIN",
        "bin_names": ("kimi",),
        "acp_args": ("acp",),
        "kill_env": "CCC_KIMI_ACP",
        "home_env": "KIMI_CODE_HOME",
        "home_default": "~/.kimi-code",
    },
    # ACP harness #2 (KIMI-FIXES-7): validates the generic layer against a
    # second ACP-speaking agent. GLM (Z.AI/Zhipu) via the ACP-registry
    # glm-acp-agent (npm i -g glm-acp-agent). Handshake works unauthenticated;
    # turns need ZAI_API_KEY (or `glm-acp-agent --setup`). No discovery yet —
    # sessions exist only when created through the generic _acp_* API.
    "glm": {
        "label": "GLM",
        "bin_env": "CCC_GLM_BIN",
        "bin_names": ("glm-acp-agent",),
        "acp_args": (),
        "kill_env": "CCC_GLM_ACP",
    },
    # xAI Grok Build. Official long-lived path is `grok agent stdio` (ACP).
    # --no-leader keeps CCC off the user's TUI leader socket. Always-approve
    # is per-session via session/new `_meta.yoloMode`, not a process flag.
    "grok": {
        "label": "Grok",
        "bin_env": "CCC_GROK_BIN",
        "bin_names": ("grok",),
        "acp_args": ("agent", "--no-leader", "stdio"),
        "kill_env": "CCC_GROK_ACP",
        "home_env": "GROK_HOME",
        "home_default": "~/.grok",
    },
}

# Harnesses whose ACP subprocess lives in the persistent worker (same
# control-plane hop as Kimi). Dashboard HTTP handlers must route through
# _control_plane_engine_call so they do not start a second grok/kimi agent.
_ACP_WORKER_HARNESSES = frozenset({"kimi", "grok"})

_ACP_LOCK = threading.Condition()
_ACP_CONNS = {}          # harness -> {"proc","reader","initialized","initializing","next_id","caps","send_lock"}
_ACP_PENDING = {}        # harness -> {req_id: {"event","response","method","sid","is_active"}}
_ACP_STATE_LOADED = set()
_ACP_ENSURE_ERROR = {}   # harness -> last ensure failure reason (diagnostics)
_ACP_RECOVERY_LOCKS = {}
# A recovered Kimi session can end with a durable ``step.begin`` but no
# matching boundary when its old ACP process dies.  Briefly suppress that
# stale wire-only busy signal after an operator-approved bridge restart; the
# fresh ACP state becomes authoritative as soon as the retried prompt starts.
_KIMI_WIRE_BUSY_SUPPRESS_UNTIL = {}

# Agent-side shell tools (kimi's Bash/Glob/Grep) run through the ACP terminal
# capability: the agent sends terminal/* requests and WE execute the command
# as a local subprocess. One registry entry per live terminal, keyed by the
# terminalId we hand back from terminal/create.
_ACP_TERMINALS = {}        # terminalId -> {"proc","buf","limit","truncated","exit","signal","exited","harness","sid","exit_event"}
_ACP_TERMINALS_LOCK = threading.Lock()
_ACP_TERMINAL_DEFAULT_LIMIT = 1024 * 1024  # retained-output cap when the agent passes no outputByteLimit
# Output of RELEASED terminals, kept briefly. Kimi's terminal-backed tool
# calls (Bash) report completion as a `tool_call_update` whose content is only
# `{type:'terminal', terminalId}` — never the text (kimi assumes the client
# already rendered the bytes in a terminal pane) — and that update lands
# AFTER the agent has already `terminal/release`d the terminal. Snapshotting
# the buffer at release is what lets the finalized tool row carry the
# command's output at all.
_ACP_TERMINAL_OUTPUT_CACHE = collections.OrderedDict()  # terminalId -> output snapshot dict
_ACP_TERMINAL_OUTPUT_CACHE_MAX = 256
# Per-tool output kept on the persisted row (and the live tool_result delta).
_ACP_TOOL_OUTPUT_PREVIEW_MAX = 1200


def _acp_harness_enabled(harness):
    cfg = _core._ACP_HARNESSES.get(harness) or {}
    kill = cfg.get("kill_env")
    if kill and os.environ.get(kill, "1").lower() in ("0", "false", "no"):
        return False
    return True


def _acp_conn_error(harness):
    """Connection-unavailable message with the last ensure failure attached
    (bin missing / launch failed / handshake timeout) when known."""
    detail = _core._ACP_ENSURE_ERROR.get(harness)
    base = f"ACP {harness} connection unavailable"
    return f"{base}: {detail}" if detail else base


def _acp_resolve_bin(harness):
    """Standard resolver shape used by every engine's availability endpoint."""
    cfg = _core._ACP_HARNESSES.get(harness)
    label = (cfg or {}).get("label") or harness
    if not cfg or not _core._acp_harness_enabled(harness):
        return {"available": False, "bin": None, "reason": "disabled", "code": "disabled"}
    override = os.environ.get(cfg.get("bin_env") or "", "").strip()
    if override:
        candidate = Path(os.path.expanduser(override))
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return {"available": True, "bin": str(candidate), "source": "env"}
        return {
            "available": False, "bin": None,
            "reason": f"{cfg['bin_env']} set but not executable: {override}",
            "code": "env_invalid",
        }
    for name in cfg.get("bin_names") or ():
        found = shutil.which(name)
        if found:
            return {"available": True, "bin": found, "source": "path"}
    # Server processes launched from a Finder app or a bare shell often have
    # a PATH that misses user-install dirs (launchd) and the harness's own
    # managed bin/ (e.g. ~/.kimi-code/bin/kimi). Probe both explicitly.
    candidates = []
    for name in cfg.get("bin_names") or ():
        candidates.extend(_core._iter_common_cli_candidates(name))
        home = os.environ.get(cfg.get("home_env") or "", "").strip()
        roots = []
        if home:
            roots.append(Path(os.path.expanduser(home)))
        if cfg.get("home_default"):
            roots.append(Path(os.path.expanduser(cfg["home_default"])))
        for root in roots:
            candidates.append(root / "bin" / name)
    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return {"available": True, "bin": str(candidate), "source": "managed"}
        except OSError:
            continue
    return {
        "available": False, "bin": None,
        "reason": f"{label} CLI not found on PATH",
        "code": "not_installed",
    }


def _acp_state_file(harness):
    return _core.COMMAND_CENTER_STATE_DIR / f"acp-{harness}-state.json"


def _acp_transcript_path(harness, sid):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sid or ""))
    return _core._ACP_TRANSCRIPT_DIR / harness / f"{safe}.jsonl"


def _acp_transcript_first_prompt(harness, sid):
    """Return the first user_text event from an ACP transcript, or None."""
    try:
        with _core._acp_transcript_path(harness, sid).open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "user_text":
                    text = str(ev.get("text") or "").strip()
                    if text:
                        return text
    except OSError:
        pass
    return None


def _kimi_wire_prompt_usages(wire_path):
    """Ordered per-user-prompt usage totals from one Kimi wire log.

    Durable ``usage.record`` rows and ``step.end.usage`` describe the same
    model calls. Prefer durable records for a prompt and use step-end values
    only when no durable record exists, so replay enrichment never doubles a
    model step. ``None`` preserves alignment for prompts with no usage.
    """
    if not wire_path:
        return []
    try:
        with Path(wire_path).open("rb") as handle:
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError, ValueError):
        return []

    def empty_usage():
        return {
            "input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "output_tokens": 0,
        }

    def add_usage(total, raw):
        if not isinstance(raw, dict):
            return False
        usage_keys = (
            "inputOther", "inputCacheRead", "inputCacheCreation", "output",
        )
        if not any(key in raw for key in usage_keys):
            return False
        total["input_tokens"] += _core._codex_int(raw.get("inputOther"))
        total["cache_read_input_tokens"] += _core._codex_int(raw.get("inputCacheRead"))
        total["cache_creation_input_tokens"] += _core._codex_int(raw.get("inputCacheCreation"))
        total["output_tokens"] += _core._codex_int(raw.get("output"))
        return True

    prompt_usages = []
    current = None
    awaiting_user_message = False

    def new_prompt():
        return {
            "durable": empty_usage(),
            "fallback": empty_usage(),
            "saw_durable": False,
        }

    def finish_prompt():
        nonlocal current, awaiting_user_message
        if current is None:
            return
        chosen = current["durable"] if current["saw_durable"] else current["fallback"]
        prompt_usages.append(chosen if any(chosen.values()) else None)
        current = None
        awaiting_user_message = False

    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        record_type = record.get("type")
        if record_type == "turn.prompt":
            finish_prompt()
            current = new_prompt()
            awaiting_user_message = True
            continue
        if record_type == "context.append_message":
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            origin = message.get("origin") or {}
            if not isinstance(origin, dict):
                continue
            if message.get("role") == "user" and origin.get("kind") in (None, "user"):
                if current is None:
                    current = new_prompt()
                elif awaiting_user_message:
                    awaiting_user_message = False
                else:
                    finish_prompt()
                    current = new_prompt()
                continue
        if current is None:
            continue
        if record_type == "usage.record" and record.get("usageScope") in (None, "turn"):
            usage = record.get("usage")
            if add_usage(current["durable"], usage):
                current["saw_durable"] = True
            continue
        if record_type == "context.append_loop_event":
            loop = record.get("event")
            if not isinstance(loop, dict):
                continue
            if loop.get("type") == "step.end":
                add_usage(current["fallback"], loop.get("usage"))

    finish_prompt()
    return prompt_usages


def _kimi_event_has_token_usage(event):
    return any(
        key in event
        for key in ("tokens_in", "tokens_cached", "tokens_out", "token_usage")
    )


def _kimi_assistant_has_visible_reply(event):
    for block in event.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if block.get("kind") in ("text", "thinking") and str(block.get("text") or "").strip():
            return True
    return False


def _enrich_kimi_transcript_token_usage(sid, events):
    """Attach one wire-derived aggregate to each prompt's final reply."""
    try:
        wire_path = _core._acp_wire_path("kimi", sid)
    except Exception:
        return events
    prompt_usages = _core._kimi_wire_prompt_usages(wire_path)
    if not prompt_usages:
        return events

    prompt_index = -1
    assistants = []

    def finish_prompt():
        if not assistants or not (0 <= prompt_index < len(prompt_usages)):
            return
        usage = prompt_usages[prompt_index]
        if not usage or any(_kimi_event_has_token_usage(event) for event in assistants):
            return
        target = next(
            (event for event in reversed(assistants) if _kimi_assistant_has_visible_reply(event)),
            assistants[-1],
        )
        _apply_kimi_turn_usage(target, usage)

    for event in events:
        event_type = event.get("type")
        if event_type == "user_text":
            finish_prompt()
            prompt_index += 1
            assistants = []
        elif event_type == "assistant" and prompt_index >= 0:
            assistants.append(event)
    finish_prompt()
    return events


def _acp_transcript_events_after(harness, sid, after_line):
    """Finalized conv events newer than `after_line`, read from the CCC-owned
    transcript (the replay source for SSE reconnects and CCC restarts)."""
    all_events = []
    try:
        with _core._acp_transcript_path(harness, sid).open() as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                all_events.append(event)
    except OSError:
        return []

    if harness == "kimi" and all_events:
        try:
            _enrich_kimi_transcript_token_usage(sid, all_events)
        except Exception:
            # Usage enrichment is optional metadata; transcript rendering is
            # still authoritative when a wire log is missing or malformed.
            pass

    events = []
    for event in all_events:
        try:
            line = int(event.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        if line > after_line:
            events.append(event)
    return events


def _acp_ts():
    return datetime.fromtimestamp(time.time(), timezone.utc).isoformat().replace("+00:00", "Z")


def _acp_new_session_state(harness, sid, cwd=""):
    return {
        "sid": sid,
        "harness": harness,
        "cwd": cwd or "",
        "status": "idle",            # idle | active | closed
        "created_at": time.time(),
        "updated_at": time.time(),
        "turn_seq": 0,
        "next_line": 1,
        "active_turn": None,         # {"req_id","msg_id","text","thought","tools","started_at"}
        "replay": None,              # {"kind","text"} while session/load replays history
        "events": collections.deque(maxlen=_ACP_EVENT_MAX),
        "deltas": collections.deque(maxlen=_ACP_DELTA_MAX),
        "delta_seq": 0,
        "pending_permissions": {},   # req_id -> {sessionId, toolCall, options, requested_at}
        "config_options": [],
        "available_commands": [],
        "model": None,
        "stop_reason": None,
        "attached": True,
    }


def _acp_session(harness, sid, create=False, cwd=""):
    sessions = _core._ACP_SESSION_STATE.setdefault(harness, {})
    state = sessions.get(sid)
    if state is None and create:
        state = _acp_new_session_state(harness, sid, cwd=cwd)
        sessions[sid] = state
    return state


def _acp_transcript_last_line(harness, sid):
    """Return the highest `line` value in the ACP transcript, or 0.

    Prefer the in-memory session's `next_line - 1` when available; otherwise
    scan the transcript file."""
    state = _core._acp_session(harness, sid)
    if state is not None:
        return max(0, state.get("next_line", 1) - 1)
    path = _core._acp_transcript_path(harness, sid)
    last_line = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                try:
                    line = int(ev.get("line") or 0)
                except (TypeError, ValueError):
                    line = 0
                if line > last_line:
                    last_line = line
    except OSError:
        pass
    return last_line


def _grok_acp_session_loaded(sid):
    """True when the grok ACP connection currently has `sid` loaded and alive."""
    with _core._ACP_LOCK:
        conn = _core._ACP_CONNS.get("grok")
        if conn is None:
            return False
        transport = conn.get("transport")
        if transport is None or not transport.alive():
            return False
        state = _core._acp_session("grok", sid)
        return state is not None and state.get("loaded_conn") == id(conn)


_grok_external_writer_cache = {}
_grok_external_writer_lock = threading.Lock()


def _grok_external_writer_active(sid):
    """True when a `grok --resume <sid>` TUI process is running.

    Cached per sid for 4 seconds — ps scans are expensive and a TUI stays
    open across multiple CCC operations."""
    now = time.time()
    with _grok_external_writer_lock:
        entry = _core._grok_external_writer_cache.get(sid)
        if entry and now - entry["ts"] < 4.0:
            return entry["active"]
    active = False
    try:
        for _pid_s, cmd in _core._raw_engine_process_commands("grok"):
            for tok in cmd.split():
                if len(tok) >= 16 and tok == sid and _core._command_targets_engine_session(cmd, tok, "grok"):
                    active = True
                    break
            if active:
                break
    except Exception:
        pass
    with _grok_external_writer_lock:
        _core._grok_external_writer_cache[sid] = {"ts": time.time(), "active": active}
    return active


def _grok_conversation_source(sid):
    """Return the current ground-truth transcript Path for a Grok session.

    Prefers the CCC ACP transcript when this process has the session loaded;
    otherwise uses the on-disk Grok store so a terminal TUI's writes are
    reflected. Falls back to the ACP transcript path when neither has data."""
    if _grok_acp_session_loaded(sid):
        return _core._acp_transcript_path("grok", sid)
    session_dir = _core._grok_session_dir(sid)
    if session_dir is not None:
        for name in ("updates.jsonl", "chat_history.jsonl"):
            p = session_dir / name
            try:
                if p.is_file() and p.stat().st_size > 0:
                    return p
            except OSError:
                continue
    return _core._acp_transcript_path("grok", sid)


def _acp_save_state_unlocked(harness):
    """Persist the last-known session registry; volatile fields excluded."""
    try:
        sessions = _core._ACP_SESSION_STATE.get(harness) or {}
        payload = {
            "schema": 1,
            "authoritative": False,
            "saved_at": _acp_ts(),
            "sessions": {
                sid: {
                    "cwd": st.get("cwd") or "",
                    "status": st.get("status") or "idle",
                    "updated_at": st.get("updated_at") or 0,
                    "turn_seq": st.get("turn_seq") or 0,
                    "next_line": st.get("next_line") or 1,
                    "model": st.get("model"),
                }
                for sid, st in sessions.items()
                if st.get("attached")
            },
        }
        path = _core._acp_state_file(harness)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(payload, f, indent=1, sort_keys=True)
        tmp.replace(path)
    except OSError:
        pass


def _acp_load_state(harness):
    """Rehydrate the session registry after a CCC restart (volatile fields
    deliberately not restored — same phantom-turn guard as codex)."""
    if harness in _core._ACP_STATE_LOADED:
        return
    _core._ACP_STATE_LOADED.add(harness)
    try:
        with _core._acp_state_file(harness).open() as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        payload = None
    sessions = (payload or {}).get("sessions") or {}
    for sid, meta in sessions.items():
        if not isinstance(meta, dict):
            continue
        state = _core._acp_session(harness, sid, create=True, cwd=meta.get("cwd") or "")
        state["status"] = "idle"
        state["turn_seq"] = int(meta.get("turn_seq") or 0)
        state["model"] = meta.get("model")
        state["updated_at"] = float(meta.get("updated_at") or 0)
        next_line = int(meta.get("next_line") or 1)
        if next_line <= 1:
            try:
                with _core._acp_transcript_path(harness, sid).open() as f:
                    next_line = sum(1 for _ in f) + 1
            except OSError:
                next_line = 1
        state["next_line"] = max(1, next_line)


def _acp_append_transcript_unlocked(harness, sid, event):
    try:
        path = _core._acp_transcript_path(harness, sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass


def _acp_emit_event_unlocked(harness, sid, event, save=False):
    """Finalize one conversation event: in-memory deque + transcript file."""
    state = _core._acp_session(harness, sid, create=True)
    event = dict(event)
    event.setdefault("line", state["next_line"])
    event.setdefault("ts", _acp_ts())
    state["next_line"] = int(event["line"]) + 1
    state["events"].append(event)
    state["updated_at"] = time.time()
    _acp_append_transcript_unlocked(harness, sid, event)
    if save:
        _acp_save_state_unlocked(harness)
    _core._ACP_LOCK.notify_all()
    return event


def _acp_emit_delta_unlocked(harness, sid, delta):
    """In-flight bubble preview (never persisted; replayed for late attach)."""
    state = _core._acp_session(harness, sid, create=True)
    state["delta_seq"] += 1
    state["deltas"].append({"seq": state["delta_seq"], "event": delta})
    state["updated_at"] = time.time()
    _core._ACP_LOCK.notify_all()


class _AcpTransport:
    """Newline-delimited JSON-RPC over a subprocess's stdio pipes."""

    def __init__(self, proc):
        self.proc = proc
        self.send_lock = threading.Lock()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def send_json(self, payload):
        data = json.dumps(payload)
        with self.send_lock:
            self.proc.stdin.write(data + "\n")
            self.proc.stdin.flush()

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass


def _acp_conn(harness):
    return _core._ACP_CONNS.get(harness)


def _acp_send(harness, payload):
    conn = _core._ACP_CONNS.get(harness)
    transport = (conn or {}).get("transport")
    if transport is None or not transport.alive():
        return False
    try:
        transport.send_json(payload)
        return True
    except (BrokenPipeError, OSError, ValueError):
        return False


def _acp_request_async(harness, method, params, sid=None, on_registered=None,
                       on_send_failed=None):
    """Register a pending id and send without waiting. Returns req_id|None."""
    conn = _acp_conn(harness)
    transport = (conn or {}).get("transport")
    if transport is None or not transport.alive():
        return None
    with _core._ACP_LOCK:
        req_id = conn["next_id"]
        conn["next_id"] += 1
        entry = {
            "event": threading.Event(),
            "response": None,
            "method": method,
            "sid": sid,
            "is_active": False,
        }
        _core._ACP_PENDING.setdefault(harness, {})[req_id] = entry
        if on_registered is not None:
            on_registered(req_id, entry)
    if not _core._acp_send(harness, {
        "jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {},
    }):
        with _core._ACP_LOCK:
            _core._ACP_PENDING.get(harness, {}).pop(req_id, None)
            if on_send_failed is not None:
                on_send_failed(req_id, entry)
        return None
    return req_id


def _acp_wait_response(harness, req_id, timeout=20):
    """Wait for a registered pending id; returns the response payload."""
    with _core._ACP_LOCK:
        entry = (_core._ACP_PENDING.get(harness) or {}).get(req_id)
    if entry is None:
        return None
    entry["event"].wait(timeout)
    with _core._ACP_LOCK:
        entry = (_core._ACP_PENDING.get(harness) or {}).pop(req_id, None)
    if entry is None:
        return None
    return entry.get("response")


def _acp_request(harness, method, params=None, timeout=20, sid=None):
    """Synchronous RPC for control methods (short timeouts only — never
    use for session/prompt, whose response lands at turn end)."""
    req_id = _core._acp_request_async(harness, method, params or {}, sid=sid)
    if req_id is None:
        return {"ok": False, "error": _core._acp_conn_error(harness)}
    response = _acp_wait_response(harness, req_id, timeout=timeout)
    if response is None:
        return {"ok": False, "error": f"ACP {harness} request timed out: {method}"}
    error = response.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        out = {"ok": False, "error": error.get("message") or f"ACP error {code}", "code": code}
        if code == -32000:
            out["auth_required"] = True
            out["error"] = f"{_core._ACP_HARNESSES[harness]['label']} login required — run `{_A_core.CP_HARNESSES[harness]['bin_names'][0]} login`"
        return out
    return {"ok": True, "result": response.get("result")}


def _acp_respond(harness, req_id, result=None, error=None):
    """Answer an agent→client request (permission prompts, fs/*, etc.)."""
    payload = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result if result is not None else {}
    return _core._acp_send(harness, payload)


def _acp_handle_message(harness, payload):
    if not isinstance(payload, dict):
        return
    method = payload.get("method")
    if "id" in payload and method:
        _acp_handle_agent_request(harness, payload.get("id"), str(method), payload.get("params") or {})
        return
    if "id" in payload:
        req_id = payload.get("id")
        with _core._ACP_LOCK:
            entry = (_core._ACP_PENDING.get(harness) or {}).get(req_id)
            if entry is not None:
                entry["response"] = payload
        # Fold prompt turns BEFORE releasing waiters: a synchronous ask
        # (_acp_ask_and_wait) reads final_text off the entry, which only
        # exists once _acp_finalize_turn ran.
        if entry is not None and entry.get("method") == "session/prompt" and entry.get("sid"):
            _core._acp_finalize_turn(harness, entry["sid"], payload, entry)
        if entry is not None:
            entry["event"].set()
        return
    if method == "session/update":
        params = payload.get("params") or {}
        sid = params.get("sessionId")
        update = params.get("update") or {}
        if sid and isinstance(update, dict):
            _core._acp_handle_session_update(harness, str(sid), update)


def _acp_handle_agent_request(harness, req_id, method, params):
    """Agent→client requests. Permission prompts and terminal/* are serviced;
    anything else gets methodNotFound so the agent never hangs waiting on us."""
    if method == "session/request_permission":
        sid = str(params.get("sessionId") or "")
        with _core._ACP_LOCK:
            state = _core._acp_session(harness, sid, create=True)
            # Keyed by str(req_id) — UI round-trips ids as strings, while the
            # original (int|str) id is preserved for the JSON-RPC response.
            state["pending_permissions"][str(req_id)] = {
                "req_id": req_id,
                "session_id": sid,
                "tool_call": params.get("toolCall") or {},
                "options": params.get("options") or [],
                "requested_at": time.time(),
            }
            tool = params.get("toolCall") or {}
            _core._acp_emit_event_unlocked(harness, sid, {
                "type": "assistant",
                "message_id": f"acp-perm-{req_id}",
                "blocks": [{
                    "kind": "tool_use",
                    "name": tool.get("title") or tool.get("kind") or "tool",
                    "detail": _acp_permission_tool_detail(tool) or tool.get("kind") or "",
                    "id": tool.get("toolCallId") or "",
                    "approval_required": True,
                    "approval_message": f"{_core._ACP_HARNESSES[harness]['label']} requests approval",
                    "acp_harness": harness,
                    "acp_request_id": req_id,
                    "acp_options": params.get("options") or [],
                }],
            }, save=True)
        return
    if method.startswith("terminal/"):
        _core._acp_handle_terminal_request(harness, req_id, method, params)
        return
    _core._acp_respond(harness, req_id, error={"code": -32601, "message": f"method not found: {method}"})


def _acp_terminal_pump(tid):
    """Drain a terminal subprocess's combined stdout/stderr into its bounded
    buffer, then record the exit status. One pump thread per terminal."""
    with _core._ACP_TERMINALS_LOCK:
        entry = _core._ACP_TERMINALS.get(tid)
    if entry is None:
        return
    proc = entry["proc"]
    try:
        while True:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
            if not chunk:
                break
            with _core._ACP_TERMINALS_LOCK:
                buf = entry["buf"]
                buf += chunk
                limit = entry["limit"]
                if len(buf) > limit:
                    # Truncate from the front at a UTF-8 character boundary
                    # (skip continuation bytes) as the spec requires.
                    start = len(buf) - limit
                    while start < len(buf) and (buf[start] & 0xC0) == 0x80:
                        start += 1
                    entry["buf"] = bytearray(buf[start:])
                    entry["truncated"] = True
    except (OSError, ValueError):
        pass
    finally:
        rc = proc.wait()
        sig = None
        code = rc
        if rc is not None and rc < 0:
            signum = -rc
            try:
                sig = signal.Signals(signum).name
            except ValueError:
                sig = str(signum)
            code = None
        with _core._ACP_TERMINALS_LOCK:
            entry["exit"] = code
            entry["signal"] = sig
            entry["exited"] = True
            entry["exit_event"].set()


def _acp_terminal_output_result(entry):
    with _core._ACP_TERMINALS_LOCK:
        result = {
            "output": bytes(entry["buf"]).decode("utf-8", errors="replace"),
            "truncated": entry["truncated"],
        }
        if entry["exited"]:
            result["exitStatus"] = {"exitCode": entry["exit"], "signal": entry["signal"]}
    return result


def _acp_terminal_wait_and_respond(harness, req_id, tid):
    """terminal/wait_for_exit runs off the ACP reader thread: blocking it would
    stall every session on the harness connection for the command's lifetime."""
    with _core._ACP_TERMINALS_LOCK:
        entry = _core._ACP_TERMINALS.get(tid)
    if entry is None:
        _core._acp_respond(harness, req_id, error={"code": -32602, "message": f"unknown terminalId: {tid}"})
        return
    entry["exit_event"].wait()
    with _core._ACP_TERMINALS_LOCK:
        result = {"exitCode": entry["exit"], "signal": entry["signal"]}
    _core._acp_respond(harness, req_id, result)


def _acp_terminal_argv(command, args=None):
    """Build Popen argv for ACP terminal/create.

    Spec-shaped agents (Kimi) send `command` as the executable and `args` as
    a list. Grok sends the whole `/bin/bash -lc '…'` line in `command` with
    no args; passing that as a single argv entry makes Popen look for a file
    named `/bin/bash -lc 'echo ok'` (ENOENT, or ENAMETOOLONG on long scripts).
    """
    if isinstance(args, list) and args:
        return [command] + [str(a) for a in args]
    try:
        if os.path.isfile(command) and os.access(command, os.X_OK):
            return [command]
    except OSError:
        pass
    if not any(c.isspace() for c in command):
        return [command]
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        argv = []
    if len(argv) >= 2:
        return argv
    return ["/bin/bash", "-lc", command]


def _acp_handle_terminal_request(harness, req_id, method, params):
    """Service ACP terminal/* requests by running the agent's shell commands
    as local subprocesses. This is what powers kimi's Bash/Glob/Grep tools."""
    sid = str(params.get("sessionId") or "")
    if method == "terminal/create":
        command = str(params.get("command") or "")
        if not command:
            _core._acp_respond(harness, req_id, error={"code": -32602, "message": "terminal/create: command is required"})
            return
        argv = _core._acp_terminal_argv(command, params.get("args"))
        cwd = params.get("cwd") or None
        if not cwd and sid:
            state = _core._acp_session(harness, sid)
            cwd = (state or {}).get("cwd") or None
        env = os.environ.copy()
        extra = params.get("env")
        if isinstance(extra, dict):
            env.update({str(k): str(v) for k, v in extra.items()})
        elif isinstance(extra, list):
            for item in extra:
                if isinstance(item, dict) and item.get("name"):
                    env[str(item["name"])] = str(item.get("value") or "")
        try:
            limit = int(params.get("outputByteLimit") or 0) or _ACP_TERMINAL_DEFAULT_LIMIT
        except (TypeError, ValueError):
            limit = _ACP_TERMINAL_DEFAULT_LIMIT
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        except (FileNotFoundError, OSError) as exc:
            _core._acp_respond(harness, req_id, error={"code": -32602, "message": f"terminal/create failed: {exc}"})
            return
        tid = str(uuid.uuid4())
        with _core._ACP_TERMINALS_LOCK:
            _core._ACP_TERMINALS[tid] = {
                "proc": proc,
                "buf": bytearray(),
                "limit": max(limit, 4096),
                "truncated": False,
                "exit": None,
                "signal": None,
                "exited": False,
                "harness": harness,
                "sid": sid,
                "exit_event": threading.Event(),
            }
        threading.Thread(
            target=_acp_terminal_pump, args=(tid,),
            daemon=True, name=f"acp-term-{tid[:8]}",
        ).start()
        _core._acp_respond(harness, req_id, {"terminalId": tid})
        return
    tid = str(params.get("terminalId") or "")
    with _core._ACP_TERMINALS_LOCK:
        entry = _core._ACP_TERMINALS.get(tid)
    if entry is None:
        _core._acp_respond(harness, req_id, error={"code": -32602, "message": f"unknown terminalId: {tid}"})
        return
    if method == "terminal/output":
        _core._acp_respond(harness, req_id, _acp_terminal_output_result(entry))
        return
    if method == "terminal/wait_for_exit":
        threading.Thread(
            target=_acp_terminal_wait_and_respond, args=(harness, req_id, tid),
            daemon=True, name=f"acp-term-wait-{tid[:8]}",
        ).start()
        return
    if method in ("terminal/kill", "terminal/release"):
        proc = entry["proc"]
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if method == "terminal/release":
            snapshot = _acp_terminal_output_result(entry)
            with _core._ACP_TERMINALS_LOCK:
                _core._acp_terminal_cache_output_unlocked(tid, snapshot)
                _core._ACP_TERMINALS.pop(tid, None)
        _core._acp_respond(harness, req_id, {})
        return
    _core._acp_respond(harness, req_id, error={"code": -32601, "message": f"method not found: {method}"})


def _acp_append_turn_text(turn, field, chunk):
    # Finalized ACP events are the replay source for a conversation. Trimming
    # the accumulated stream here silently removed the opening of long replies.
    turn[field] = (turn.get(field) or "") + chunk
    if field == "text":
        # `text` is flushed to its own row before every tool call (stream
        # order); the whole-turn concatenation stays available for the
        # synchronous-ask answer (see _acp_finalize_turn).
        turn["text_all"] = (turn.get("text_all") or "") + chunk


def _acp_flush_turn_text_unlocked(harness, sid, turn, usage=None):
    """Persist the turn's accumulated thought/text as ONE assistant row and
    reset the accumulators.

    Called before a tool row can be emitted and at turn end, so the finalized
    transcript keeps the model's real order — think → say → run → think → …
    — instead of every tool row first and one mashed-together text block at
    the end (which the client then rendered as "26 tool calls" over a wall of
    sentences with no spaces between them)."""
    blocks = []
    if turn.get("thought"):
        blocks.append({"kind": "thinking", "text": turn["thought"]})
    if turn.get("text"):
        blocks.append({"kind": "text", "text": turn["text"]})
    turn["thought"] = ""
    turn["text"] = ""
    if not blocks:
        return None
    event = {"type": "assistant", "message_id": turn["msg_id"], "blocks": blocks}
    if usage:
        _apply_kimi_turn_usage(event, usage)
    return _core._acp_emit_event_unlocked(harness, sid, event)


def _acp_replay_flush_unlocked(harness, sid, state, replay):
    """Flush the session/load replay's pending text bucket (and any tool rows
    still waiting for a terminal status) in arrival order."""
    if replay.get("kind") and replay.get("text"):
        ev = _core._acp_message_event(state, replay["kind"], replay["text"])
        if ev is not None:
            _core._acp_emit_event_unlocked(harness, sid, ev)
    replay["text"] = ""
    replay["kind"] = None
    for tid, tool in list((replay.get("tools") or {}).items()):
        if not tool.get("emitted"):
            tool["emitted"] = True
            _core._acp_emit_event_unlocked(harness, sid, _acp_tool_event(
                {"msg_id": f"acp-replay-{sid}-{state['next_line']}"}, tid, tool))
    replay["tools"] = {}


def _acp_handle_session_update(harness, sid, update):
    kind = update.get("sessionUpdate")
    content = update.get("content") or {}
    chunk = content.get("text") if isinstance(content, dict) else None
    if isinstance(chunk, str):
        chunk = _core._strip_lone_surrogates(chunk)
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid, create=True)
        # CCC-941: only content-bearing updates count as "activity" — bumping
        # this on every kind (incl. available_commands_update/
        # config_option_update, which the harness can resend on reconnect
        # with no real turn happening) made old idle sessions show "1h ago".
        if kind not in ("available_commands_update", "config_option_update"):
            state["updated_at"] = time.time()

        # session/load history replay: accumulate per speaker/kind and finalize
        # on switches; the load response flushes the tail (see _acp_load).
        # CCC-932: agent_thought_chunk used to be dropped during replay
        # (unhandled here, then swallowed by the "no active_turn" branch
        # below) — a resumed Kimi session showed only text/tool_use, no
        # thinking. Track it as its own "thought" bucket so it flushes into
        # its own thinking-kind block instead of merging into surrounding text.
        replay = state.get("replay")
        if replay is not None and kind in ("user_message_chunk", "agent_message_chunk", "agent_thought_chunk"):
            speaker = "user" if kind == "user_message_chunk" else (
                "thought" if kind == "agent_thought_chunk" else "assistant")
            if replay.get("kind") and replay["kind"] != speaker and replay.get("text"):
                ev = _core._acp_message_event(state, replay["kind"], replay["text"])
                if ev is not None:
                    _core._acp_emit_event_unlocked(harness, sid, ev)
                replay["text"] = ""
            replay["kind"] = speaker
            replay["text"] = (replay.get("text") or "") + (chunk or "")
            return

        if kind in ("agent_message_chunk", "agent_thought_chunk") and chunk:
            turn = state.get("active_turn")
            if turn is None:
                # Unsolicited chunk (e.g. mid-replay thought) — ignore.
                return
            field = "text" if kind == "agent_message_chunk" else "thought"
            _core._acp_append_turn_text(turn, field, chunk)
            block_type = "text" if kind == "agent_message_chunk" else "thinking"
            _acp_emit_delta_unlocked(harness, sid, {
                "type": "assistant_block",
                "message_id": turn["msg_id"],
                "blocks": [{"type": block_type, "text": chunk}],
            })
            return

        if kind == "user_message_chunk":
            # Live user echo is skipped (we emit user_text at prompt time).
            return

        if kind == "tool_call":
            tool_id = str(update.get("toolCallId") or "")
            title = update.get("title") or update.get("kind") or "tool"
            # rawInput arrives on the CREATE for non-streamed calls (never on
            # the lazy create — docs/kimi-code-reference.md §vocabulary), so
            # seed detail/input here too, not only on tool_call_update.
            raw_input = update.get("rawInput")
            detail = update.get("kind") or ""
            if isinstance(raw_input, dict) and raw_input:
                detail = _core._tool_use_detail(title, raw_input, max_len=160) or detail
            diff = _acp_tool_content_diff(update)
            entry = {
                "title": title, "status": update.get("status") or "running",
                "detail": detail, "emitted": False,
                "acp_kind": update.get("kind") or "",
            }
            if isinstance(raw_input, dict) and raw_input:
                entry["input"] = _core._tool_input_payload(raw_input)
            if diff:
                entry["diff"] = diff
            if replay is not None:
                # session/load history: the text bucket flushes so the row
                # lands where the tool ran; the row itself waits for its
                # terminal status (same as a live turn) or the load's flush.
                if replay.get("text"):
                    ev = _core._acp_message_event(state, replay["kind"], replay["text"])
                    if ev is not None:
                        _core._acp_emit_event_unlocked(harness, sid, ev)
                    replay["text"] = ""
                    replay["kind"] = None
                replay.setdefault("tools", {})[tool_id] = entry
                return
            turn = state.get("active_turn")
            if turn is not None:
                # Text/thought streamed BEFORE this call belongs above its
                # row — persist it now so the transcript keeps stream order.
                _acp_flush_turn_text_unlocked(harness, sid, turn)
                turn.setdefault("tools", {})[tool_id] = entry
            # Live bubble shows the call immediately; the finalized conv row
            # is deferred until rawInput arrives (rich detail) — see update.
            _acp_emit_delta_unlocked(harness, sid, {
                "type": "assistant_block",
                "message_id": (turn or {}).get("msg_id") or f"acp-{harness}-tool",
                "blocks": [{
                    "type": "tool_use", "name": title, "id": tool_id,
                    "summary": detail,
                }],
            })
            return

        if kind == "tool_call_update":
            tool_id = str(update.get("toolCallId") or "")
            turn = state.get("active_turn")
            if replay is not None:
                tool = (replay.get("tools") or {}).get(tool_id)
                if tool is None:
                    return
                _acp_apply_tool_update(tool, update)
                if not tool.get("emitted") and update.get("status") in ("completed", "failed"):
                    tool["emitted"] = True
                    _core._acp_emit_event_unlocked(harness, sid, _acp_tool_event(
                        {"msg_id": f"acp-replay-{sid}-{state['next_line']}"}, tool_id, tool))
                return
            if turn is None:
                return
            tool = (turn.get("tools") or {}).get(tool_id)
            if tool is None:
                tool = turn.setdefault("tools", {})[tool_id] = {
                    "title": "tool", "status": "running", "detail": "",
                    "emitted": False,
                }
            output_text = _acp_apply_tool_update(tool, update)
            if update.get("status") in ("completed", "failed") and output_text:
                _acp_emit_delta_unlocked(harness, sid, {
                    "type": "assistant_block",
                    "message_id": turn.get("msg_id") or f"acp-{harness}-tool",
                    "blocks": [{"type": "tool_result", "id": tool_id, "text": tool["output"]}],
                })
            if not tool.get("emitted") and update.get("status") in ("completed", "failed"):
                # Terminal status only: emitting at the first rawInput would
                # freeze the row at in_progress. Long calls live in the
                # bubble meanwhile; anything still open flushes at turn end.
                tool["emitted"] = True
                _core._acp_emit_event_unlocked(harness, sid, _acp_tool_event(turn, tool_id, tool))
            return

        if kind == "available_commands_update":
            state["available_commands"] = update.get("availableCommands") or []
            return

        if kind == "config_option_update":
            options = update.get("configOptions")
            if isinstance(options, list):
                state["config_options"] = options
            return

        if kind == "plan":
            # Whole-plan replace from the harness's TodoList tool (see
            # docs/kimi-code-reference.md §1). Update the live bubble block in
            # place (same message_id) and persist each CHANGED snapshot so a
            # reload shows the plan as of that turn.
            entries = update.get("entries")
            if not isinstance(entries, list):
                return
            norm = []
            for e in entries:
                if not isinstance(e, dict):
                    continue
                content = str(e.get("content") or "").strip()
                if not content:
                    continue
                norm.append({
                    "content": content[:300],
                    "status": str(e.get("status") or "pending"),
                    "priority": str(e.get("priority") or ""),
                })
            if norm == (state.get("plan") or []):
                return
            state["plan"] = norm
            turn = state.get("active_turn")
            if turn is not None:
                _acp_flush_turn_text_unlocked(harness, sid, turn)
            msg_id = (turn or {}).get("msg_id") or f"acp-{harness}-plan"
            _acp_emit_delta_unlocked(harness, sid, {
                "type": "assistant_block",
                "message_id": msg_id,
                "blocks": [{"type": "plan", "entries": norm}],
            })
            _core._acp_emit_event_unlocked(harness, sid, {
                "type": "assistant",
                "message_id": f"{msg_id}-plan",
                "blocks": [{"kind": "plan", "entries": norm}],
            }, save=True)
            return

        if kind == "current_mode_update":
            state["mode"] = update.get("currentModeId")
            return

        if kind == "usage_update":
            state["usage"] = update
            return

        # plan and unknown update kinds: recorded nowhere, never fatal.


def _acp_apply_tool_update(tool, update):
    """Fold one tool_call_update into a tracked tool entry (live turn or
    session/load replay). Returns the output text captured on a terminal
    status, else ""."""
    if update.get("title"):
        # The started-upgrade replaces the lazy create's name-only
        # title with the canonical one (description ?? name).
        tool["title"] = update["title"]
    if update.get("kind"):
        tool["acp_kind"] = update["kind"]
    if update.get("status"):
        tool["status"] = update["status"]
    raw_input = update.get("rawInput")
    if isinstance(raw_input, dict) and raw_input:
        tool["detail"] = _core._tool_use_detail(tool.get("title") or "", raw_input, max_len=160)
        tool["input"] = _core._tool_input_payload(raw_input)
    diff = _acp_tool_content_diff(update)
    if diff:
        tool["diff"] = diff
    if update.get("status") not in ("completed", "failed"):
        return ""
    output_text = _core._acp_tool_content_text(update)
    if output_text:
        tool["output"] = output_text[:_ACP_TOOL_OUTPUT_PREVIEW_MAX]
    return output_text


def _acp_raw_output_text(raw):
    """Best-effort text from a tool_call_update's rawOutput (kimi passes the
    SDK's raw output through: a string for shell-ish tools, a content-part
    list for media/MCP tools, occasionally a dict)."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        return "\n".join(
            str(p.get("text") or "") for p in raw
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
    if isinstance(raw, dict):
        for key in ("output", "text", "stdout"):
            if isinstance(raw.get(key), str):
                return raw[key].strip()
    return ""


def _acp_terminal_output_snapshot(tid):
    """Output of a live OR recently released ACP terminal, or None."""
    if not tid:
        return None
    with _core._ACP_TERMINALS_LOCK:
        entry = _core._ACP_TERMINALS.get(tid)
        cached = _core._ACP_TERMINAL_OUTPUT_CACHE.get(tid)
    if entry is not None:
        return _acp_terminal_output_result(entry)
    return cached


def _acp_terminal_cache_output_unlocked(tid, snapshot):
    """Remember a released terminal's output (bounded, oldest evicted)."""
    _core._ACP_TERMINAL_OUTPUT_CACHE[tid] = snapshot
    _core._ACP_TERMINAL_OUTPUT_CACHE.move_to_end(tid)
    while len(_core._ACP_TERMINAL_OUTPUT_CACHE) > _core._ACP_TERMINAL_OUTPUT_CACHE_MAX:
        _core._ACP_TERMINAL_OUTPUT_CACHE.popitem(last=False)


def _acp_tool_content_text(update):
    """Join an update's content-block text; fall back to rawOutput, then to
    the output of a terminal the update points at (kimi's Bash rows carry
    ONLY `{type:'terminal', terminalId}` — see _ACP_TERMINAL_OUTPUT_CACHE)."""
    parts = []
    terminal_ids = []
    for c in update.get("content") or []:
        if not isinstance(c, dict):
            continue
        inner = c.get("content")
        if isinstance(inner, dict) and isinstance(inner.get("text"), str):
            parts.append(inner["text"])
        elif c.get("type") == "terminal" and c.get("terminalId"):
            terminal_ids.append(str(c["terminalId"]))
    text = "".join(parts).strip()
    if not text:
        text = _core._acp_raw_output_text(update.get("rawOutput"))
    if not text:
        for tid in terminal_ids:
            snap = _core._acp_terminal_output_snapshot(tid)
            out = str((snap or {}).get("output") or "").strip()
            if out:
                text = out
                break
            exit_status = (snap or {}).get("exitStatus") or {}
            if exit_status and exit_status.get("exitCode") not in (None, 0):
                text = f"(no output, exit code {exit_status.get('exitCode')})"
                break
    return text


def _acp_tool_content_diff(update):
    """First {type:'diff', path, oldText, newText} content block, if any.

    Edit/Write calls carry these (see docs/kimi-code-reference.md §Content
    shapes); kept in full so the client can render the diff."""
    for c in update.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "diff":
            old = c.get("oldText")
            new = c.get("newText")
            return {
                "path": str(c.get("path") or ""),
                "oldText": old if isinstance(old, str) else "",
                "newText": new if isinstance(new, str) else "",
            }
    return None


def _acp_tool_name(tool):
    """Map an ACP tool call to a stable Claude-style tool name.

    ACP's own ``title`` is per-call human prose (often the raw argument
    text — a grep pattern, a shell command, a file path) with no generic
    identifier behind it. Using ``title`` as the tool's *name* (as CCC used
    to) makes every call look like a distinct tool to the client's grouping
    logic, which assumes ``name`` is a stable category the way Claude's own
    tool names are — collapsing a run of reads/searches into one unreadable
    "Used A, B, and C" header instead of "read 5 files" (CCC-854). ACP's
    ``kind`` enum IS that stable category; map it onto the Claude names the
    client already knows how to summarize/group, falling back to the raw
    title only for kinds with no clean Claude equivalent (``other``,
    ``think``, MCP-sourced calls, calls made before ``kind`` arrives).
    """
    acp_kind = tool.get("acp_kind") or ""
    title = tool.get("title") or ""
    if acp_kind == "edit":
        return "Write" if title.strip().lower().startswith("write") else "Edit"
    mapped = {
        "read": "Read", "search": "Grep", "execute": "Bash", "fetch": "WebFetch",
        "delete": "Edit", "move": "Edit",
    }.get(acp_kind)
    return mapped or title or "tool"


def _acp_tool_event(turn, tool_id, tool):
    """One finalized conv event per tool call, emitted once rawInput (or
    completion) gives us the real detail — never a kind-only placeholder."""
    block = {
        "kind": "tool_use",
        "name": _acp_tool_name(tool),
        "detail": tool.get("detail") or "",
        "id": tool_id,
    }
    status = tool.get("status")
    if status and status not in ("pending", "in_progress", "running"):
        block["tool_status"] = status
    if tool.get("output"):
        block["output_preview"] = tool["output"]
    if tool.get("input"):
        block["has_input"] = True
        block["input"] = tool["input"]
    if tool.get("diff"):
        block["diff"] = tool["diff"]
    return {
        "type": "assistant",
        "message_id": (turn or {}).get("msg_id") or f"acp-tool-{tool_id}",
        "blocks": [block],
    }


def _acp_permission_tool_detail(tool):
    """Kimi's permission toolCall carries the description in content text
    ('Requesting approval to Running: <cmd>') — surface the actual command."""
    parts = []
    for c in tool.get("content") or []:
        if isinstance(c, dict):
            inner = c.get("content")
            if isinstance(inner, dict) and isinstance(inner.get("text"), str):
                parts.append(inner["text"])
    text = " ".join(" ".join(parts).split())
    prefix = "Requesting approval to "
    if text.startswith(prefix):
        text = text[len(prefix):]
    return text[:200]


_ACP_EMBEDDED_CONTROL_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>"
    r"|<kimi-skill-loaded\b[^>]*>.*?</kimi-skill-loaded>",
    re.DOTALL,
)

_KIMI_GOAL_READ_PREFIX = (
    "<ccc-kimi-goal>\n"
    "The slash-shaped user message below is a CCC compatibility "
    "command, not a Kimi ACP command. Use your GetGoal tool to inspect "
    "the current durable goal, then report its objective and status. "
    "If you need to clear or complete it, use UpdateGoal rather than a "
    "CLI slash command.\n"
    "</ccc-kimi-goal>\n"
)

_KIMI_GOAL_CREATE_PREFIX = (
    "<ccc-kimi-goal>\n"
    "The slash-shaped user message below is a CCC compatibility command, "
    "not a Kimi ACP command. The user explicitly requested a durable goal. "
    "Use your CreateGoal tool with the text after `/goal` as the objective, "
    "then pursue it autonomously until its completion criterion is "
    "satisfied. If the goal must be cleared or completed early, use "
    "UpdateGoal rather than `/goal clear`. If context is filling up, use "
    "the compact-to-queue skill to preserve open work instead of relying "
    "on the CLI-only `/compact`.\n"
    "</ccc-kimi-goal>\n"
)


def _strip_ccc_kimi_goal_prefix(text):
    """Remove only the exact <ccc-kimi-goal> compatibility prefix CCC generated.

    User-authored <ccc-kimi-goal> blocks — including blocks inside a /goal
    objective — are ordinary visible text and must survive byte-for-byte;
    this only matches CCC's own two injected wrapper strings.

    Kimi's ACP agent titles a session from the raw first user message, so
    when that message is CCC's /goal-command shim, the injected instruction
    text (not the user's actual goal) ends up as the session's title unless
    this is applied there too (CCC-920).
    """
    t = (text or "").strip()
    for prefix in (_core._KIMI_GOAL_READ_PREFIX, _KIMI_GOAL_CREATE_PREFIX):
        if t.startswith(prefix):
            return t[len(prefix):].strip()
    return t


def _acp_message_event(state, speaker, text):
    """Replayed user/assistant/thought text → conv event. Control bookkeeping
    (system-reminders, skill-load wrappers, command XML) is dropped like the
    Claude transcript path; empty-after-filter messages return None."""
    if speaker == "thought":
        # Matches the live agent_thought_chunk path (see below): thinking is
        # not XML-stripped, the model quoting a wrapper while reasoning about
        # it is legitimate content.
        text = (text or "").strip()
        if not text:
            return None
        return {
            "type": "assistant",
            "message_id": f"acp-replay-{state['sid']}-{state['next_line']}",
            "blocks": [{"kind": "thinking", "text": text}],
        }
    text = (text or "").strip()
    if not text or _core._is_transcript_control_text(text):
        return None
    if speaker == "user":
        text = _strip_ccc_kimi_goal_prefix(text)
    # Kimi ACP appends injected control XML to the user's real prose instead
    # of delivering it as a standalone message — strip the embedded blocks,
    # then re-check for control-only / empty text.
    text = _ACP_EMBEDDED_CONTROL_RE.sub("", text).strip()
    if not text or _core._is_transcript_control_text(text):
        return None
    if speaker == "user":
        return {"type": "user_text", "text": text}
    return {
        "type": "assistant",
        "message_id": f"acp-replay-{state['sid']}-{state['next_line']}",
        "blocks": [{"kind": "text", "text": text}],
    }


def _kimi_wire_turn_usage_since(wire_path, offset):
    """Return aggregate Kimi usage records written after one prompt began.

    Kimi's ACP adapter does not publish ``usage_update`` notifications, but
    its append-only wire records one ``usage.record`` per model step.  A CCC
    turn can make several tool-use steps, so aggregate the records between the
    prompt's starting byte offset and its terminal response.
    """
    if not wire_path or offset is None:
        return None
    try:
        start = max(0, int(offset))
        with Path(wire_path).open("rb") as f:
            f.seek(start)
            lines = f.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError, ValueError):
        return None

    totals = {
        "input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 0,
    }
    saw_usage_record = False
    fallback_steps = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "usage.record" and record.get("usageScope") in (None, "turn"):
            usage = record.get("usage")
            saw_usage_record = True
        elif record.get("type") == "context.append_loop_event":
            loop = record.get("event") or {}
            usage = loop.get("usage") if loop.get("type") == "step.end" else None
            if isinstance(usage, dict):
                fallback_steps.append(usage)
            continue
        else:
            continue
        if not isinstance(usage, dict):
            continue
        totals["input_tokens"] += _core._codex_int(usage.get("inputOther"))
        totals["cache_read_input_tokens"] += _core._codex_int(usage.get("inputCacheRead"))
        totals["cache_creation_input_tokens"] += _core._codex_int(usage.get("inputCacheCreation"))
        totals["output_tokens"] += _core._codex_int(usage.get("output"))
    if not saw_usage_record:
        for usage in fallback_steps:
            totals["input_tokens"] += _core._codex_int(usage.get("inputOther"))
            totals["cache_read_input_tokens"] += _core._codex_int(usage.get("inputCacheRead"))
            totals["cache_creation_input_tokens"] += _core._codex_int(usage.get("inputCacheCreation"))
            totals["output_tokens"] += _core._codex_int(usage.get("output"))
    return totals if any(totals.values()) else None


def _apply_kimi_turn_usage(event, usage):
    """Set the engine-neutral token-chip fields on one assistant event."""
    if not isinstance(event, dict) or not isinstance(usage, dict):
        return False
    fresh = _core._codex_int(usage.get("input_tokens"))
    cached = _core._codex_int(usage.get("cache_read_input_tokens"))
    created = _core._codex_int(usage.get("cache_creation_input_tokens"))
    output = _core._codex_int(usage.get("output_tokens"))
    if not (fresh or cached or created or output):
        return False
    event["tokens_in"] = fresh + cached + created
    event["tokens_cached"] = cached
    event["tokens_out"] = output
    event["token_usage"] = dict(usage)
    return True


def _acp_finalize_turn(harness, sid, response, pending_entry):
    """A session/prompt response arrived: the turn is over (end_turn,
    cancelled, or error). Fold accumulated text into conv events."""
    requeue_text = None
    requeue_front = False
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid, create=True)
        turn = state.get("active_turn")
        if turn is None or (pending_entry.get("is_active") is False and turn.get("req_id") != pending_entry.get("req_id")):
            turn = None
        error = response.get("error")
        message = error.get("message") if isinstance(error, dict) else str(error or "")
        remote_busy = harness == "kimi" and _core._acp_remote_turn_busy_error(message)
        stop_reason = ((response.get("result") or {}).get("stopReason")) if not error else None
        if turn is not None:
            turn_usage = None
            if harness == "kimi":
                turn_usage = _core._kimi_wire_turn_usage_since(
                    _core._acp_wire_path("kimi", sid), turn.get("wire_offset"),
                )
            # Flush tool rows not yet emitted (calls that never got rawInput,
            # or ended mid-flight) so every tool has exactly one rich row.
            for tid, tool in (turn.get("tools") or {}).items():
                if not tool.get("emitted"):
                    tool["emitted"] = True
                    _core._acp_emit_event_unlocked(harness, sid, _acp_tool_event(turn, tid, tool))
            # Trailing thought/text (everything after the last tool call —
            # usually the answer). Earlier chunks were flushed in stream
            # order as their tool calls arrived.
            final_text = turn.get("text") or ""
            flushed = _acp_flush_turn_text_unlocked(harness, sid, turn, usage=turn_usage)
            if flushed is None and turn_usage:
                for event in reversed(list(state.get("events") or [])):
                    if event.get("type") == "assistant":
                        _apply_kimi_turn_usage(event, turn_usage)
                        break
            # Synchronous-ask handoff: _acp_ask_and_wait reads this after the
            # pending event fires (set post-fold in _acp_handle_message).
            pending_entry["final_text"] = turn.get("text_all") or final_text
            state["active_turn"] = None
            state["status"] = "idle"
            state["stop_reason"] = stop_reason or ("error" if error else None)
            if remote_busy:
                requeue_text = turn.get("prompt") or ""
                requeue_front = bool(turn.get("from_queue"))
        if error and not remote_busy:
            _core._acp_emit_event_unlocked(harness, sid, {
                "type": "result", "subtype": "error",
                "error": message or "ACP turn failed",
            }, save=True)
            _acp_emit_delta_unlocked(harness, sid, {"type": "result", "subtype": "error"})
        elif not error:
            # The wire tail may have folded this turn's step.end already —
            # don't emit a second identical result row.
            recent = list(state.get("events") or [])[-1:]
            if not (recent and recent[0].get("type") == "result"
                    and recent[0].get("subtype") == (stop_reason or "end_turn")):
                _core._acp_emit_event_unlocked(harness, sid, {
                    "type": "result", "subtype": stop_reason or "end_turn",
                }, save=True)
            _acp_emit_delta_unlocked(harness, sid, {
                "type": "result", "subtype": stop_reason or "end_turn",
            })
        _core._ACP_LOCK.notify_all()
    # Kimi can reject a prompt when a turn started outside CCC after our
    # preflight check. Preserve the user's action instead of surfacing a red
    # concurrency error. The queue watcher waits on Kimi's wire state and
    # retries after the external turn reaches its real end boundary.
    if requeue_text and not _core._queue_kimi_remote_busy_retry(
            sid,
            requeue_text,
            front=requeue_front,
    ):
        with _core._ACP_LOCK:
            _core._acp_emit_event_unlocked(harness, sid, {
                "type": "result",
                "subtype": "error",
                "error": (
                    "Kimi rejected the turn as busy, and CCC could not "
                    "persist the retry handoff."
                ),
            }, save=True)
            _acp_emit_delta_unlocked(
                harness,
                sid,
                {"type": "result", "subtype": "error"},
            )


def _acp_reader(harness, conn):
    transport = conn.get("transport")
    try:
        for line in transport.proc.stdout:
            line = (line or "").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                _core._acp_handle_message(harness, payload)
            except Exception:
                # A folding bug must never kill the reader loop.
                pass
    finally:
        # Agent shell tools outlive nothing: kill every terminal this harness's
        # sessions created so they don't leak as orphans past the connection.
        with _core._ACP_TERMINALS_LOCK:
            orphan_tids = [
                tid for tid, term in _core._ACP_TERMINALS.items()
                if term["harness"] == harness
            ]
            for tid in orphan_tids:
                term = _core._ACP_TERMINALS.pop(tid)
                if term["proc"].poll() is None:
                    try:
                        term["proc"].kill()
                    except OSError:
                        pass
                term["exit_event"].set()
        with _core._ACP_LOCK:
            current = _core._ACP_CONNS.get(harness)
            if current is conn:
                _core._ACP_CONNS.pop(harness, None)
            pending = _core._ACP_PENDING.pop(harness, {})
            for entry in pending.values():
                entry["response"] = {"error": {"code": -32003, "message": f"ACP {harness} process exited"}}
                entry["event"].set()
            sessions = _core._ACP_SESSION_STATE.get(harness) or {}
            for st in sessions.values():
                if st.get("status") == "active":
                    st["status"] = "idle"
                    st["active_turn"] = None
            _core._ACP_LOCK.notify_all()


def _acp_ensure(harness):
    """Lazily start + initialize the harness's ACP subprocess."""
    cfg = _core._ACP_HARNESSES.get(harness)
    if cfg is None or not _core._acp_harness_enabled(harness):
        return None
    with _core._ACP_LOCK:
        _core._acp_load_state(harness)
        while True:
            conn = _core._ACP_CONNS.get(harness)
            if conn and conn.get("initialized") and conn["transport"].alive():
                return conn
            if not (conn or {}).get("initializing"):
                break
            _core._ACP_LOCK.wait(0.5)
        conn = {"initializing": True, "next_id": 1, "caps": {}}
        _core._ACP_CONNS[harness] = conn

    resolved = _core._acp_resolve_bin(harness)
    proc = None
    if resolved.get("available"):
        try:
            proc = subprocess.Popen(
                [resolved["bin"], *cfg.get("acp_args", ())],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except (FileNotFoundError, OSError):
            proc = None
    if proc is None:
        reason = resolved.get("reason") or f"could not launch the {cfg.get('label', harness)} CLI"
        with _core._ACP_LOCK:
            _core._ACP_ENSURE_ERROR[harness] = reason
            _core._ACP_CONNS.pop(harness, None)
            _core._ACP_LOCK.notify_all()
        return None

    transport = _AcpTransport(proc)
    conn["proc"] = proc
    conn["transport"] = transport
    reader = threading.Thread(
        target=_acp_reader, args=(harness, conn),
        daemon=True, name=f"acp-reader-{harness}",
    )
    conn["reader"] = reader
    reader.start()

    req_id = _core._acp_request_async(harness, "initialize", {
        "protocolVersion": _ACP_PROTOCOL_VERSION,
        "clientInfo": {
            "name": "claude-command-center",
            "title": "Claude Command Center",
            "version": _core.__version__,
        },
        # terminal capability: the agent's shell tools (kimi Bash/Glob/Grep)
        # route through terminal/* requests, which CCC executes as local
        # subprocesses (_acp_handle_terminal_request). No fs capability: the
        # agent reads/writes files locally on the same machine.
        "clientCapabilities": {"terminal": True},
    })
    response = _acp_wait_response(harness, req_id, timeout=10) if req_id is not None else None
    result = (response or {}).get("result")
    if isinstance(result, dict):
        with _core._ACP_LOCK:
            if _core._ACP_CONNS.get(harness) is conn and transport.alive():
                conn["caps"] = result.get("agentCapabilities") or {}
                conn["agent_info"] = result.get("agentInfo") or {}
                conn["auth_methods"] = result.get("authMethods") or []
                conn["initialized"] = True
                conn["initializing"] = False
                _core._ACP_ENSURE_ERROR.pop(harness, None)
                _core._ACP_LOCK.notify_all()
                return conn
    transport.close()
    with _core._ACP_LOCK:
        _core._ACP_ENSURE_ERROR[harness] = (
            f"{cfg.get('label', harness)} ACP handshake timed out or failed "
            f"(process {'alive' if transport.alive() else 'exited'})"
        )
        if _core._ACP_CONNS.get(harness) is conn:
            _core._ACP_CONNS.pop(harness, None)
        _core._ACP_LOCK.notify_all()
    return None


def _acp_session_new(harness, cwd, prompt=None, model=None, mode=None, effort=None):
    """session/new (+ optional config + optional first prompt)."""
    conn = _core._acp_ensure(harness)
    if conn is None:
        resolved = _core._acp_resolve_bin(harness)
        return {"ok": False, "error": resolved.get("reason") or f"ACP {harness} unavailable"}
    new_params = {"cwd": cwd, "mcpServers": []}
    if harness == "grok":
        # Grok's official always-approve is session/new `_meta.yoloMode`.
        # CCC-spawned sessions are unattended, same as Kimi's spawn mode.
        spawn_mode = (mode or os.environ.get("CCC_GROK_SPAWN_MODE") or "yolo").strip() or "yolo"
        if spawn_mode == "auto":
            new_params["_meta"] = {"autoMode": True}
        elif spawn_mode != "default":
            new_params["_meta"] = {"yoloMode": True}
    resp = _core._acp_request(harness, "session/new", new_params, timeout=25)
    if not resp.get("ok"):
        return resp
    result = resp.get("result") or {}
    sid = str(result.get("sessionId") or "")
    if not sid:
        return {"ok": False, "error": "ACP session/new returned no sessionId"}
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid, create=True, cwd=cwd)
        state["loaded_conn"] = id(conn)
        state["config_options"] = result.get("configOptions") or []
        for opt in state["config_options"]:
            if isinstance(opt, dict) and opt.get("id") == "model":
                state["model"] = opt.get("currentValue")
        # Newer ACP session-state shape (GLM): model/mode ride as
        # models.currentModelId / modes.currentModeId instead of the
        # configOptions select list (kimi's vocabulary). Capture generically.
        models_block = result.get("models")
        if not state.get("model") and isinstance(models_block, dict):
            state["model"] = models_block.get("currentModelId")
        modes_block = result.get("modes")
        if isinstance(modes_block, dict):
            state["mode"] = modes_block.get("currentModeId")
        _acp_save_state_unlocked(harness)
    if mode:
        _core._acp_set_config(harness, sid, "mode", mode)
    if model:
        _core._acp_set_config(harness, sid, "model", model)
    if effort:
        if harness == "grok":
            # Grok advertises effort as sessionConfig options with category
            # "mode" and ids low/medium/high/xhigh. Best-effort; spawn still
            # succeeds if the option name is not accepted.
            _core._acp_set_config(harness, sid, effort, True)
        else:
            # Kimi's ACP "thinking" option: binary on/off on older CLI builds
            # (any non-off value coerces to the model's default effort — a
            # harmless no-op), a real effort picker (low/high/max from the
            # model's support_efforts) on newer ones. Set AFTER the model so the
            # effort validates against the right support_efforts list.
            _core._acp_set_config(harness, sid, "thinking", effort)
    _core._acp_wire_tail_start(harness)
    if prompt:
        sent = _core._acp_prompt(harness, sid, prompt)
        if not sent.get("ok"):
            return sent
    return {
        "ok": True,
        "session_id": sid,
        "via": "acp",
        "harness": harness,
        "config_options": result.get("configOptions") or [],
    }


def _kimi_goal_prompt_text(text):
    """Translate CCC's ``/goal`` affordance into Kimi's native goal tools.

    Kimi ACP handles slash-prefixed prompts before they reach the model, but
    its command catalog does not expose ``/goal``. Kimi does expose durable
    CreateGoal/GetGoal tools to the model, so route only this compatibility
    command around the ACP slash parser. All other text stays byte-for-byte.
    """
    raw = str(text or "")
    match = re.match(r"^\s*/goal(?:\s+(.*))?\s*$", raw, re.IGNORECASE | re.DOTALL)
    if not match:
        return raw
    objective = (match.group(1) or "").strip()
    if not objective:
        return _core._KIMI_GOAL_READ_PREFIX + raw.strip()
    return _KIMI_GOAL_CREATE_PREFIX + raw.strip()


def _acp_prompt(
    harness, sid, text, mode="send", from_queue=False, idempotency_key=None,
):
    """Async session/prompt: ACK returns immediately; the turn streams via
    session/update notifications and finishes in _acp_finalize_turn."""
    if harness in _core._ACP_WORKER_HARNESSES:
        routed = _core._control_plane_engine_call(
            harness, "prompt", {
                "session_id": sid,
                "text": text,
                "mode": mode,
                "from_queue": bool(from_queue),
            },
            idempotency_key=idempotency_key,
        )
        if routed is not None:
            return routed
    if harness == "grok" and _core._grok_external_writer_active(sid):
        return {"ok": False, "error": "Grok session is active in a terminal — close it before sending.", "code": "grok_external_active"}
    if not text:
        return {"ok": False, "error": "empty prompt"}
    text = _core._strip_lone_surrogates(str(text))
    visible_text = text
    if harness == "kimi":
        text = _core._kimi_goal_prompt_text(text)
    if harness in ("kimi", "grok") and re.match(r"^\s*/context(\s|$)", text, re.IGNORECASE):
        return {
            "ok": False,
            "error": (
                f"{harness.capitalize()} sessions don't support /context (that's a "
                "Claude Code CLI command) — check the token-usage strip above the "
                "composer for this session's context instead."
            ),
            "code": "unsupported_command",
        }
    # ACP transports (kimi/grok) don't support IN-PLACE mid-turn steering the
    # way Codex/Claude do — a session/prompt sent while a turn is active gets
    # rejected by the agent itself ("Invalid request: another turn is
    # already in progress"), which would surface as a raw STOPPED error if
    # sent straight through. Steer is therefore returned here exactly like a
    # regular send (a plain busy code) — the inject_input caller is the one
    # that turns a steer's busy code into cancel-then-resend (session/cancel,
    # the same primitive the Esc button uses) before ever falling back to
    # the durable queue (CCC-922).
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid, create=True)
        if state.get("status") == "active":
            return {"ok": False, "error": "turn already in progress", "code": "busy"}
    if harness == "kimi" and _core._kimi_wire_turn_active(sid):
        return {"ok": False, "error": "turn already in progress", "code": "busy"}
    attach_err = _core._acp_ensure_session_loaded(harness, sid)
    if attach_err is not None:
        return attach_err
    def activate_turn(req_id, entry):
        """Install a turn before its request is visible to the ACP reader.

        A local ACP harness can return the terminal prompt response before
        ``_acp_request_async`` returns.  Registering the turn at the request
        boundary prevents that completion from being folded first and then
        resurrected as a permanent active/Thinking turn.
        """
        state = _core._acp_session(harness, sid, create=True)
        state["turn_seq"] += 1
        wire_offset = None
        if harness == "kimi":
            try:
                wire_offset = _core._acp_wire_path("kimi", sid).stat().st_size
            except (AttributeError, OSError):
                pass
        state["active_turn"] = {
            "req_id": req_id,
            "msg_id": f"acp-{harness}-{state['turn_seq']}",
            "text": "", "thought": "", "tools": {},
            # The original user text is the canonical retry payload. Kimi may
            # receive a translated compatibility prompt (for `/goal`), but a
            # remote-busy race must requeue the visible command so the next
            # send translates exactly once and transcript text stays honest.
            "prompt": visible_text,
            "from_queue": bool(from_queue),
            "started_at": time.time(),
            "wire_offset": wire_offset,
        }
        state["status"] = "active"
        state["deltas"].clear()
        entry["is_active"] = True
        _core._acp_emit_event_unlocked(harness, sid, {
            "type": "user_text", "text": visible_text,
        }, save=True)

    def roll_back_unsent_turn(req_id, _entry):
        state = _core._acp_session(harness, sid)
        turn = (state or {}).get("active_turn") or {}
        if turn.get("req_id") == req_id:
            state["active_turn"] = None
            state["status"] = "idle"

    req_id = _core._acp_request_async(harness, "session/prompt", {
        "sessionId": sid,
        "prompt": [{"type": "text", "text": text}],
    }, sid=sid, on_registered=activate_turn, on_send_failed=roll_back_unsent_turn)
    if req_id is None:
        return {"ok": False, "error": f"ACP {harness} send failed"}
    return {
        "ok": True, "via": "acp-prompt", "harness": harness,
        "session_id": sid, "turn": state.get("turn_seq"), "req_id": req_id,
    }


def _acp_ask_and_wait(harness, sid, text, timeout_ms=30000):
    """Synchronous inject+wait for an ACP harness session (the /api/ask
    contract): drive session/prompt, block for the turn-end response, and
    return the assistant text the turn produced."""
    if harness in _core._ACP_WORKER_HARNESSES:
        routed = _core._control_plane_engine_call(
            harness, "ask", {
                "session_id": sid,
                "text": text,
                "timeout_ms": timeout_ms,
            },
        )
        if routed is not None:
            return routed
    source = f"{harness}-acp"
    started = time.monotonic()
    res = _core._acp_prompt(harness, sid, text)
    if not res.get("ok"):
        res.setdefault("source", source)
        return res
    req_id = res.get("req_id")
    if req_id is None:
        return {"ok": False, "error": f"ACP {harness} send failed", "source": source}
    timeout_s = max(0.5, timeout_ms / 1000.0)
    with _core._ACP_LOCK:
        entry = (_core._ACP_PENDING.get(harness) or {}).get(req_id)
    if entry is None:
        return {"ok": False, "error": "prompt request not registered", "source": source}
    entry["event"].wait(timeout_s)
    with _core._ACP_LOCK:
        entry = (_core._ACP_PENDING.get(harness) or {}).pop(req_id, None) or entry
    response = entry.get("response")
    duration_ms = int((time.monotonic() - started) * 1000)
    if response is None:
        # Timed out mid-turn — the still-running turn's partial text is the
        # best reply we have (mirrors ask_engine_session_and_wait's timeout).
        with _core._ACP_LOCK:
            state = _core._acp_session(harness, sid)
            partial = str(((state or {}).get("active_turn") or {}).get("text") or "")
        return {
            "ok": False, "error": "timeout", "partial": partial.strip(),
            "duration_ms": duration_ms, "source": source,
        }
    final_text = str(entry.get("final_text") or "").strip()
    if response.get("error"):
        err = response["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        return {
            "ok": False, "error": message or "ACP turn failed",
            "partial": final_text, "duration_ms": duration_ms, "source": source,
        }
    return {
        "ok": True, "text": final_text, "duration_ms": duration_ms,
        "num_turns": 1, "cost_usd": None, "source": source,
    }


def _acp_cancel(harness, sid):
    if harness in _core._ACP_WORKER_HARNESSES:
        routed = _core._control_plane_engine_call(
            harness, "cancel", {"session_id": sid},
        )
        if routed is not None:
            return routed
    # session/cancel targets a sessionId inside the live ACP connection —
    # if this sid was never loaded onto the current connection (fresh
    # reconnect after a restart, or a session driven outside CCC), the CLI
    # has no such session and silently drops the notification while we'd
    # still report ok:true. Attach first so Esc actually reaches a turn the
    # process knows about (same pattern as _acp_set_config).
    attach_err = _core._acp_ensure_session_loaded(harness, sid)
    if attach_err is not None:
        return attach_err
    # ACP session/cancel is a notification (no id, no response).
    sent = _core._acp_send(harness, {
        "jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sid},
    })
    return {"ok": sent, "via": "acp-cancel"}


def _acp_load(harness, sid, cwd):
    """Attach to an existing harness session in the current ACP connection.

    Uses session/load (history replays as session/update chunks, folded by
    the replay branch and flushed here) when CCC has no transcript yet;
    session/resume (no replay) when the CCC transcript already holds the
    history — e.g. re-attaching after a CCC restart.
    """
    conn = _core._acp_ensure(harness)
    if harness == "grok" and _core._grok_external_writer_active(sid):
        return {"ok": False, "error": "Grok session is active in a terminal — close it before sending.", "code": "grok_external_active"}
    if conn is None:
        return {"ok": False, "error": _core._acp_conn_error(harness)}
    has_history = False
    try:
        has_history = _core._acp_transcript_path(harness, sid).stat().st_size > 0
    except OSError:
        pass
    method = "session/resume" if has_history else "session/load"
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid, create=True, cwd=cwd)
        if method == "session/load" and state.get("replay") is None:
            state["replay"] = {"kind": None, "text": ""}
    resp = _core._acp_request(harness, method, {
        "sessionId": sid, "cwd": cwd, "mcpServers": [],
    }, timeout=30, sid=sid)
    # A dormant harness session can be mid-write from another live consumer
    # of its own on-disk store (e.g. a native TUI the user has open outside
    # CCC) right as we attach — the harness's own resume handler can trip
    # over that half-written state and return a transient, cryptic error
    # (observed: a Grok internal TypeError string). One quiet retry after a
    # short beat covers the common case instead of surfacing a raw crash
    # message the user has to notice and manually resend around (CCC-853).
    if not resp.get("ok") and not resp.get("auth_required"):
        time.sleep(0.75)
        resp = _core._acp_request(harness, method, {
            "sessionId": sid, "cwd": cwd, "mcpServers": [],
        }, timeout=30, sid=sid)
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid, create=True, cwd=cwd)
        replay = state.get("replay")
        if replay:
            _core._acp_replay_flush_unlocked(harness, sid, state, replay)
        state["replay"] = None
        if resp.get("ok"):
            state["attached"] = True
            state["loaded_conn"] = id(conn)
            if cwd:
                state["cwd"] = cwd
            # load AND resume both return configOptions (kimi server.ts:363-408)
            # — refresh the snapshot so attach-on-view picks up the session's
            # real model/mode instead of keeping a stale spawn-time value.
            result = resp.get("result") or {}
            options = result.get("configOptions")
            if isinstance(options, list) and options:
                state["config_options"] = options
                for opt in options:
                    if isinstance(opt, dict) and opt.get("id") == "model":
                        state["model"] = opt.get("currentValue")
        _acp_save_state_unlocked(harness)
    if resp.get("ok"):
        _core._acp_wire_tail_start(harness)
    if not resp.get("ok"):
        return resp
    return {"ok": True, "session_id": sid, "harness": harness, "via": f"acp-{method.split('/')[-1]}"}


_ACP_ATTACH_VIEW_MIN_INTERVAL_S = 60.0


def _acp_maybe_attach_on_view(harness, sid):
    """Attach a viewed-but-empty session so its history backfills.

    A TUI-launched session has no CCC transcript, so opening it in CCC used
    to show an empty pane. On view (and throttled per sid), attach via
    session/load — the replay folds into the CCC transcript and the pane
    fills. Returns the _acp_load result, or None when no attach was needed.
    """
    if not _core._acp_harness_enabled(harness):
        return None
    if harness in _core._ACP_WORKER_HARNESSES:
        routed = _core._control_plane_engine_call(
            harness, "attach", {"session_id": sid}, mutate=False,
        )
        if routed is not None:
            return routed
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid)
        conn = _core._ACP_CONNS.get(harness)
        loaded = bool(state and conn and state.get("loaded_conn") == id(conn))
        attempted = (state or {}).get("attach_attempted_at") or 0
    cwd = (state or {}).get("cwd") or ""
    if not cwd and harness == "kimi":
        cwd = (_core._kimi_session_index().get(sid) or {}).get("work_dir") or ""
    if not cwd and harness == "grok":
        session_dir = _core._grok_session_dir(sid)
        if session_dir is not None:
            cwd = _core._grok_decode_bucket_cwd(session_dir.parent) or ""
    # Viewed sessions are watched for TUI-originated turns (KIMI-FIXES-3) —
    # independent of whether an attach/backfill is needed this process.
    with _core._ACP_LOCK:
        st = _core._acp_session(harness, sid, create=True, cwd=cwd)
        st["wire_watch"] = True
    _core._acp_wire_tail_start(harness)
    if loaded:
        return None
    if time.time() - attempted < _ACP_ATTACH_VIEW_MIN_INTERVAL_S:
        return None
    try:
        if _core._acp_transcript_path(harness, sid).stat().st_size > 0:
            return None
    except OSError:
        pass
    if not cwd:
        return None
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid, create=True, cwd=cwd)
        state["attach_attempted_at"] = time.time()
    return _acp_load(harness, sid, cwd)


def _acp_set_config(harness, sid, config_id, value):
    if harness in _core._ACP_WORKER_HARNESSES:
        routed = _core._control_plane_engine_call(
            harness, "config", {
                "session_id": sid,
                "config_id": config_id,
                "value": value,
            },
        )
        if routed is not None:
            return routed
    attach_err = _core._acp_ensure_session_loaded(harness, sid)
    if attach_err is not None:
        return attach_err
    resp = _core._acp_request(harness, "session/set_config_option", {
        "sessionId": sid, "configId": config_id, "value": value,
    }, timeout=10, sid=sid)
    if resp.get("ok"):
        # Keep the local snapshot honest — session rows read state["model"]
        # for the cost-tier icon, and it otherwise stays at the session/new
        # default forever.
        result = resp.get("result") or {}
        with _core._ACP_LOCK:
            state = _core._acp_session(harness, sid, create=True)
            options = result.get("configOptions")
            if isinstance(options, list):
                state["config_options"] = options
                for opt in options:
                    if isinstance(opt, dict) and opt.get("id") == "model":
                        state["model"] = opt.get("currentValue")
            elif config_id == "model":
                state["model"] = value
            for opt in state.get("config_options") or []:
                if isinstance(opt, dict) and opt.get("id") == config_id:
                    opt["currentValue"] = value
            _acp_save_state_unlocked(harness)
    return resp


def _acp_ensure_session_loaded(harness, sid):
    """Ensure the ACP conn is up and `sid` is attached to it (auto-attach
    from the harness's on-disk store when needed). Returns an error dict on
    failure, None when the session is loaded."""
    conn = _core._acp_ensure(harness)
    if harness == "grok" and _core._grok_external_writer_active(sid):
        return {"ok": False, "error": "Grok session is active in a terminal — close it before sending.", "code": "grok_external_active"}
    if conn is None:
        return {"ok": False, "error": _core._acp_conn_error(harness)}
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid, create=True)
        loaded = state.get("loaded_conn") == id(conn)
        cwd = state.get("cwd") or ""
    if loaded:
        return None
    if not cwd and harness == "kimi":
        cwd = (_core._kimi_session_index().get(sid) or {}).get("work_dir") or ""
    if not cwd and harness == "grok":
        session_dir = _core._grok_session_dir(sid)
        if session_dir is not None:
            cwd = _core._grok_decode_bucket_cwd(session_dir.parent) or ""
    if not cwd:
        return {"ok": False, "error": "session cwd unknown — cannot attach", "code": "no_cwd"}
    attached = _acp_load(harness, sid, cwd)
    if not attached.get("ok"):
        return attached
    return None


def _acp_resolve_approval(harness, sid, request_id, option_id=None):
    """Answer a pending session/request_permission. option_id None = cancel."""
    if harness in _core._ACP_WORKER_HARNESSES:
        routed = _core._control_plane_engine_call(
            harness, "approval", {
                "session_id": sid,
                "request_id": request_id,
                "option_id": option_id,
            },
        )
        if routed is not None:
            return routed
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid)
        pending = (state or {}).get("pending_permissions") or {}
        entry = pending.pop(str(request_id), None)
    if entry is None:
        return {"ok": False, "error": "no pending permission request"}
    if option_id:
        outcome = {"outcome": "selected", "optionId": option_id}
    else:
        outcome = {"outcome": "cancelled"}
    sent = _core._acp_respond(harness, entry.get("req_id", request_id), result={"outcome": outcome})
    with _core._ACP_LOCK:
        _acp_save_state_unlocked(harness)
        _core._ACP_LOCK.notify_all()
    return {"ok": sent, "via": "acp-approval"}


def _acp_session_snapshot(harness, sid):
    if harness in _core._ACP_WORKER_HARNESSES:
        routed = _core._control_plane_engine_call(
            harness, "snapshot", {"session_id": sid}, mutate=False,
        )
        if routed is not None:
            return routed.get("snapshot")
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid)
        if state is None:
            return None
        return {
            "sid": sid,
            "harness": harness,
            "cwd": state.get("cwd") or "",
            "status": state.get("status") or "idle",
            "model": state.get("model"),
            "turn_seq": state.get("turn_seq") or 0,
            "pending_permissions": len(state.get("pending_permissions") or {}),
            "config_options": state.get("config_options") or [],
            "updated_at": state.get("updated_at") or 0,
        }


def _acp_is_session(harness, sid):
    """Does this sid belong to the harness? Cheap, no subprocess spawn.

    True for CCC-attached sessions, sessions with a CCC transcript, and
    (kimi) sessions present in the harness's own on-disk index so TUI
    sessions route through the ACP attach flow too.
    """
    if not sid:
        return False
    with _core._ACP_LOCK:
        _core._acp_load_state(harness)
        if sid in (_core._ACP_SESSION_STATE.get(harness) or {}):
            return True
    if _core._acp_transcript_path(harness, sid).is_file():
        return True
    if harness == "kimi" and sid in _core._kimi_session_index():
        return True
    if harness == "grok" and _core._is_grok_session(sid):
        return True
    return False


def _is_kimi_session(session_id):
    return _acp_is_session("kimi", str(session_id or ""))


def _session_acp_harness(session_id):
    """ACP harness name for a session, or None if it is not ACP-backed."""
    sid = str(session_id or "").strip()
    if not sid:
        return None
    if _core._is_kimi_session(sid):
        return "kimi"
    if _acp_is_session("grok", sid):
        return "grok"
    return None


def _resolve_kimi_bin():
    return _core._acp_resolve_bin("kimi")


def _resolve_grok_bin():
    return _core._acp_resolve_bin("grok")


