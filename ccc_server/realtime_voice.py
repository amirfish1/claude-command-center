# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Realtime voice mode for CCC, powered by the Codex app-server
`thread/realtime/*` API (experimental).

One voice session at a time. The browser negotiates WebRTC directly with
the app-server's voice host, so audio never transits CCC: the server
relays only the SDP offer/answer plus JSON-RPC transcript/state events,
fans them out over SSE, and answers `item/tool/call` requests from the
backing Codex thread against a small read-only tool surface. Mutations
are never executed by the model — they become proposals in
ccc_server/assistant_actions.py that the user confirms by click.

The OpenAI API key (BYOK profile, provider "openai") is injected into the
child environment only: never on argv, never logged, never persisted,
never returned from any API.

Stdlib-only, same rule as server.py. Names still living in server.py are
reached via ``_core`` at call time.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import tempfile

from ccc_server import core as _core, test_isolation_active
from ccc_server.paths import COMMAND_CENTER_STATE_DIR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VOICE_STATE_DIR = COMMAND_CENTER_STATE_DIR / "voice"
if test_isolation_active():
    # Keep test writes (config, opt-in transcripts, scratch) out of the
    # user's real state dir — same redirect convention as server.py.
    VOICE_STATE_DIR = Path(tempfile.gettempdir()) / f"ccc-test-voice-{os.getpid()}"
VOICE_CONFIG_FILE = VOICE_STATE_DIR / "voice.json"
VOICE_TRANSCRIPT_DIR = VOICE_STATE_DIR / "transcripts"
VOICE_SCRATCH_DIR = VOICE_STATE_DIR / "scratch"

# version:"v3" only accepts the v1 voice names (the app-server rejects v2
# voices with "not supported for v3"), so the default must come from that
# set; "cove" is also the upstream defaultV1.
DEFAULT_VOICE = "cove"
DEFAULT_MAX_MINUTES = 15
DEFAULT_IDLE_SECONDS = 120
# Browsers throttle hidden-tab timers to ~1/min, so 45s would kill a voice
# session the moment the user switches tabs mid-conversation. 120s survives
# one throttle cycle; a truly closed tab still dies fast via sendBeacon.
HEARTBEAT_TIMEOUT_SECONDS = 120.0
START_TIMEOUT_SECONDS = 60.0
CALL_TIMEOUT_SECONDS = 30.0
# WebRTC signaling deadline: issue #35094-style failures can leave the
# app-server silent (no sdp, no error) after the call is created.
SDP_ANSWER_TIMEOUT_SECONDS = 20.0
EVENT_BUFFER_MAX = 600
TRANSCRIPT_MAX_ITEMS = 400
# Volatile SSE events (websocket-path audio chunks) are dropped from replay
# if they sat in the buffer this long — stale audio is worse than a gap.
VOLATILE_EVENT_MAX_AGE_S = 1.5

# Schema-derived catalog (codex app-server generate-json-schema --experimental,
# codex-cli 0.160.0). Refreshed from live thread/realtime/listVoices on each
# session start; this is the offline fallback for /api/voice/voices.
VOICES_V1 = ["juniper", "maple", "spruce", "ember", "vale", "breeze", "arbor", "sol", "cove"]
VOICES_V2 = ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"]
DEFAULT_V1 = "cove"
DEFAULT_V2 = "marin"
_ALL_VOICES = set(VOICES_V1) | set(VOICES_V2)

# Dynamic tools the backing Codex thread may call. Read-only by contract;
# every mutation goes through ccc_propose_action -> assistant_actions.
TOOL_NAMES = ("ccc_attention", "ccc_session", "ccc_sessions", "ccc_models", "ccc_ticket",
              "ccc_queues", "ccc_jobs", "ccc_triage_queue", "ccc_propose_action")

_TICKET_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}-\d{1,7}$")
_QUEUE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_VOICE_PROMPT = (
    "You are the voice of Claude Command Center (CCC), a dashboard for the "
    "user's coding-agent sessions. Answers are spoken aloud: keep them to "
    "one to three short sentences unless the user asks for detail. For "
    "anything about live sessions, queues, or tickets, delegate to your "
    "Codex backend, which has ccc_* tools with the real board state — never "
    "guess or invent session state. For anything that changes state (send "
    "input to a session, spawn a session, file or comment a ticket), the "
    "backend must call ccc_propose_action — tell the user the action is "
    "waiting for their click in the CCC dashboard, and never claim it "
    "happened before that confirmation arrives."
)

_CODEX_INSTRUCTIONS = (
    "You back a CCC voice session. Use only the ccc_* tools for board "
    "state; do not run shell commands or read files. Keep tool results "
    "compact. Use ccc_ticket to explain the recorded needs-input question and "
    "recent ticket context, ccc_queues for named queue state, ccc_jobs for "
    "scheduled jobs (systemd timers and cron: did it run, did it succeed, "
    "when is it next, why not), ccc_models to "
    "resolve an explicit model name to its approved ID before triage selection, and "
    "ccc_triage_queue to propose a senior triage session. Anything that mutates "
    "CCC must go through ccc_propose_action; you never execute changes yourself. "
    "Every tool result names its source and as-of time. Ticket needs-input "
    "questions and blocker notes are recorded reports with an age, not verified "
    "current health: say \"recorded N days ago\" and never state them as current "
    "fact. Only pid-verified sessions and the queue health snapshot are current."
)

_APPROVAL_DENY_METHODS = frozenset({
    "execCommandApproval",
    "applyPatchApproval",
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "mcpServer/elicitation/request",
    "item/tool/requestUserInput",
})

# ---------------------------------------------------------------------------
# Config persistence (voice.json — voice name, BYOK profile, limits)
# ---------------------------------------------------------------------------

_CONFIG_LOCK = threading.Lock()


def _default_config():
    return {
        "voice": DEFAULT_VOICE,
        "profile": "",
        "max_minutes": DEFAULT_MAX_MINUTES,
        "idle_seconds": DEFAULT_IDLE_SECONDS,
        "save_transcripts": False,
        "allow_api_fallback": True,
        "show_mobile_btn": True,
    }


def voice_config_load():
    try:
        data = json.loads(VOICE_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    cfg = _default_config()
    if isinstance(data, dict):
        for key in cfg:
            if key in data:
                cfg[key] = data[key]
    return _config_sanitize(cfg)


def _config_sanitize(cfg):
    out = _default_config()
    voice = str(cfg.get("voice") or "").strip().lower()
    out["voice"] = voice if voice in _ALL_VOICES else DEFAULT_VOICE
    out["profile"] = str(cfg.get("profile") or "").strip()[:120]
    try:
        out["max_minutes"] = max(1, min(120, int(cfg.get("max_minutes"))))
    except (TypeError, ValueError):
        out["max_minutes"] = DEFAULT_MAX_MINUTES
    try:
        out["idle_seconds"] = max(15, min(1800, int(cfg.get("idle_seconds"))))
    except (TypeError, ValueError):
        out["idle_seconds"] = DEFAULT_IDLE_SECONDS
    out["save_transcripts"] = bool(cfg.get("save_transcripts"))
    out["allow_api_fallback"] = bool(cfg.get("allow_api_fallback", True))
    out["show_mobile_btn"] = bool(cfg.get("show_mobile_btn", True))
    return out


def voice_config_save(patch):
    if not isinstance(patch, dict):
        return {"ok": False, "error": "config patch must be an object"}
    with _CONFIG_LOCK:
        cfg = _config_sanitize({**voice_config_load(), **patch})
        try:
            VOICE_STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = VOICE_CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(VOICE_CONFIG_FILE)
        except OSError as e:
            return {"ok": False, "error": f"could not save voice config: {e}"}
    return {"ok": True, "config": cfg}


# ---------------------------------------------------------------------------
# BYOK OpenAI key resolution + profile helpers
# ---------------------------------------------------------------------------

def _openai_profiles():
    """BYOK profiles that hold an `openai` key (names only, never the key)."""
    try:
        profiles = _core.byok_list_profiles()
    except Exception:
        return []
    return [p["name"] for p in profiles if "openai" in (p.get("providers") or [])]


def _resolve_openai_key(profile):
    """(key, profile_used, error_dict). The key only ever lives in the child env."""
    candidates = []
    if profile:
        candidates = [profile]
    else:
        candidates = _openai_profiles()
    for name in candidates:
        try:
            key = _core.byok_get_key(name, "openai")
        except Exception:
            key = None
        if key:
            return key, name, None
    if profile:
        return None, None, {
            "ok": False, "code": "voice_no_openai_key",
            "error": f"BYOK profile {profile!r} has no OpenAI key. "
                     "Pick another profile or add an OpenAI key in Settings > BYOK.",
        }
    return None, None, {
        "ok": False, "code": "voice_no_openai_key",
        "error": "Voice needs an OpenAI API key. Add one under Settings > BYOK "
                 "(provider: openai), then try again.",
    }


def _redact(text, key):
    if not key:
        return text
    try:
        return str(text).replace(key, "[REDACTED]")
    except Exception:
        return "[redacted]"


# ---------------------------------------------------------------------------
# Voice catalog (offline fallback + live refresh)
# ---------------------------------------------------------------------------

_VOICES_LOCK = threading.Lock()
_VOICES_LIVE = None


def voice_catalog():
    with _VOICES_LOCK:
        live = _VOICES_LIVE
    if live:
        return {"ok": True, "source": "live", "voices": live}
    return {
        "ok": True,
        "source": "fallback",
        "voices": {"v1": list(VOICES_V1), "v2": list(VOICES_V2),
                   "defaultV1": DEFAULT_V1, "defaultV2": DEFAULT_V2},
    }


def _record_live_voices(result):
    global _VOICES_LIVE
    voices = (result or {}).get("voices") or {}
    v1 = [v for v in voices.get("v1") or [] if isinstance(v, str)]
    v2 = [v for v in voices.get("v2") or [] if isinstance(v, str)]
    if not (v1 or v2):
        return
    with _VOICES_LOCK:
        _VOICES_LIVE = {
            "v1": v1 or list(VOICES_V1),
            "v2": v2 or list(VOICES_V2),
            "defaultV1": voices.get("defaultV1") or DEFAULT_V1,
            "defaultV2": voices.get("defaultV2") or DEFAULT_V2,
        }


# ---------------------------------------------------------------------------
# Board briefing (bounded: cached attention feed + queue rollup only)
# ---------------------------------------------------------------------------

def build_briefing(limit=8, feed_fn=None, rollup_fn=None):
    """Compact, size-capped board snapshot for the voice session's seed.

    Bounded work by contract: one cached attention-feed call + one cached
    queue rollup, no per-row scans, no subprocesses. `feed_fn`/`rollup_fn`
    are injectable so the perf test can count calls.
    """
    if feed_fn is None:
        feed_fn = _core.compute_attention_feed
    if rollup_fn is None:
        rollup_fn = _core._watchtower_queue_rollup
    lines = []
    now = datetime.now().strftime("%H:%M")
    try:
        feed = feed_fn(recent_only=True, limit=limit) or {}
    except Exception:
        feed = {}
    items = feed.get("items") or []
    if items:
        lines.append(f"CCC board at {now}: {len(items)} item(s) need attention.")
        for it in items[:limit]:
            label = it.get("where") or it.get("kind") or "item"
            repo = it.get("repo") or it.get("folder_label") or ""
            q = (it.get("question_text") or it.get("title") or "").strip().replace("\n", " ")
            line = f"- {label}"
            if repo:
                line += f" in {repo}"
            if it.get("session_id"):
                line += f" [session {str(it['session_id'])[:8]}]"
            if q:
                line += f": {q[:160]}"
            lines.append(line)
    else:
        lines.append(f"CCC board at {now}: nothing needs attention right now.")
    try:
        roll = rollup_fn() or {}
    except Exception:
        roll = {}
    if roll:
        lines.append(
            "WatchTower: "
            f"{roll.get('open_total', 0)} open ticket(s) across "
            f"{roll.get('queues_total', 0)} queue(s), "
            f"{roll.get('workers_live', 0)} worker(s) live, "
            f"{roll.get('stuck_total', 0)} stuck queue(s)."
        )
    text = "\n".join(lines)
    return text[:3500]


def _fmt_tool_text(feed_or_data):
    return {"contentItems": [{"type": "inputText", "text": feed_or_data}], "success": True}


def _tool_err(msg):
    return {"contentItems": [{"type": "inputText", "text": f"error: {msg}"}], "success": False}


# Claude registry status -> the state labels the session list uses.
_REGISTRY_STATE = {"busy": "working", "idle": "idle"}


def _utc_stamp(epoch):
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime("%H:%M:%SZ")


def _age_text(seconds):
    """Spoken-friendly age: 40s, 12m, 5h, 3d."""
    try:
        s = max(0, int(seconds))
    except (TypeError, ValueError):
        return "unknown age"
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{round(s / 60)}m"
    if s < 172800:
        return f"{round(s / 3600)}h"
    return f"{round(s / 86400)}d"


def _asof(epoch, now=None):
    """'02:24:40Z (12s ago)' for an epoch, or 'time unknown'."""
    try:
        epoch = float(epoch)
    except (TypeError, ValueError):
        return "time unknown"
    if epoch <= 0:
        return "time unknown"
    now = time.time() if now is None else now
    return f"{_utc_stamp(epoch)} ({_age_text(now - epoch)} ago)"


def _iso_epoch(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _split_needs_input(items):
    """Split WatchTower ticket records by how strong the needs-input signal is.

    explicit: status blocked/awaiting_answer, i.e. the ticket is parked on the human.
    claimed: needs_input flag still set while a worker holds the ticket, i.e. a
    question was recorded but the ticket is in flight and may already be answered.
    """
    explicit, claimed = [], []
    for item in items:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref") or "").upper()
        if not _TICKET_REF_RE.fullmatch(ref):
            continue
        status = str(item.get("status") or "").lower()
        if status in ("blocked", "awaiting_answer"):
            explicit.append(item)
        elif item.get("needs_input") and status != "closed":
            claimed.append(item)
    return explicit, claimed


def _ticket_age(item, now):
    ts = _iso_epoch(item.get("blocked_at") or item.get("updated_at"))
    return _age_text(now - ts) if ts else "unknown age"


def _tickets_synced_at():
    try:
        return _core._ux_fixes_list_synced_at(None, None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# The voice session
# ---------------------------------------------------------------------------

class VoiceSession:
    """One realtime voice session = one `codex app-server` child."""

    def __init__(self, session_id, voice, profile):
        self.id = session_id
        self.voice = voice
        self.profile = profile
        self.state = "connecting"  # connecting|live|stopping|closed|error
        self.state_detail = ""
        self.error = None
        self.reason = None
        self.proc = None
        self.thread_id = None
        self.realtime_version = None
        self.transport = None        # "webrtc" (subscription) | "websocket" (api key)
        self.billing = None          # "subscription" | "api_key"
        self.started_at = time.time()
        self.closed_at = None
        self.last_activity = time.monotonic()
        self.last_heartbeat = None  # None until the browser checks in once
        self.audio_ms = 0
        self.codex_tokens = 0
        self.transcript = []          # [{role, text, ts}] memory only
        self._tr_open = {}            # role -> partial text in flight
        self.pending_actions = {}     # action_id -> public action dict
        self.seq = 0
        self.events = deque(maxlen=EVENT_BUFFER_MAX)
        self.cond = threading.Condition()
        self._write_lock = threading.Lock()
        self._pending = {}            # rpc id -> (event, result box)
        self._next_id = 0
        self._sdp_event = threading.Event()
        self._sdp_answer = None
        self._connect_error = None   # realtime/error seen while connecting
        self._stderr_tail = deque(maxlen=40)
        self._closed_once = False
        self._close_lock = threading.Lock()   # atomic _closed_once check-and-set
        self._close_done = threading.Event()  # bookkeeping fully finished
        self._key = None              # held only to redact accidents; never read
        self._watchdog_stop = threading.Event()
        self._watchdog_started = False  # respawns share one watchdog
        self._welcome_sent = False

    # -- events ---------------------------------------------------------

    def emit(self, etype, data, volatile=False):
        with self.cond:
            self.seq += 1
            ev = {"seq": self.seq, "type": etype, "data": data, "ts": time.time()}
            if volatile:
                ev["volatile"] = True
            self.events.append(ev)
            self.cond.notify_all()

    def set_state(self, state, detail=""):
        self.state = state
        self.state_detail = detail
        self.emit("state", {"state": state, "detail": detail})

    def touch(self):
        self.last_activity = time.monotonic()

    def _fresh_events(self, seq):
        """Buffered events after `seq`, with stale volatile ones dropped:
        an audio chunk older than a second and a half would replay as noise."""
        now = time.time()
        return [dict(e) for e in self.events
                if e["seq"] > seq
                and not (e.get("volatile") and now - e["ts"] > VOLATILE_EVENT_MAX_AGE_S)]

    def events_since(self, seq):
        with self.cond:
            return self._fresh_events(seq)

    def wait_events(self, seq, timeout=15.0):
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                pending = self._fresh_events(seq)
                if pending:
                    return pending
                left = deadline - time.monotonic()
                if left <= 0 or self.state in ("closed", "error"):
                    # Flush a final state event so waiters see the close.
                    return self._fresh_events(seq)
                self.cond.wait(min(left, 5.0))

    # -- JSON-RPC -------------------------------------------------------

    def _send(self, obj):
        line = json.dumps(obj) + "\n"
        proc = self.proc
        if proc is None or proc.stdin is None:
            return False
        try:
            with self._write_lock:
                proc.stdin.write(line.encode("utf-8"))
                proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def call(self, method, params, timeout=CALL_TIMEOUT_SECONDS):
        with self._write_lock:
            self._next_id += 1
            rid = self._next_id
        ev = threading.Event()
        box = {}
        self._pending[rid] = (ev, box)
        if not self._send({"id": rid, "method": method, "params": params or {}}):
            self._pending.pop(rid, None)
            return None, {"message": "app-server is not running"}
        if not ev.wait(timeout):
            self._pending.pop(rid, None)
            return None, {"message": f"{method} timed out"}
        return box.get("result"), box.get("error")

    def _respond(self, rid, result):
        self._send({"id": rid, "result": result if result is not None else {}})

    # -- child lifecycle ------------------------------------------------

    def spawn(self, codex_bin, key, scratch, extra_argv=()):
        env = dict(os.environ)
        if key:
            env["OPENAI_API_KEY"] = key
            self._key = key  # retained for redaction only
        else:
            # Subscription path: the child must NOT see a key — if one leaked
            # into CCC's env, the realtime lane would silently bill it.
            env.pop("OPENAI_API_KEY", None)
            self._key = None
        self.proc = subprocess.Popen(
            [codex_bin, "app-server", *extra_argv],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=scratch,
            env=env,
            start_new_session=True,
        )
        threading.Thread(target=self._reader, name="voice-reader", daemon=True).start()
        threading.Thread(target=self._stderr_pump, name="voice-stderr", daemon=True).start()
        if not self._watchdog_started:
            self._watchdog_started = True
            threading.Thread(target=self._watchdog, name="voice-watchdog", daemon=True).start()

    def _stderr_pump(self):
        proc = self.proc
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = _redact(raw.decode("utf-8", "replace").rstrip(), self._key)
                if line:
                    self._stderr_tail.append(line[:500])
        except (OSError, ValueError):
            pass

    def _reader(self):
        proc = self.proc
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    self.emit("error", {"message": "malformed app-server line"})
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:
                    self._on_server_request(msg)
                elif "id" in msg:
                    slot = self._pending.pop(msg.get("id"), None)
                    if slot:
                        ev, box = slot
                        box["result"] = msg.get("result")
                        box["error"] = msg.get("error")
                        ev.set()
                elif "method" in msg:
                    self._on_notification(msg.get("method"), msg.get("params") or {})
        except (OSError, ValueError):
            pass
        finally:
            self._child_exited(proc)

    def _child_exited(self, proc):
        # Respawn (WebRTC -> websocket fallback, --enable retry) kills the
        # old child; its reader hitting EOF must not close the session that
        # just spawned a replacement. Only the CURRENT child's exit counts.
        if proc is not self.proc:
            return
        if self.state in ("closed", "error"):
            return
        tail = "; ".join(list(self._stderr_tail)[-3:])[:300]
        detail = tail or "app-server exited"
        self.error = detail
        self.reason = self.reason or "child_exit"
        self.emit("error", {"message": _redact(detail, self._key)})
        self.close(reason=self.reason or "child_exit")

    # -- notifications --------------------------------------------------

    def _on_notification(self, method, params):
        if not isinstance(params, dict):
            params = {}
        if method == "thread/realtime/started":
            self.realtime_version = params.get("version")
            self.set_state("listening")
            self.touch()
            return
        if method == "thread/realtime/sdp":
            self._sdp_answer = params.get("sdp")
            self._sdp_event.set()
            return
        if method == "thread/realtime/transcript/delta":
            role = str(params.get("role") or "assistant")
            delta = str(params.get("delta") or "")
            self._tr_open[role] = (self._tr_open.get(role) or "") + delta
            self.emit("transcript_delta", {"role": role, "delta": delta})
            self.touch()
            return
        if method == "thread/realtime/transcript/done":
            role = str(params.get("role") or "assistant")
            text = str(params.get("text") or self._tr_open.pop(role, "") or "")
            self._tr_open.pop(role, None)
            entry = {"role": role, "text": text, "ts": time.time()}
            self.transcript.append(entry)
            if len(self.transcript) > TRANSCRIPT_MAX_ITEMS:
                self.transcript = self.transcript[-TRANSCRIPT_MAX_ITEMS:]
            self.emit("transcript", entry)
            if role == "assistant":
                self.set_state("listening")
            self.touch()
            return
        if method == "thread/realtime/outputAudio/delta":
            audio = params.get("audio") or {}
            samples = audio.get("samplesPerChannel") or 0
            rate = audio.get("sampleRate") or 24000
            if rate:
                self.audio_ms += int(1000 * samples / rate)
            if self.transport == "websocket" and audio.get("data"):
                # Only the websocket path carries audio through CCC — forward
                # the chunk for the browser to play. Volatile: drop on replay.
                self.emit("audio", {
                    "data": audio["data"],
                    "sampleRate": audio.get("sampleRate") or 24000,
                    "numChannels": audio.get("numChannels") or 1,
                }, volatile=True)
            if self.state == "listening":
                self.set_state("speaking")
            self.touch()
            return
        if method == "thread/realtime/error":
            msg = _redact(str(params.get("message") or "realtime error")[:400], self._key)
            if self._sdp_answer is None:
                if self._connect_error is None:
                    self._connect_error = msg
                # Wake the SDP waiter now instead of burning the full timeout.
                self._sdp_event.set()
            self.emit("error", {"message": msg})
            self.touch()
            return
        if method == "thread/realtime/closed":
            self.close(reason=str(params.get("reason") or "closed"))
            return
        if method == "thread/realtime/itemAdded":
            self._on_realtime_item(params.get("item"))
            return
        if method in ("item/started", "item/completed"):
            self._on_thread_item(method, params)
            return
        if method == "thread/tokenUsage/updated":
            usage = ((params.get("tokenUsage") or {}).get("total") or {})
            self.codex_tokens = int(usage.get("totalTokens") or self.codex_tokens)
            return
        # Everything else (mcpServer/*, account/*, hook/*, remoteControl/*)
        # is irrelevant to the voice lane.

    def _on_realtime_item(self, item):
        if not isinstance(item, dict):
            return
        if item.get("type") == "dynamicToolCall":
            self.set_state("thinking", str(item.get("tool") or "tool"))
        elif "<realtime_delegation>" in json.dumps(item):
            self.set_state("thinking", "delegating")
        self.touch()

    def _on_thread_item(self, method, params):
        item = params.get("item") or {}
        itype = item.get("type") if isinstance(item, dict) else None
        if itype == "dynamicToolCall" and method == "item/started":
            self.set_state("thinking", str(item.get("tool") or "tool"))
        elif itype == "agentMessage" and method == "item/started":
            self.set_state("thinking", "composing")
        self.touch()

    # -- server -> client requests --------------------------------------

    def _on_server_request(self, msg):
        method = msg.get("method")
        params = msg.get("params") or {}
        rid = msg.get("id")
        if method == "item/tool/call":
            result = self._run_tool(params)
            self._respond(rid, result)
            return
        if method == "currentTime/read":
            self._respond(rid, {"currentTimeAt": int(time.time())})
            return
        if method in _APPROVAL_DENY_METHODS:
            # Voice sessions run approvalPolicy=never on a read-only sandbox;
            # an approval request reaching us means the model tried to leave
            # the lane. Deny everything.
            self._respond(rid, self._deny_result(method))
            return
        # Unknown request: empty object is the protocol's null-ish response.
        self._respond(rid, {})

    @staticmethod
    def _deny_result(method):
        if method == "mcpServer/elicitation/request":
            return {"action": "decline"}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        if method == "item/permissions/requestApproval":
            return {"permissions": None, "scope": "turn"}
        return {"decision": "denied"}

    # -- tools ----------------------------------------------------------

    def _run_tool(self, params):
        tool = str(params.get("tool") or "")
        args = params.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        self.emit("tool", {"tool": tool, "phase": "call"})
        try:
            if tool == "ccc_attention":
                return _fmt_tool_text(self._tool_attention(args))
            if tool == "ccc_session":
                return _fmt_tool_text(self._tool_session(args))
            if tool == "ccc_sessions":
                return _fmt_tool_text(self._tool_sessions(args))
            if tool == "ccc_models":
                return _fmt_tool_text(self._tool_models(args))
            if tool == "ccc_ticket":
                return _fmt_tool_text(self._tool_ticket(args))
            if tool == "ccc_queues":
                return _fmt_tool_text(self._tool_queues(args))
            if tool == "ccc_jobs":
                return _fmt_tool_text(self._tool_jobs(args))
            if tool == "ccc_triage_queue":
                return _fmt_tool_text(self._tool_triage_queue(args))
            if tool == "ccc_propose_action":
                return _fmt_tool_text(self._tool_propose(args))
            return _tool_err(f"unknown tool {tool!r}")
        except Exception as e:
            return _tool_err(_redact(str(e)[:200], self._key))

    def _tool_attention(self, args):
        try:
            limit = max(1, min(20, int(args.get("limit") or 10)))
        except (TypeError, ValueError):
            limit = 10
        now = time.time()
        feed = _core.compute_attention_feed(recent_only=True, limit=limit) or {}
        items = feed.get("items") or []
        out = []
        if items:
            out.append(f"{len(items)} live session(s) need attention (session feed, as of {_utc_stamp(now)}):")
            for it in items:
                repo = it.get("repo") or it.get("folder_label") or ""
                q = (it.get("question_text") or "").strip().replace("\n", " ")[:180]
                row = f"- [{it.get('kind')}] {it.get('where') or ''} {repo}".strip()
                if it.get("session_id"):
                    row += f" session={it['session_id']}"
                if it.get("mtime"):
                    row += f" last active {_age_text(now - float(it['mtime']))} ago"
                if q:
                    row += f" | {q}"
                out.append(row)
        else:
            out.append(f"No live session is waiting on the user (session feed, as of {_utc_stamp(now)}).")
        # The session feed never reads WatchTower, so ticket questions are a
        # separate source: report them here instead of letting "nothing needs
        # input" contradict ccc_queues.
        try:
            tickets = _core._ux_fixes_list_items_cached(None, None) or []
        except Exception:
            tickets = None
        if tickets is None:
            out.append("WatchTower ticket records unavailable, so queue needs-input state is unknown.")
        else:
            explicit, claimed = _split_needs_input(tickets)
            synced = _tickets_synced_at()
            src = f"WatchTower ticket records synced {_asof(synced, now)}" if synced else "WatchTower ticket records, sync time unknown"
            if explicit or claimed:
                out.append(f"Recorded needs-input tickets ({src}); these are recorded reports, "
                           "not verified current:")
                explicit.sort(key=lambda i: _iso_epoch(i.get("blocked_at") or i.get("updated_at")) or 0)
                for it in explicit[:limit]:
                    out.append(f"- {str(it.get('ref')).upper()} parked on the user for "
                               f"{_ticket_age(it, now)}: {self._compact_text(it.get('block_question') or it.get('question'), 140) or 'question not recorded'}")
                if len(explicit) > limit:
                    out.append(f"- ... {len(explicit) - limit} more parked ticket(s); use ccc_queues")
                if claimed:
                    out.append(f"- {len(claimed)} more ticket(s) carry a needs-input flag while a worker "
                               f"holds them (may be answered already): "
                               + ", ".join(str(i.get('ref')).upper() for i in claimed[:8]))
            else:
                out.append(f"No tickets are recorded as needing input ({src}).")
        return "\n".join(out)[:4000]

    def _tool_session(self, args):
        sid = str(args.get("session_id") or "").strip()
        if not sid:
            return "error: session_id is required"
        detail, _status = _core.compute_session_detail(sid)
        if not isinstance(detail, dict) or not detail.get("ok", True):
            # compute_session_detail only reads Claude/Devin transcripts, but the
            # attention feed spans every engine (kimi, codex, ...): fall back to
            # the same archive rows the feed is built from.
            row = self._archive_row(sid)
            if not row:
                # Not a full id: try an id prefix (voice users dictate the first
                # 8 chars), then a display name.
                hits = self._match_prefix(sid) or self._match_title(sid)
                if len(hits) > 1:
                    return "multiple matches, pick one:\n" + "\n".join(
                        f"- {r.get('title')} session={r.get('session_id')}" for r in hits[:8])
                if not hits:
                    return f"session {sid} not found"
                return self._tool_session({"session_id": hits[0].get("session_id")})
            parts = [f"session {row.get('session_id') or sid}:"]
            title = row.get("title") or row.get("display_name")
            if title:
                parts.append(f"title={title}")
            for key in ("engine", "folder_label"):
                if row.get(key):
                    parts.append(f"{key}={row[key]}")
            parts.append("live" if row.get("is_live") else "not live")
            parts.append(self._liveness_line(row.get("session_id") or sid, row.get("modified") or row.get("mtime")))
            if row.get("last_assistant_text"):
                parts.append(f"last: {str(row['last_assistant_text'])[:300]}")
            return "\n".join(parts)[:3000]
        parts = [f"session {sid}:"]
        state = detail.get("session_state") or {}
        for key in ("title", "engine", "repo_label", "folder_label"):
            if detail.get(key):
                parts.append(f"{key}={detail[key]}")
        if state.get("summary"):
            parts.append(f"summary: {str(state['summary'])[:300]}")
        if state.get("next_step_user"):
            parts.append(f"awaits user: {str(state['next_step_user'])[:300]}")
        row = self._archive_row(sid) or {}
        parts.append(self._liveness_line(sid, row.get("modified") or row.get("mtime") or detail.get("modified")))
        if detail.get("last_assistant_text"):
            parts.append(f"last: {str(detail['last_assistant_text'])[:300]}")
        return "\n".join(parts)[:3000]

    @staticmethod
    def _archive_rows():
        convs, _cached = _core._archive_all_rows_cached({
            "include_prs": False,
            "resolve_pr_states": False,
            "resolve_effective": False,
            "resolve_worktree_dirty": False,
        })
        return convs or []

    def _archive_row(self, sid):
        """Archive row for a session id, tolerating a bare/`session_`-prefixed id."""
        want = {sid, sid[len("session_"):] if sid.startswith("session_") else "session_" + sid}
        for row in self._archive_rows():
            if row.get("session_id") in want:
                return row
        return None

    def _match_prefix(self, query):
        """Archive rows whose session id starts with query (>=6 chars, spaces/dashes ignored)."""
        q = query.lower().replace(" ", "")
        if len(q) < 6 or not all(c in "0123456789abcdef-_session" for c in q):
            return []
        bare = q[len("session_"):] if q.startswith("session_") else q
        rows = []
        for r in self._archive_rows():
            sid = str(r.get("session_id") or "").lower()
            if sid.startswith(bare) or sid.startswith(q) or sid.startswith("session_" + bare):
                rows.append(r)
        rows.sort(key=lambda r: (not r.get("is_live"), -(r.get("modified") or r.get("mtime") or 0)))
        return rows

    def _match_title(self, query):
        """Archive rows whose title contains every word of query (live first)."""
        words = query.lower().split()
        rows = [r for r in self._archive_rows()
                if all(w in (r.get("title") or "").lower() for w in words)]
        rows.sort(key=lambda r: (not r.get("is_live"), -(r.get("modified") or r.get("mtime") or 0)))
        return rows

    def _liveness_line(self, sid, modified=None):
        """Proof line: pid-verified process, registry status, transcript activity age."""
        now = time.time()
        meta = self._registry_sessions().get(sid)
        bits = []
        if meta:
            bits.append(f"process pid {meta.get('pid')} verified alive at {_utc_stamp(now)}"
                        + (f", Claude reports {meta['status']}" if meta.get("status") else ""))
        else:
            bits.append("no running Claude process found for this id by pid check "
                        f"at {_utc_stamp(now)} (other engines use their own signal)")
        try:
            if modified and float(modified) > 0:
                bits.append(f"transcript last written {_asof(float(modified), now)}")
        except (TypeError, ValueError):
            pass
        return "proof: " + "; ".join(bits)

    @staticmethod
    def _registry_sessions():
        """{session_id: registry entry} for Claude processes verified alive by pid."""
        try:
            return dict(_core._load_session_registry() or {})
        except Exception:
            return {}

    def _tool_sessions(self, args):
        try:
            limit = max(1, min(30, int(args.get("limit") or 15)))
        except (TypeError, ValueError):
            limit = 15
        query = str(args.get("query") or "").strip()
        live_only = bool(args.get("live_only", True))
        now = time.time()
        all_rows = self._archive_rows()
        registry = self._registry_sessions()
        if query:
            rows = self._match_title(query)
        elif live_only:
            rows = [r for r in all_rows if r.get("is_live")]
        else:
            rows = list(all_rows)
        # The archive can lag a process by a cache window, so union in every
        # session whose pid is verified alive right now.
        if live_only and not query:
            have = {r.get("session_id") for r in rows}
            archived = {r.get("session_id"): r for r in all_rows}
            for sid, meta in registry.items():
                if sid in have:
                    continue
                if sid in archived:
                    # Archive row exists but its cached liveness lags the process.
                    rows.append(dict(archived[sid], is_live=True,
                                     state=_REGISTRY_STATE.get(meta.get("status"), archived[sid].get("state"))))
                    continue
                try:
                    updated = float(meta.get("updatedAt") or 0) / 1000.0
                except (TypeError, ValueError):
                    updated = 0
                rows.append({"session_id": sid, "engine": "claude", "is_live": True,
                             "title": meta.get("name") or "", "folder_label": os.path.basename(str(meta.get("cwd") or "")),
                             "modified": updated,
                             "state": _REGISTRY_STATE.get(meta.get("status"), meta.get("status"))})
        rows.sort(key=lambda r: -(r.get("modified") or r.get("mtime") or 0))
        if not rows:
            return f"No {'open ' if live_only else ''}sessions (checked {_utc_stamp(now)})."
        by_engine = {}
        for r in rows:
            eng = r.get("engine") or "claude"
            by_engine[eng] = by_engine.get(eng, 0) + 1
        header = (f"{min(len(rows), limit)} of {len(rows)} session(s) as of {_utc_stamp(now)} "
                  f"[{', '.join(f'{k}={v}' for k, v in sorted(by_engine.items()))}]; "
                  f"{len(registry)} Claude process(es) verified alive by pid")
        if len(rows) > limit:
            header += f"; {len(rows) - limit} more not shown (raise limit)"
        out = [header + ":"]
        for r in rows[:limit]:
            sid = r.get("session_id")
            line = (f"- {r.get('title') or r.get('folder_label') or ''} "
                    f"engine={r.get('engine') or 'claude'} session={sid}")
            if r.get("state"):
                line += f" state={r['state']}"
            ts = r.get("modified") or r.get("mtime")
            if ts:
                line += f" last activity {_age_text(now - float(ts))} ago"
            if sid in registry:
                line += f" proof=process pid {registry[sid].get('pid')}"
            elif r.get("is_live"):
                line += " proof=engine/hook signal, no pid check"
            out.append(line)
        return "\n".join(out)[:4000]

    def _tool_models(self, args):
        """List current catalog IDs suitable for a voice-selected route."""
        engine = str(args.get("engine") or "").strip().lower()
        query = self._compact_text(args.get("query"), 120).lower()
        if engine and not re.fullmatch(r"[a-z0-9_-]{1,32}", engine):
            return "error: engine has unexpected characters"
        try:
            payload = _core._build_engine_model_catalog(force_refresh=False) or {}
        except Exception:
            payload = {}
        catalog = payload.get("catalog") if isinstance(payload, dict) else None
        if not isinstance(catalog, dict):
            return "Model catalog unavailable."
        rows = []
        for name in sorted(catalog):
            if engine and name != engine:
                continue
            bucket = catalog.get(name) or {}
            for item in bucket.get("models") or []:
                if not isinstance(item, dict) or item.get("available") is False or item.get("policy_blocked"):
                    continue
                model_id = self._compact_text(item.get("id"), 100)
                label = self._compact_text(item.get("label") or model_id, 120)
                haystack = (model_id + " " + label).lower()
                if not model_id or (query and query not in haystack):
                    continue
                efforts = item.get("reasoning_efforts")
                efforts = [str(value) for value in efforts] if isinstance(efforts, (list, tuple)) else []
                line = f"{name}: {model_id} ({label})"
                if efforts:
                    line += "; efforts: " + ", ".join(efforts[:8])
                rows.append(line)
        if not rows:
            return "No approved available models match."
        return ("Approved available models:\n" + "\n".join(rows[:30]))[:4000]

    @staticmethod
    def _compact_text(value, limit):
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[:limit - 1] + "…"

    def _tool_ticket(self, args):
        """Read one WatchTower ticket, falling back to CCC's explicitly stale cache."""
        ref = str(args.get("ref") or "").strip().upper()
        if not _TICKET_REF_RE.fullmatch(ref):
            return "error: ref must look like QUEUE-123"
        item = None
        stale = False
        try:
            item = _core._q.get(ref)
        except Exception:
            item = None
        if not isinstance(item, dict):
            try:
                cached = _core._ux_fixes_list_items_cached(None, None) or []
            except Exception:
                cached = []
            item = next((row for row in cached if isinstance(row, dict)
                         and str(row.get("ref") or "").upper() == ref), None)
            stale = isinstance(item, dict)
        if not isinstance(item, dict):
            return f"ticket {ref} not found"
        now = time.time()
        out = [f"ticket {ref}" + (" (stale cached fallback)" if stale else "") + ":"]
        if stale:
            out.append(f"source: CCC ticket cache synced {_asof(_tickets_synced_at(), now)}; live read failed")
        else:
            out.append(f"source: live WatchTower read at {_utc_stamp(now)}")
        updated = _iso_epoch(item.get("updated_at"))
        if updated:
            out.append(f"ticket last updated {_age_text(now - updated)} ago")
        title = self._compact_text(item.get("title"), 260)
        if title:
            out.append(f"title: {title}")
        status = self._compact_text(item.get("status"), 80)
        if status:
            out.append(f"status: {status}")
        body = self._compact_text(item.get("body") or item.get("text") or item.get("description"), 1200)
        if body:
            out.append(f"body: {body}")
        if item.get("needs_input") or str(item.get("status") or "").lower() == "blocked":
            question = self._compact_text(
                item.get("block_question") or item.get("question")
                or item.get("question_text") or item.get("needs_input_question"), 700)
            blocked = _iso_epoch(item.get("blocked_at"))
            when = (f" [recorded {_age_text(now - blocked)} ago, not verified still open]" if blocked
                    else " [recorded, not verified still open]")
            out.append("needs input: " + (question or "question not recorded") + when)
        comments = item.get("comments")
        if isinstance(comments, list):
            recent = []
            for comment in comments[-4:]:
                if isinstance(comment, dict):
                    author = self._compact_text(comment.get("author") or comment.get("by") or "comment", 80)
                    text = self._compact_text(comment.get("text") or comment.get("body") or comment.get("content"), 350)
                else:
                    author, text = "comment", self._compact_text(comment, 350)
                if text:
                    recent.append(f"{author}: {text}")
            if recent:
                out.append("recent comments: " + " | ".join(recent))
        return "\n".join(out)[:4000]

    def _tool_queues(self, args):
        requested = str(args.get("queue") or "").strip()
        if requested and not _QUEUE_NAME_RE.fullmatch(requested):
            return "error: queue must use letters, numbers, _ or -"
        requested = requested.upper()
        try:
            health = _core.build_ux_fixes_health_payload() or {}
        except Exception:
            health = {}
        health_rows = health.get("queues") if isinstance(health, dict) else []
        health_rows = health_rows if isinstance(health_rows, list) else []
        by_queue = {str(row.get("queue") or "").upper(): row for row in health_rows
                    if isinstance(row, dict) and str(row.get("queue") or "").strip()}
        try:
            config = _core._wt_read_config() or {}
        except Exception:
            config = {}
        config_names = {str(name).strip().upper() for name in config if str(name).strip()}
        try:
            items = _core._ux_fixes_list_items_cached(None, None) or []
        except Exception:
            items = []
        item_queues = {str(item.get("project") or "").upper() for item in items
                       if isinstance(item, dict) and str(item.get("project") or "").strip()}
        names = sorted(set(by_queue) | config_names | item_queues)
        if requested:
            if requested not in names:
                return f"error: queue {requested} is not configured or cached"
            names = [requested]
        if not names:
            return "WatchTower queue data unavailable."
        now = time.time()
        health_ts = None
        try:
            health_ts = (_core._ux_fixes_health_snapshot or {}).get("ts")
        except Exception:
            pass
        synced = _tickets_synced_at()
        out = [f"Sources: queue health snapshot {_asof(health_ts, now)}; WatchTower ticket records "
               f"synced {_asof(synced, now)}. Needs-input questions are recorded reports, "
               "not verified current state; worker/stuck figures are the health snapshot."]
        for name in names[:20]:
            row = by_queue.get(name) or {}
            queue_items = [item for item in items if isinstance(item, dict)
                           and str(item.get("project") or "").upper() == name]
            depth = row.get("depth")
            if depth is None:
                depth = sum(1 for item in queue_items if item.get("status") != "closed")
            explicit, claimed = _split_needs_input(queue_items)
            needs = [str(item.get("ref") or "").upper() for item in queue_items
                     if item.get("needs_input") or item.get("status") == "blocked"]
            needs = [ref for ref in needs if _TICKET_REF_RE.fullmatch(ref)]
            line = f"{name}: {int(depth or 0)} open"
            if row.get("workers") is not None:
                line += f", {int(row.get('workers') or 0)} worker(s)"
            if row.get("last_activity_seconds") is not None:
                line += f", last activity {_age_text(row['last_activity_seconds'])} ago"
            if row.get("stuck"):
                line += ", stuck"
            line += f"; {len(needs)} need input"
            if needs:
                line += " (" + ", ".join(needs[:12]) + ")"
            out.append(line)
            for it in sorted(explicit, key=lambda i: _iso_epoch(i.get("blocked_at") or i.get("updated_at")) or 0)[:6]:
                kind = f" [{it['block_kind']}]" if it.get("block_kind") else ""
                out.append(f"  - {str(it.get('ref')).upper()} {str(it.get('status')).lower()}{kind} for "
                           f"{_ticket_age(it, now)} (recorded): "
                           f"{self._compact_text(it.get('block_question') or it.get('question'), 140) or 'question not recorded'}")
            if claimed:
                out.append(f"  - {len(claimed)} flagged while a worker holds them (may be answered): "
                           + ", ".join(str(i.get('ref')).upper() for i in claimed[:6]))
        return "\n".join(out)[:4000]

    _JOB_PROBLEM = frozenset({"failed", "stale"})

    @staticmethod
    def _job_verdict(job, now):
        """One factual sentence on why a job is or is not out, from its own fields."""
        status = job.get("status")
        nxt = _iso_epoch(job.get("next_run_at"))
        last = _iso_epoch(job.get("last_run_at"))
        nxt_s = f"{_utc_stamp(nxt)} (in {_age_text(nxt - now)})" if nxt and nxt > now else None
        if status == "disabled":
            return "timer is disabled, so it will not run until someone enables it"
        if status == "running":
            return "running right now"
        if status == "failed":
            code = job.get("exit_code")
            tail = f"; next attempt {nxt_s}" if nxt_s else ""
            return f"last run FAILED{f' with exit code {code}' if code is not None else ''}{tail}"
        if status == "stale":
            ago = f"last ran {_age_text(now - last)} ago" if last else "no recorded run"
            return f"no upcoming run is scheduled ({ago}); the timer may be stopped or exhausted"
        if status == "unknown":
            return ("cron keeps no run history, so whether the last run succeeded cannot be verified"
                    + (f"; next fire {nxt_s}" if nxt_s else ""))
        if nxt_s and last:
            return f"on schedule: last ran {_age_text(now - last)} ago, next run {nxt_s}"
        if nxt_s:
            return f"scheduled, no run recorded yet; next run {nxt_s}"
        return "healthy per systemd but no next run time is known"

    def _tool_jobs(self, args):
        """Scheduled jobs (systemd system+user timers and cron) with outcome and verdict."""
        query = self._compact_text(args.get("query"), 80).lower()
        problems_only = bool(args.get("problems_only"))
        try:
            limit = max(1, min(12, int(args.get("limit") or 6)))
        except (TypeError, ValueError):
            limit = 6
        from ccc_server import scheduled_jobs_feed as feed
        now = time.time()
        payload = feed.get_jobs_feed(wait_s=22) or {}
        host = (payload.get("hosts") or {}).get("hermes") or {}
        if host.get("status") == "loading":
            return "Scheduled-job data is still loading (first collection takes ~15s); ask again shortly."
        if host.get("status") == "offline" and not payload.get("jobs"):
            return f"Scheduled-job host is unreachable: {host.get('error') or 'unknown error'}"
        jobs = [j for j in payload.get("jobs") or [] if isinstance(j, dict)]
        collected = _iso_epoch(host.get("collected_at"))
        src = f"job feed (systemd system+user timers and crontab on {payload.get('local_host') or 'host'}), collected {_asof(collected, now)}"
        if host.get("stale_data"):
            src += f"; HOST OFFLINE, this is old data: {host.get('error') or ''}"
        words = query.split()
        if words:
            jobs = [j for j in jobs if all(w in " ".join(str(j.get(k) or "") for k in
                                                       ("name", "description", "project", "schedule", "id")).lower()
                                           for w in words)]
        if problems_only:
            jobs = [j for j in jobs if j.get("status") in self._JOB_PROBLEM]
        counts = {}
        for j in (payload.get("jobs") or []):
            counts[j.get("status")] = counts.get(j.get("status"), 0) + 1
        header = [f"Source: {src}.",
                  "All jobs: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items(), key=lambda kv: str(kv[0])))]
        if not jobs:
            what = f"matching '{query}'" if query else "with problems"
            return "\n".join(header + [f"No scheduled jobs {what}. Try a shorter query (the project or script name)."])[:4000]
        order = {"failed": 0, "stale": 1, "running": 2, "unknown": 3, "ok": 4, "disabled": 5}
        jobs.sort(key=lambda j: (order.get(j.get("status"), 9), j.get("name") or ""))
        out = header + [f"{min(len(jobs), limit)} of {len(jobs)} job(s):"]
        detailed = bool(words) or problems_only
        for j in jobs[:limit]:
            kind = j.get("scope") or j.get("manager") or "job"
            out.append(f"- {j.get('name')} [{kind}] {j.get('schedule') or ''} status={j.get('status')}")
            out.append(f"  verdict: {self._job_verdict(j, now)}")
            if not detailed:
                continue
            if j.get("description"):
                out.append("  what: " + self._compact_text(j["description"], 160))
            last = _iso_epoch(j.get("last_run_at"))
            if last:
                dur = f", took {j['last_duration_s']}s" if j.get("last_duration_s") is not None else ""
                out.append(f"  last run: {_asof(last, now)}{dur}, exit {j.get('exit_code')}")
            if j.get("outcome"):
                out.append("  last output: " + self._compact_text(j["outcome"], 220))
            if j.get("tickets"):
                out.append("  refs: " + ", ".join(
                    (f"{t.get('ref')} {t.get('url') or ''}".strip() if isinstance(t, dict) else str(t))
                    for t in j["tickets"][:4]))
            for ev in (j.get("events") or [])[-3:]:
                at = _iso_epoch(ev.get("at"))
                out.append(f"  event {_age_text(now - at) if at else '?'} ago: " + self._compact_text(ev.get("text"), 160))
        if len(jobs) > limit:
            out.append(f"... {len(jobs) - limit} more; narrow with query or problems_only")
        return "\n".join(out)[:4000]

    def _tool_triage_queue(self, args):
        """Create a confirm-only senior triage session proposal for one queue."""
        queue = str(args.get("queue") or "").strip().upper()
        if not _QUEUE_NAME_RE.fullmatch(queue):
            return "error: queue must use letters, numbers, _ or -"
        repo_path, error = _core._wt_queue_attend_resolve_repo(queue)
        if error:
            return f"error: {self._compact_text(error, 300)}"
        explicit_engine = str(args.get("engine") or "").strip().lower()
        explicit_model = str(args.get("model") or "").strip()
        explicit_effort = str(args.get("effort") or "").strip().lower()
        if bool(explicit_engine) != bool(explicit_model):
            return "error: explicit engine and model must be provided together"
        profile = str(args.get("profile") or "deep").strip().lower()
        if profile not in ("deep", "default"):
            return "error: profile must be deep or default"
        if explicit_engine:
            engine, model, effort = explicit_engine, explicit_model, explicit_effort
        else:
            try:
                defaults = _core._load_spawn_defaults() or {}
            except Exception:
                defaults = {}
            if profile == "deep":
                routes = ((defaults.get("model_profiles") or {}).get("deep") or {}).get("models") or []
                route = next((row for row in routes if isinstance(row, dict)
                              and str(row.get("engine") or "").strip()
                              and str(row.get("model") or "").strip()), None)
                if route is None:
                    return "error: configure a Deep profile in CCC Settings before proposing deep queue triage"
                engine = str(route.get("engine") or "").strip().lower()
                model = str(route.get("model") or "").strip()
                effort = str(route.get("effort") or "").strip().lower()
            else:
                engine = str(defaults.get("worker_engine") or "").strip().lower()
                model = str(defaults.get("worker_model") or "").strip()
                effort = str(defaults.get("worker_reasoning_effort") or "").strip().lower()
        if engine and not re.fullmatch(r"[a-z0-9_-]{1,32}", engine):
            return "error: engine has unexpected characters"
        if model and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:\[\]-]{0,63}", model):
            return "error: model has unexpected characters"
        if effort and not re.fullmatch(r"[a-z]+", effort):
            return "error: effort has unexpected characters"
        prompt = (
            f"Act as the senior triage lead for WatchTower queue {queue}. Inspect its "
            "tickets and blockers, explain every recorded needs input question using the "
            "ticket evidence, inspect stale claims safely, prioritize the next work, "
            "and report a concise triage summary. No blindly reclaiming live workers. "
            "Do not implement ticket fixes or mutate ticket state unless the owner "
            "explicitly asks after reviewing your report."
        )
        params = {"cwd": str(repo_path), "prompt": prompt, "name": f"Triage {queue}"}
        if engine:
            params["engine"] = engine
        if model:
            params["model"] = model
        if effort:
            params["effort"] = effort
        return self._tool_propose({
            "kind": "spawn_session", "params": params,
            "reason": f"Triage WatchTower queue {queue} with a senior session.",
        })

    def _tool_propose(self, args):
        kind = str(args.get("kind") or "").strip()
        params = args.get("params") if isinstance(args.get("params"), dict) else {}
        reason = str(args.get("reason") or "")[:300]
        from ccc_server import assistant_actions as _aa
        try:
            res = _aa.store().propose(kind, params, reason)
        except _aa.ActionError as e:
            return f"error: {e}"
        aid = res.get("action_id")
        item = _aa.store().get(aid) or {}
        pub = _aa.store().public(item, with_token=True)
        self.pending_actions[aid] = pub
        self.emit("action", pub)
        return (
            f"Proposed action {aid}: {res.get('effect')}. It is now a card in the "
            "CCC dashboard. Tell the user to click Confirm (or Dismiss); the "
            "action will not run until they do. You will be told the outcome."
        )

    # -- action outcome (called by server.py after confirm/dismiss) ------

    def notify_action_result(self, action):
        if not isinstance(action, dict):
            return
        aid = action.get("id")
        # Drop the confirm token from what we retain/forward: the token only
        # ever travels to the browser in the original "action" event.
        clean = {k: v for k, v in action.items() if k != "confirm_token"}
        if aid in self.pending_actions:
            self.pending_actions.pop(aid, None)
        self.emit("action", clean)
        if not self.thread_id or self.state not in ("listening", "thinking", "speaking"):
            return
        status = clean.get("status")
        effect = clean.get("effect") or "the action"
        if status == "done":
            text = f"The user confirmed and it is done: {effect}."
        elif status == "failed":
            err = ((clean.get("result") or {}).get("error") or "it failed")[:160]
            text = f"The user confirmed, but it failed: {err}."
        elif status == "dismissed":
            text = f"The user declined: {effect}."
        else:
            return
        self.call("thread/realtime/appendSpeech",
                  {"threadId": self.thread_id, "text": text}, timeout=15)

    # -- watchdog / timeouts ---------------------------------------------

    def _watchdog(self):
        cfg = voice_config_load()
        idle = float(cfg["idle_seconds"])
        max_len = float(cfg["max_minutes"]) * 60.0
        while not self._watchdog_stop.wait(1.0):
            if self.state in ("closed", "error"):
                return
            now = time.monotonic()
            if now - self.last_activity > idle:
                self.stop(reason="idle_timeout")
                return
            if time.time() - self.started_at > max_len:
                self.stop(reason="max_duration")
                return
            if (self.last_heartbeat is not None
                    and now - self.last_heartbeat > HEARTBEAT_TIMEOUT_SECONDS):
                self.stop(reason="heartbeat_lost")
                return

    def heartbeat(self):
        # Deliberately NOT touch(): heartbeats prove the browser is alive,
        # they are not voice activity — otherwise an idle timer would never
        # fire while the tab is open.
        self.last_heartbeat = time.monotonic()

    def welcome(self):
        """Speak once, after the browser confirms its audio transport is ready."""
        with self.cond:
            if self._welcome_sent or not self.thread_id or self.state in ("stopping", "closed", "error"):
                return
            self._welcome_sent = True
        _, error = self.call("thread/realtime/appendSpeech", {
            "threadId": self.thread_id,
            "text": "Hi, what's up?",
        }, timeout=15)
        if error:
            # A later connected heartbeat can retry a rejected request.
            with self.cond:
                self._welcome_sent = False

    # -- close / teardown -------------------------------------------------

    def stop(self, reason="user"):
        """Graceful stop: realtime/stop, then kill the child. Idempotent,
        and synchronous: waits for whichever thread is running close() —
        the app-server's `closed` notification routinely wins the race —
        so callers only return once bookkeeping is finished."""
        if self._closed_once:
            self._close_done.wait(timeout=8)
            return
        self.reason = self.reason or reason
        self.set_state("stopping", reason)
        if self.thread_id and self.proc and self.proc.poll() is None:
            try:
                self.call("thread/realtime/stop", {"threadId": self.thread_id}, timeout=8)
            except Exception:
                pass
        self._kill_proc()
        self.close(reason=self.reason or reason)
        self._close_done.wait(timeout=8)

    def _kill_proc(self):
        proc = self.proc
        if proc is None:
            return
        # Clear first so the dead child's reader-thread exit cannot race a
        # respawn into closing the whole session (_child_exited identity check).
        self.proc = None
        try:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        proc.kill()
                    proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    def close(self, reason="closed"):
        with self._close_lock:
            if self._closed_once:
                return
            self._closed_once = True
        self._key = None  # redaction handle ends with the child
        self._watchdog_stop.set()
        self.closed_at = time.time()
        self.reason = self.reason or reason
        try:
            self._kill_proc()
            self._finish_bookkeeping()
            if self.state not in ("error",):
                self.set_state("closed", self.reason)
            _manager_clear(self)
        finally:
            self._close_done.set()

    def _finish_bookkeeping(self):
        dur = self.closed_at - self.started_at
        if self._tr_open:
            for role, text in self._tr_open.items():
                if text:
                    self.transcript.append({"role": role, "text": text, "ts": time.time()})
            self._tr_open = {}
        # Optional transcript persistence (default off — privacy).
        cfg = voice_config_load()
        if cfg.get("save_transcripts") and self.transcript:
            try:
                VOICE_TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
                path = VOICE_TRANSCRIPT_DIR / f"{self.id}.jsonl"
                with open(path, "w", encoding="utf-8") as fh:
                    for entry in self.transcript:
                        fh.write(json.dumps(entry) + "\n")
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            except OSError:
                pass
        # Usage ledger: duration + audio for both paths. Dollar cost only
        # applies to the api_key fallback — the subscription path logs
        # duration with no provider charge attached.
        try:
            _core.byok_record_usage(
                session_id=self.id,
                engine="codex-realtime",
                model="openai/realtime",
                key_profile=self.profile or None,
                tokens_in=0,
                tokens_out=0,
                cost_usd=None,
                provider="openai" if self.billing == "api_key" else None,
                extra={
                    "duration_s": round(dur, 1),
                    "audio_ms": self.audio_ms,
                    "codex_tokens": self.codex_tokens,
                    "billing": self.billing,
                    "transport": self.transport,
                    "reason": self.reason,
                },
            )
        except Exception:
            pass
        self.emit("closed", {
            "reason": self.reason,
            "duration_s": round(dur, 1),
            "audio_ms": self.audio_ms,
            "codex_tokens": self.codex_tokens,
        })

    def public(self):
        return {
            "id": self.id,
            "state": self.state,
            "state_detail": self.state_detail,
            "voice": self.voice,
            "profile": self.profile,
            "started_at": self.started_at,
            "elapsed_s": round((self.closed_at or time.time()) - self.started_at, 1),
            "audio_ms": self.audio_ms,
            "codex_tokens": self.codex_tokens,
            "error": self.error,
            "reason": self.reason,
            "realtime_version": self.realtime_version,
            "transport": self.transport,
            "billing": self.billing,
            "pending_actions": [dict(a) for a in self.pending_actions.values()],
        }


# ---------------------------------------------------------------------------
# Manager: exactly one live session
# ---------------------------------------------------------------------------

_MGR_LOCK = threading.Lock()
_SESSION = None
_LAST_SESSION = None


def voice_status():
    with _MGR_LOCK:
        sess = _SESSION
        last = _LAST_SESSION
    cfg = voice_config_load()
    return {
        "ok": True,
        "active": sess is not None and sess.state not in ("closed", "error"),
        "session": sess.public() if sess else None,
        "last_session": last.public() if last and not sess else None,
        "config": cfg,
        "openai_profiles": _openai_profiles(),
    }


def _session_or_none():
    with _MGR_LOCK:
        return _SESSION


def _manager_clear(sess):
    """Move a self-closed session out of the live slot (idle/max/heartbeat/
    child-exit paths close without going through voice_stop)."""
    global _SESSION, _LAST_SESSION
    with _MGR_LOCK:
        if _SESSION is sess:
            _LAST_SESSION = sess
            _SESSION = None


def _session_by_id(session_id):
    """Live session first, then the most recently closed one (SSE readers
    still need the closed session's event buffer after it leaves _SESSION)."""
    with _MGR_LOCK:
        for cand in (_SESSION, _LAST_SESSION):
            if cand is not None and cand.id == session_id:
                return cand
    return None


def voice_heartbeat(session_id, audio_ms=None, connected=False):
    if not isinstance(connected, bool):
        return {"ok": False, "error": "connected must be a boolean", "code": "voice_bad_request"}, 400
    sess = _session_by_id(session_id)
    if not sess or sess.state in ("closed", "error"):
        return {"ok": False, "error": "no such voice session", "code": "voice_no_session"}, 404
    sess.heartbeat()
    # WebRTC audio never transits CCC, so the only speech-duration signal on
    # the subscription path is the oai-events usage the browser relays here.
    if audio_ms is not None:
        try:
            sess.audio_ms = max(sess.audio_ms, min(int(audio_ms), 24 * 3600 * 1000))
        except (TypeError, ValueError):
            pass
    if connected:
        sess.welcome()
    return {"ok": True}, 200


def voice_audio_append(session_id, data, sample_rate, num_channels):
    """Mic chunk for the websocket-transport fallback. The browser posts
    base64 PCM16; CCC forwards it as thread/realtime/appendAudio."""
    sess = _session_or_none()
    if not sess or sess.id != session_id or sess.state in ("closed", "error"):
        return {"ok": False, "error": "no such voice session",
                "code": "voice_no_session"}, 404
    if sess.transport != "websocket":
        return {"ok": False, "error": "session is not on the websocket transport",
                "code": "voice_bad_request"}, 400
    if not isinstance(data, str) or not data or len(data) > 1024 * 1024:
        return {"ok": False, "error": "invalid audio data",
                "code": "voice_bad_request"}, 400
    try:
        rate = int(sample_rate) if sample_rate else 24000
        chans = int(num_channels) if num_channels else 1
    except (TypeError, ValueError):
        rate, chans = 24000, 1
    try:
        import base64
        samples = len(base64.b64decode(data)) // 2 // max(1, chans)
    except Exception:
        samples = 0
    if not sess.thread_id:
        return {"ok": False, "error": "realtime not started",
                "code": "voice_no_session"}, 404
    _result, err = sess.call("thread/realtime/appendAudio", {
        "threadId": sess.thread_id,
        "audio": {
            "data": data,
            "sampleRate": rate,
            "numChannels": chans,
            "samplesPerChannel": samples or None,
        },
    }, timeout=10)
    if err:
        return {"ok": False, "error": _redact(str(err)[:200], sess._key),
                "code": "voice_audio_failed"}, 502
    sess.touch()  # user is speaking — counts as activity for the idle timer
    return {"ok": True}, 200


class _VoiceStartError(Exception):
    def __init__(self, code, message, status=502):
        super().__init__(message)
        self.code = code
        self.status = status


class _MethodNotFound(Exception):
    pass


def voice_start(params):
    """Validate, spawn, negotiate. Returns (response_dict, http_status)."""
    global _SESSION
    params = params if isinstance(params, dict) else {}
    transport_req = str(params.get("transport") or "auto").strip().lower()
    if transport_req not in ("auto", "webrtc", "websocket"):
        return {"ok": False, "code": "voice_bad_request",
                "error": "transport must be auto, webrtc, or websocket"}, 400
    sdp_offer = params.get("sdp_offer")
    if transport_req != "websocket":
        if not isinstance(sdp_offer, str) or "v=0" not in sdp_offer[:200]:
            return {"ok": False, "code": "voice_bad_request",
                    "error": "sdp_offer (a WebRTC SDP offer string) is required"}, 400
        if len(sdp_offer) > 65536:
            return {"ok": False, "code": "voice_bad_request", "error": "sdp_offer too large"}, 400

    cfg = voice_config_load()
    voice = str(params.get("voice") or cfg["voice"] or DEFAULT_VOICE).strip().lower()
    if voice not in _ALL_VOICES:
        # A live catalog may know voices the fallback doesn't; keep it loose:
        # reject only obviously malformed values.
        if not voice or len(voice) > 40 or not voice.replace("-", "").isalnum():
            return {"ok": False, "code": "voice_bad_request",
                    "error": "unknown voice"}, 400
    # version:"v3" accepts only the v1 voice names; reject a stale v2 choice
    # here instead of letting the app-server bounce the realtime start.
    with _VOICES_LOCK:
        live_v1 = list((_VOICES_LIVE or {}).get("v1") or [])
    valid = live_v1 or list(VOICES_V1)
    if valid and voice not in valid:
        return {"ok": False, "code": "voice_bad_request",
                "error": (f"voice {voice!r} is not available on this realtime "
                          f"version; pick one of: {', '.join(valid[:12])}")}, 400
    profile = str(params.get("profile") or cfg["profile"] or "").strip()

    codex_info = _core._resolve_codex_bin()
    if not codex_info.get("available") or not codex_info.get("bin"):
        return {"ok": False, "code": "voice_no_codex",
                "error": codex_info.get("reason") or "Codex CLI not found"}, 503

    # The key is only fetched when a websocket (paid) attempt can happen —
    # the subscription WebRTC path never touches BYOK at all.
    want_paid = transport_req == "websocket" or (
        transport_req == "auto" and cfg.get("allow_api_fallback"))
    key = used_profile = None
    if want_paid:
        key, used_profile, kerr = _resolve_openai_key(profile)
        if transport_req == "websocket" and kerr:
            return kerr, 400

    with _MGR_LOCK:
        if _SESSION is not None and _SESSION.state not in ("closed", "error"):
            return {"ok": False, "code": "voice_busy",
                    "error": "a voice session is already running"}, 409
        sess = VoiceSession("voice_" + secrets.token_hex(4), voice, None)
        _SESSION = sess

    try:
        _bootstrap(sess, codex_info["bin"], transport_req, key, used_profile,
                   sdp_offer, cfg)
    except _VoiceStartError as e:
        sess.error = _redact(str(e)[:300], key)
        sess.close(reason="start_failed")
        return {"ok": False, "code": e.code,
                "error": sess.error or str(e)}, e.status
    except Exception as e:
        sess.error = _redact(str(e)[:300], key)
        sess.close(reason="start_failed")
        return {"ok": False, "code": "voice_start_failed",
                "error": sess.error or "voice session failed to start"}, 502

    return {"ok": True, "session_id": sess.id, "state": sess.state,
            "sdp_answer": sess._sdp_answer, "voice": sess.voice,
            "profile": sess.profile, "transport": sess.transport,
            "billing": sess.billing}, 200


def _bootstrap(sess, codex_bin, transport_req, key, used_profile, sdp_offer, cfg):
    """Try transports in order: WebRTC on the ChatGPT subscription first,
    then the paid websocket path (child env gets the BYOK key) only when
    WebRTC failed and a key exists."""
    VOICE_SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    plan = []
    if transport_req in ("auto", "webrtc"):
        plan.append(("webrtc", None, sdp_offer))
    if key and transport_req in ("auto", "websocket"):
        if transport_req == "websocket" or cfg.get("allow_api_fallback", True):
            plan.append(("websocket", key, None))
    if not plan:
        raise _VoiceStartError("voice_bad_request", "no viable voice transport", 400)

    last_err = None
    for transport, env_key, offer in plan:
        try:
            _bootstrap_once(sess, codex_bin, transport, env_key, offer)
            sess.transport = transport
            sess.billing = "api_key" if env_key else "subscription"
            sess.profile = used_profile if env_key else None
            # A failed first attempt (e.g. the WebRTC sideband timeout) can
            # outlast the heartbeat window on short test budgets; the browser
            # heartbeats only after start returns, so reset the liveness clock.
            sess.heartbeat()
            sess.touch()
            return
        except _VoiceStartError as e:
            last_err = e
        except Exception as e:
            last_err = e
    code = getattr(last_err, "code", "voice_start_failed")
    status = getattr(last_err, "status", 502)
    raise _VoiceStartError(code, str(last_err)[:300] or "voice failed to start", status)


def _bootstrap_once(sess, codex_bin, transport, key, sdp_offer):
    """One spawn+negotiate attempt. Retries once with --enable
    realtime_conversation if the app-server build gates the feature."""
    last = None
    for extra_argv in ((), ("--enable", "realtime_conversation")):
        try:
            _bootstrap_child(sess, codex_bin, transport, key, sdp_offer, extra_argv)
            return
        except _MethodNotFound as e:
            last = e
            continue
    if isinstance(last, _VoiceStartError):
        raise last
    if last is not None:
        raise _VoiceStartError("voice_start_failed", str(last) or "realtime unavailable")


def _bootstrap_child(sess, codex_bin, transport, key, sdp_offer, extra_argv):
    sess._kill_proc()
    sess.thread_id = None
    sess.transport = transport   # outputAudio/delta routing needs it at once
    sess._sdp_event.clear()
    sess._connect_error = None
    sess.spawn(codex_bin, key, str(VOICE_SCRATCH_DIR), extra_argv=extra_argv)

    result, error = sess.call("initialize", {
        "clientInfo": {"name": "claude-command-center", "version": str(_core.__version__)},
        "capabilities": {"experimentalApi": True},
    }, timeout=START_TIMEOUT_SECONDS)
    if error or not isinstance(result, dict):
        raise _VoiceStartError(
            "voice_start_failed",
            f"app-server initialize failed: {(error or {}).get('message', 'no result')}")
    sess._send({"method": "initialized"})

    result, error = sess.call("thread/realtime/listVoices", {}, timeout=15)
    if isinstance(result, dict):
        _record_live_voices(result)
    elif error and _looks_method_missing(error):
        raise _MethodNotFound(str(error))

    briefing = build_briefing()
    result, error = sess.call("thread/start", {
        "cwd": str(VOICE_SCRATCH_DIR),
        "ephemeral": True,
        "approvalPolicy": "never",
        "sandbox": "read-only",
        "dynamicTools": _tool_specs(),
    }, timeout=START_TIMEOUT_SECONDS)
    thread = (result or {}).get("thread") or {}
    tid = thread.get("id")
    if error and _looks_method_missing(error):
        raise _MethodNotFound(str(error))
    if error or not tid:
        raise _VoiceStartError(
            "voice_start_failed",
            f"thread/start failed: {(error or {}).get('message', 'no thread id')}")
    sess.thread_id = tid

    rt_params = {
        "threadId": tid,
        "outputModality": "audio",
        "voice": sess.voice,
        "version": "v3",
        "prompt": _VOICE_PROMPT,
        "realtimeStartInstructions": _CODEX_INSTRUCTIONS,
        "initialItems": [{"role": "developer", "text": briefing}],
        "clientManagedHandoffs": False,
    }
    if transport == "webrtc":
        rt_params["transport"] = {"type": "webrtc", "sdp": sdp_offer}
    else:
        rt_params["transport"] = {"type": "websocket"}
    result, error = sess.call("thread/realtime/start", rt_params,
                              timeout=START_TIMEOUT_SECONDS)
    if error and _looks_method_missing(error):
        raise _MethodNotFound(str(error))
    if error:
        msg = str((error or {}).get("message") or "realtime/start failed")
        code = "voice_sideband_failed" if _looks_sideband(msg) else "voice_start_failed"
        raise _VoiceStartError(code, msg[:300])

    if transport == "webrtc":
        # SDP answer arrives as a notification; issue #35094-style failures
        # can leave it never arriving, so the wait is bounded either way.
        deadline_hit = not sess._sdp_event.wait(timeout=SDP_ANSWER_TIMEOUT_SECONDS)
        if sess._connect_error:
            raise _VoiceStartError("voice_sideband_failed",
                                   str(sess._connect_error)[:300])
        if deadline_hit:
            raise _VoiceStartError(
                "voice_connect_timeout",
                "the voice service did not answer the WebRTC offer in time. "
                "This happens for some accounts; try again or check Codex "
                "realtime availability for your plan")
        if not sess._sdp_answer:
            raise _VoiceStartError("voice_start_failed", "empty SDP answer")


def _looks_method_missing(error):
    msg = json.dumps(error or {}).lower()
    return ("method not found" in msg or "unknown method" in msg
            or "not enabled" in msg or "experimental" in msg
            or "no such method" in msg)


def _looks_sideband(msg):
    m = (msg or "").lower()
    return ("call_id_not_found" in m or "sideband" in m
            or "404" in m or "403" in m
            or "realtime conversation" in m)


def _tool_specs():
    """dynamicTools surface: read-only tools plus confirm-only proposals."""
    return [
        {
            "type": "function",
            "name": "ccc_attention",
            "description": (
                "List what currently needs the user's attention: live sessions "
                "waiting on them AND WatchTower tickets recorded as needing "
                "input (labeled recorded, with age). Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "max items, default 10"}},
            },
        },
        {
            "type": "function",
            "name": "ccc_session",
            "description": "Summarize one CCC session by session_id or by its display name/title. Read-only.",
            "inputSchema": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        },
        {
            "type": "function",
            "name": "ccc_sessions",
            "description": (
                "List open (live) CCC sessions across all engines with their "
                "session_ids, state, last-activity age and liveness proof "
                "(pid-verified or engine signal). Pass query to search by title "
                "words; live_only=false for recent history. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "max items, default 15"},
                    "live_only": {"type": "boolean"},
                    "query": {"type": "string", "description": "title words to search for"},
                },
            },
        },
        {
            "type": "function",
            "name": "ccc_models",
            "description": "List approved, currently available model IDs, labels, and supported efforts. Use before choosing an explicit engine/model triage route. Read-only.",
            "inputSchema": {"type": "object", "properties": {
                "engine": {"type": "string", "description": "optional engine name"},
                "query": {"type": "string", "description": "optional model name spoken by the user"},
            }},
        },
        {
            "type": "function",
            "name": "ccc_queues",
            "description": "WatchTower queue summaries with source timestamps, needs-input refs and "
            "how long each has been recorded as blocked. Pass queue for one named queue. Read-only.",
            "inputSchema": {"type": "object", "properties": {
                "queue": {"type": "string", "description": "optional WatchTower queue name"},
            }},
        },
        {
            "type": "function",
            "name": "ccc_jobs",
            "description": (
                "Scheduled jobs: systemd timers (system and user) and cron. Pass "
                "query (job, project or script words like 'bym digest' or 'ship') "
                "to see whether it ran, whether it succeeded, last output, next run "
                "and a verdict on why it has not run. problems_only lists failed or "
                "stale jobs. Read-only."
            ),
            "inputSchema": {"type": "object", "properties": {
                "query": {"type": "string", "description": "job/project/script words"},
                "problems_only": {"type": "boolean"},
                "limit": {"type": "integer", "description": "max jobs, default 6"},
            }},
        },
        {
            "type": "function",
            "name": "ccc_ticket",
            "description": "Read a WatchTower ticket body, recorded needs-input question, and recent comments. Read-only.",
            "inputSchema": {"type": "object", "properties": {
                "ref": {"type": "string", "description": "ticket ref like QUEUE-42"},
            }, "required": ["ref"]},
        },
        {
            "type": "function",
            "name": "ccc_triage_queue",
            "description": "Propose a senior session to safely triage one queue. Never spawns until the user confirms its CCC card. Defaults to the configured Deep profile; pass profile=default for worker defaults, or explicit engine, model, and optional effort for a chosen route.",
            "inputSchema": {"type": "object", "properties": {
                "queue": {"type": "string"},
                "profile": {"type": "string", "enum": ["deep", "default"]},
                "engine": {"type": "string"},
                "model": {"type": "string"},
                "effort": {"type": "string"},
            }, "required": ["queue"]},
        },
        {
            "type": "function",
            "name": "ccc_propose_action",
            "description": (
                "Propose a MUTATING action for the user to confirm in the CCC "
                "dashboard. Never executes anything. kind is one of "
                "spawn_session, inject, wt_add, wt_comment; params follows the "
                "kind: spawn_session {cwd, prompt, engine?, model?, name?}; "
                "inject {session_id, text}; wt_add {queue, title, text, priority?}; "
                "wt_comment {ref, text}."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["spawn_session", "inject", "wt_add", "wt_comment"]},
                    "params": {"type": "object"},
                    "reason": {"type": "string"},
                },
                "required": ["kind", "params"],
            },
        },
    ]


def voice_stop(session_id=None, reason="user"):
    sess = _session_or_none()
    if not sess:
        return {"ok": True, "stopped": False, "note": "no active voice session"}, 200
    if session_id and sess.id != session_id:
        return {"ok": False, "error": "session id mismatch",
                "code": "voice_no_session"}, 409
    sess.stop(reason=reason)
    _manager_clear(sess)
    return {"ok": True, "stopped": True}, 200


def notify_action_result(action):
    """server.py calls this after an assistant-actions confirm/dismiss so the
    voice session learns (and speaks) the outcome."""
    sess = _session_or_none()
    if sess:
        try:
            sess.notify_action_result(action)
        except Exception:
            pass


def voice_events_wait(session_id, after_seq, timeout=15.0):
    """SSE pump helper: returns (events, latest_seq, still_active)."""
    sess = _session_by_id(session_id)
    if not sess:
        return [], after_seq, False
    events = sess.wait_events(after_seq, timeout=timeout)
    latest = events[-1]["seq"] if events else after_seq
    with sess.cond:
        latest = max(latest, sess.seq)
    return events, latest, sess.state not in ("closed", "error")


def voice_shutdown():
    """Server exit hook: no orphan app-server children."""
    sess = _session_or_none()
    if sess:
        try:
            sess.stop(reason="server_shutdown")
        except Exception:
            pass
