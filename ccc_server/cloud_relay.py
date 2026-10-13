"""CCC Cloud Relay — local (device) client.

Stdlib-only implementation of the device side of the CCC Cloud Relay protocol
(docs/cloud-relay/PROTOCOL.md, §2/§3/§4/§7). The device only ever makes
OUTBOUND HTTPS requests: it pushes a minimized state snapshot and long-polls
for commands, then validates and executes every command itself against its own
loopback CCC API — exactly as a local caller would. The relay is not a proxy.

Design rules honoured here:

- No inbound listener; no pip deps (urllib/http.client/json/sqlite3/hashlib/
  hmac/secrets/threading/re/os/time/datetime/socket only).
- Disabled by default. Enabling and pairing are localhost-only (server.py
  `_cloud_relay_local_only`); this module never widens network trust, never touches
  network.json / ALLOWED_ORIGINS / Tailscale / telemetry.
- Local minimization is mandatory: titles/labels/summaries/question text run
  through redact() before leaving the machine; `share_titles` off sends opaque
  "Session on <machine>" labels.
- Every executed/rejected command appends to cloud/audit.jsonl (no payloads,
  no secrets).
- CCC_CLOUD_DISABLED=1 kills the loop unconditionally.

State lives under ~/.claude/command-center/cloud/:
  config.json           — {enabled, relay_url, share_titles, device_id,
                            account_email_masked, account_ref, disabled_reason}
  credentials           — 0600 {device_id, device_secret} (secret, never logged)
  idempotency.sqlite3   — request_id -> {result_json, ts} (24h) + last_seq
  audit.jsonl           — {ts, request_id, capability, session_ref, outcome}
"""

from __future__ import annotations

import hashlib
import hmac  # noqa: F401
import http.client  # noqa: F401
import json
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    import fcntl  # repo-root shim delegates to the stdlib module on POSIX
except Exception:  # pragma: no cover
    fcntl = None

try:  # federation is a repo-root stdlib module; used only for machine label.
    import federation  # type: ignore
except Exception:  # pragma: no cover - federation always present in-repo
    federation = None

PROTOCOL_VERSION = 1

# Field caps (PROTOCOL §4.1 — the relay independently enforces these; we apply
# them locally so nothing over-length ever leaves the machine).
CAP_TITLE = 120
CAP_SUMMARY = 300
CAP_QUESTION = 2000
CAP_REPLY = 4000
CAP_LABEL = 120
CAP_TURN_TEXT = 2000
MAX_SESSIONS = 200
MAX_ATTENTION = 50
MAX_QUEUES = 50
MAX_SCHEDULES = 50
MAX_QUESTIONS = 50
MAX_REPLIES_UP = 20
MAX_OPTIONS = 4
MAX_TURNS = 20

# Command validation (PROTOCOL §4.3).
ALLOWED_CAPABILITIES = (
    "session.send_input",
    "question.answer",
    "session.wake",
    "session.detail",
    "session.history",
)
COMMAND_MAX_LIFETIME_S = 600      # 10 min
COMMAND_SKEW_GRACE_S = 60         # clock-skew grace
COMMAND_TEXT_MAX_BYTES = 16 * 1024  # 16 KiB input cap

# session.history: a larger bounded window than session.detail. Both detail
# bodies are trimmed oldest-first to stay under the relay's 48 KiB
# detail_json bound (an oversized body is refused there as detail_too_large).
HISTORY_MAX_TURNS = 120
HISTORY_TURN_CAP = 4000
HISTORY_TAIL_BYTES = 384 * 1024
DETAIL_MAX_BYTES = 40 * 1024

# Consecutive 401 bad_device_credentials before the loop gives up and asks
# the user to pair again (one stray 401 must not wipe a live pairing).
CREDENTIAL_REJECTS_FATAL = 3
# Loop-stop reasons that mean the service no longer accepts this machine:
# the local credential is dead weight and is wiped (fail closed locally).
SERVICE_DISCONNECT_REASONS = ("device_revoked", "account_deleted",
                              "credentials_rejected")
DEVICE_CAPABILITIES = ["sessions", "attention", "workers", "questions",
                       "session_input", "question_answer", "session_wake",
                       "session_detail", "session_history"]

# Loop timing (PROTOCOL §7).
STATE_PUSH_INTERVAL_S = 20.0
STATE_PUSH_MIN_INTERVAL_S = 3.0   # coalesce on-demand pushes
# An unchanged snapshot is sent as a small heartbeat instead (only when the
# service advertised `unchanged_ok`); a full snapshot still goes out at least
# this often so a service that lost its copy recovers on its own.
STATE_FULL_REFRESH_S = 600.0
POLL_WAIT_S = 25
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 60.0

ENGINE_ENUM = {"claude", "codex", "gemini", "cursor", "hermes", "other"}
QUEUE_STATE_ENUM = {"ok", "backlog", "draining", "stuck"}
ATTENTION_KIND_ENUM = {"question", "stuck", "failed", "completed", "approval", "offline"}


# ---------------------------------------------------------------------------
# Environment kill switch
# ---------------------------------------------------------------------------

def cloud_disabled_env() -> bool:
    """CCC_CLOUD_DISABLED=1 (or true/yes/on) kills the relay loop
    unconditionally — a hard switch parallel to CCC_TELEMETRY_DISABLED but
    independent of it. Checked at loop start and on every cycle."""
    v = (os.environ.get("CCC_CLOUD_DISABLED") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Paths / state directory
# ---------------------------------------------------------------------------

def _default_state_dir() -> str:
    # Test/override hook keeps unit tests hermetic without touching real state.
    override = os.environ.get("CCC_CLOUD_STATE_DIR")
    if override:
        return override
    try:
        from ccc_server.paths import COMMAND_CENTER_STATE_DIR
        return os.path.join(str(COMMAND_CENTER_STATE_DIR), "cloud")
    except Exception:  # pragma: no cover - leaf module always present
        return os.path.join(os.path.expanduser("~"), ".claude", "command-center", "cloud")


class CloudPaths:
    def __init__(self, state_dir: str | None = None):
        self.dir = state_dir or _default_state_dir()

    def _p(self, name: str) -> str:
        return os.path.join(self.dir, name)

    @property
    def config(self) -> str:
        return self._p("config.json")

    @property
    def credentials(self) -> str:
        return self._p("credentials")

    @property
    def idempotency(self) -> str:
        return self._p("idempotency.sqlite3")

    @property
    def audit(self) -> str:
        return self._p("audit.jsonl")

    @property
    def loop_lock(self) -> str:
        return self._p("loop.lock")

    def ensure(self) -> None:
        os.makedirs(self.dir, exist_ok=True)


# ---------------------------------------------------------------------------
# Redaction & truncation (PROTOCOL §4.1 / data-classification "class E")
# ---------------------------------------------------------------------------

# Credentialed URL for ANY scheme (ssh://, ftp://, git://, http(s)://, …), not
# just http, so `ssh://user:pass@host/path` cannot leak its userinfo.
_RE_URL_USERINFO = re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s/@]+@[^\s]+", re.IGNORECASE)
# Any absolute POSIX path of two or more segments, plus tilde paths — not just a
# fixed prefix allowlist (the old 7-prefix list leaked /usr, /mnt, /srv, /root,
# /Applications, /Volumes/<mount>, /Library, /media, ~/… etc.).
_RE_PATH_POSIX = re.compile(r"(?:~|(?<![\w.]))/[\w.\-]+(?:/[\w.\-]+)+")
# Windows drive paths AND UNC shares (\\server\share\…).
_RE_PATH_WIN = re.compile(r"(?:[A-Za-z]:\\|\\\\[^\s\\]+\\)[^\s\"'<>|]+")
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_RE_HEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")
# base64/urlsafe-ish token >=16 chars containing a digit OR at least one of the
# non-word token chars (+ / = _ -). We deliberately do NOT treat a plain
# mixed-case alphabetic run as a token: real secrets almost always carry a
# digit or symbol, whereas CamelCase code identifiers (AuthenticationManager)
# are common in legitimate session titles and must survive. An all-letter
# secret is a rare residual gap; `share_titles=off` is the guarantee for it.
_RE_TOKEN = re.compile(
    r"\b(?=[A-Za-z0-9+/_=\-]*(?:\d|[+/=_\-]))[A-Za-z0-9+/_=\-]{16,}\b"
)

_REDACTED = "[redacted]"


def redact(text) -> str:
    """Strip things that look like absolute paths (POSIX/tilde/Windows/UNC),
    credentialed URLs of any scheme, emails, and long hex/base64 tokens. Order
    matters: URLs-with-userinfo and paths first (they can contain @ / hex),
    then emails, then bare tokens.

    This is a heuristic best-effort minimizer, NOT a guarantee. The guarantee
    for sensitive labels is `share_titles=off` (opaque labels); see the privacy
    docs. It errs toward over-redaction of long identifier-like tokens."""
    if text is None:
        return ""
    s = str(text)
    s = _RE_URL_USERINFO.sub(_REDACTED, s)
    s = _RE_PATH_POSIX.sub(_REDACTED, s)
    s = _RE_PATH_WIN.sub(_REDACTED, s)
    s = _RE_EMAIL.sub(_REDACTED, s)
    s = _RE_HEX.sub(_REDACTED, s)
    s = _RE_TOKEN.sub(_REDACTED, s)
    return s


def truncate(text, limit: int) -> str:
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    if limit <= 1:
        return s[:limit]
    return s[: limit - 1] + "…"


def redact_cap(text, limit: int) -> str:
    """Redact first, then hard-cap. Redaction can only shrink/replace, and we
    cap after so the final field is always within the protocol bound."""
    return truncate(redact(text), limit)


def mask_email(email: str) -> str:
    """a***@***.com — mirrors the masked form the relay returns; used only as a
    local fallback if the relay did not pre-mask."""
    email = (email or "").strip()
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    head = local[:1] if local else ""
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    return f"{head}***@***.{tld}" if tld else f"{head}***@***"


# ---------------------------------------------------------------------------
# Config & credentials
# ---------------------------------------------------------------------------

_CONFIG_DEFAULTS = {
    "enabled": False,
    "relay_url": "",
    "share_titles": True,
    "device_id": "",
    "account_email_masked": "",
    "account_ref": "",
    "disabled_reason": "",
}


def load_config(paths: CloudPaths) -> dict:
    data = {}
    try:
        with open(paths.config, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = dict(_CONFIG_DEFAULTS)
    for k in _CONFIG_DEFAULTS:
        if k in data:
            out[k] = data[k]
    return out


def save_config(paths: CloudPaths, config: dict) -> None:
    paths.ensure()
    _atomic_write(paths.config, json.dumps(config, indent=2) + "\n", mode=0o644)


def load_credentials(paths: CloudPaths) -> dict | None:
    try:
        with open(paths.credentials, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("device_id") and data.get("device_secret"):
        return data
    return None


def save_credentials(paths: CloudPaths, creds: dict) -> None:
    """Write the device secret 0600, atomically. Never logged, never echoed."""
    paths.ensure()
    _atomic_write(paths.credentials, json.dumps(creds), mode=0o600)


def wipe_credentials(paths: CloudPaths) -> None:
    try:
        os.remove(paths.credentials)
    except OSError:
        pass


def _atomic_write(path: str, text: str, mode: int) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    # O_CREAT with an explicit mode so the secret file is 0600 from the first
    # byte (no window where it exists world-readable).
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Idempotency store (sqlite3) — request_id -> result, 24h retention + last_seq
# ---------------------------------------------------------------------------

class IdempotencyStore:
    RETENTION_S = 24 * 3600

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        return conn

    def _init_db(self):
        with self._lock, self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS idem ("
                "request_id TEXT PRIMARY KEY, result_json TEXT, ts REAL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)"
            )
            # Reservations mark a request_id as dispatch-attempted BEFORE the
            # local action runs, so a crash between dispatch and record_result
            # cannot cause a re-execution (double action) on redelivery. A
            # reserved-but-never-finalized id resolves to an "interrupted"
            # result — at-most-once, which is the safe default for an action
            # that sends input to an agent.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS inprogress ("
                "request_id TEXT PRIMARY KEY, ts REAL)"
            )

    def get_result(self, request_id: str) -> dict | None:
        if not request_id:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM idem WHERE request_id = ?", (request_id,)
            ).fetchone()
        if not row or not row[0]:
            return None
        try:
            return json.loads(row[0])
        except ValueError:
            return None

    def reserve(self, request_id: str) -> bool:
        """Mark request_id dispatch-attempted before the local action runs.
        Returns True only for the caller that inserted the reservation, so two
        racing executors (threads or processes sharing the store) can never
        both dispatch the same request_id. Already-finalized ids return False."""
        if not request_id:
            return True
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM inprogress WHERE ts < ?", (now - self.RETENTION_S,))
            if conn.execute(
                    "SELECT 1 FROM idem WHERE request_id = ?", (request_id,)).fetchone():
                return False
            cur = conn.execute(
                "INSERT OR IGNORE INTO inprogress (request_id, ts) VALUES (?, ?)",
                (request_id, now),
            )
            return cur.rowcount == 1

    def is_reserved(self, request_id: str) -> bool:
        if not request_id:
            return False
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM inprogress WHERE request_id = ?", (request_id,)
            ).fetchone()
        return row is not None

    def record_result(self, request_id: str, result: dict) -> None:
        if not request_id:
            return
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO idem (request_id, result_json, ts) "
                "VALUES (?, ?, ?)",
                (request_id, json.dumps(result), now),
            )
            # The command is finalized — clear its in-progress reservation.
            conn.execute(
                "DELETE FROM inprogress WHERE request_id = ?", (request_id,))
            conn.execute("DELETE FROM idem WHERE ts < ?", (now - self.RETENTION_S,))

    def get_last_seq(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT v FROM meta WHERE k = 'last_seq'"
            ).fetchone()
        if not row:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return 0

    def set_last_seq(self, seq: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta (k, v) VALUES ('last_seq', ?)",
                (str(int(seq)),),
            )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def audit_append(paths: CloudPaths, request_id: str, capability: str,
                 session_ref: str, outcome: str) -> None:
    """Append one bounded audit row. Contains ids + enum outcome only — never
    the command text, never secrets."""
    row = {
        "ts": _iso_now(),
        "request_id": request_id or "",
        "capability": capability or "",
        "session_ref": session_ref or "",
        "outcome": outcome or "",
    }
    try:
        paths.ensure()
        with open(paths.audit, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Session replies: the way back from a session to the remote client
# ---------------------------------------------------------------------------
# session.send_input reaches a Claude session as a peer message from CCC's
# own peer socket, so the session answers with SendMessage to "ccc". CCC
# keeps the sessions the relay messaged ("open threads"). A peer message from
# one of them is held here and goes up in the next snapshot as `replies`; the
# relay's ask API hands it to the caller that asked (sync, or email + fetch).

REPLY_THREAD_TTL_S = 24 * 3600
MAX_REPLIES = 20
RELAY_REPLY_HINT = ("[Sent from a remote client through CCC Cloud Relay. "
                    "To answer, SendMessage to \"ccc\"; CCC forwards it back.]")
_REPLIES_LOCK = threading.Lock()


def _replies_path(paths: CloudPaths) -> str:
    return paths._p("replies.json")


def _load_replies(paths: CloudPaths, now: float) -> dict:
    try:
        with open(_replies_path(paths), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    threads = data.get("threads") if isinstance(data, dict) else None
    replies = data.get("replies") if isinstance(data, dict) else None
    cutoff = now - REPLY_THREAD_TTL_S
    threads = {str(k): float(v) for k, v in (threads or {}).items()
               if isinstance(v, (int, float)) and v >= cutoff}
    replies = [r for r in (replies or []) if isinstance(r, dict)
               and float(r.get("at") or 0) >= cutoff][-MAX_REPLIES:]
    return {"threads": threads, "replies": replies}


def _save_replies(paths: CloudPaths, data: dict) -> None:
    _atomic_write(_replies_path(paths), json.dumps(data), 0o600)


def open_reply_thread(paths: CloudPaths, session_ref: str, now: float | None = None) -> None:
    """The remote client just messaged this session: accept its replies, and
    drop replies it already saw (it is answering them)."""
    ref = _norm_ref(session_ref)
    if not ref:
        return
    now = time.time() if now is None else now
    with _REPLIES_LOCK:
        data = _load_replies(paths, now)
        data["threads"][ref] = now
        data["replies"] = [r for r in data["replies"] if r.get("session_ref") != ref]
        try:
            _save_replies(paths, data)
        except OSError:
            pass


def record_session_reply(paths: CloudPaths, session_ref: str, text: str,
                         now: float | None = None) -> bool:
    """Hold a peer message to CCC for the remote client. False when the
    session has no open thread (CCC never guesses a destination)."""
    ref = _norm_ref(session_ref)
    body = str(text or "").strip()
    if not ref or not body:
        return False
    now = time.time() if now is None else now
    with _REPLIES_LOCK:
        data = _load_replies(paths, now)
        if ref not in data["threads"]:
            return False
        data["replies"].append({"id": secrets.token_hex(6), "session_ref": ref,
                                "text": body[:COMMAND_TEXT_MAX_BYTES], "at": now})
        data["replies"] = data["replies"][-MAX_REPLIES:]
        try:
            _save_replies(paths, data)
        except OSError:
            return False
    return True


def pending_replies(paths: CloudPaths, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    with _REPLIES_LOCK:
        return _load_replies(paths, now)["replies"]


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_from_epoch(epoch) -> str | None:
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def _parse_iso(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# Machine label / platform
# ---------------------------------------------------------------------------

def _hostname() -> str:
    try:
        return socket.gethostname().split(".")[0] or "this-mac"
    except OSError:
        return "this-mac"


def machine_label() -> str:
    if federation is not None:
        try:
            name = (federation.node_identity() or {}).get("display_name")
            if name:
                return truncate(str(name), CAP_LABEL)
        except Exception:
            pass
    return truncate(_hostname(), CAP_LABEL)


def _platform() -> str:
    try:
        return os.uname().sysname.lower()
    except AttributeError:  # Windows
        return (os.environ.get("OS") or "windows").lower()


# ---------------------------------------------------------------------------
# Local loopback API client
# ---------------------------------------------------------------------------

class LocalAPI:
    """Calls THIS machine's CCC server on 127.0.0.1:<port>. Mirrors server.py's
    _federation_self_api so relayed writes get identical validation as local
    callers."""

    def __init__(self, port: int, timeout: float = 30.0):
        self.port = int(port)
        self.timeout = timeout

    def call(self, method: str, path: str, body: dict | None = None,
             query: dict | None = None, timeout: float | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        if query:
            pairs = {k: v for k, v in query.items() if v is not None}
            if pairs:
                url += "?" + urllib.parse.urlencode(pairs)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        # Loopback origin so same-origin POST gates accept the call.
        req.add_header("Origin", f"http://127.0.0.1:{self.port}")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            try:
                parsed = json.loads(e.read().decode("utf-8", "replace"))
            except ValueError:
                parsed = {}
            return e.code, (parsed if isinstance(parsed, dict) else {})
        except (OSError, ValueError) as e:
            return 0, {"ok": False, "error": f"local api call failed: {e}"}

    def get(self, path: str, query: dict | None = None):
        return self.call("GET", path, query=query)

    def post(self, path: str, body: dict | None = None):
        return self.call("POST", path, body=body)


# ---------------------------------------------------------------------------
# Schedules provider (honest minimal)
# ---------------------------------------------------------------------------

def collect_schedules() -> list[dict]:
    """Best-effort real schedule state. Currently: launchd LaunchAgents whose
    label mentions 'ccc' or 'claude'. Returns bounded rows or [] when nothing
    is found (the caller then OMITS 'schedules' from the capability list, which
    the protocol supports)."""
    rows: list[dict] = []
    home = os.path.expanduser("~")
    la_dir = os.path.join(home, "Library", "LaunchAgents")
    try:
        names = sorted(os.listdir(la_dir))
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".plist"):
            continue
        low = name.lower()
        if "ccc" not in low and "claude" not in low:
            continue
        label = redact_cap(name[: -len(".plist")], CAP_LABEL)
        rows.append({
            "label": label,
            "kind": "launchd",
            "next_run": None,
            "last_result": "none",
            "last_run": None,
        })
        if len(rows) >= MAX_SCHEDULES:
            break
    return rows


# ---------------------------------------------------------------------------
# Snapshot builder (PROTOCOL §4.1)
# ---------------------------------------------------------------------------

def _norm_engine(engine) -> str:
    e = str(engine or "claude").strip().lower()
    return e if e in ENGINE_ENUM else "other"


def _map_attention_kind(kind) -> str:
    """Local classifier kind -> wire enum, per PROTOCOL Appendix A #5."""
    k = str(kind or "").strip().lower()
    if k in ATTENTION_KIND_ENUM:
        return k
    if k in ("question_blocked", "soft_block", "sidecar_waiting") \
            or "question" in k or "waiting" in k or "ask" in k:
        return "question"
    # A wedged tool call is not an approval request; it falls through to
    # "stuck" below.
    if k == "stale_tool_call":
        return "stuck"
    if k in ("pending_tool", "needs_approval") \
            or "approval" in k or "permission" in k:
        return "approval"
    if "fail" in k or "error" in k:
        return "failed"
    if k in ("pushed_open", "committed_not_pushed", "uncommitted_edits",
             "open_backlog", "needs_attention_label") \
            or "complete" in k or "done" in k or "pushed" in k \
            or "committed" in k:
        return "completed"
    if "offline" in k:
        return "offline"
    return "stuck"


def _norm_queue_state(state) -> str:
    s = str(state or "").strip().lower()
    s = {"active": "draining", "clear": "ok"}.get(s, s)
    return s if s in QUEUE_STATE_ENUM else "backlog"


SNAPSHOT_SESSION_FIELDS = (
    "id", "session_id", "display_name", "folder_label", "engine", "state",
    "is_live", "mtime", "modified", "live_context_percent", "question_waiting",
    "question_id", "question_text", "question_options",
    # Sub-agent / approval context for attention items (additive).
    "parent_session_id", "pending_tool", "pending_file", "first_message",
)
SNAPSHOT_SESSION_QUERY = {
    "all": "1", "compact": "1", "limit": str(MAX_SESSIONS),
    "fields": ",".join(SNAPSHOT_SESSION_FIELDS),
}


def _context_pct(row: dict):
    """Percent of the context window used (PROTOCOL §4.1), or None."""
    val = row.get("context_pct")
    if val is None:
        val = row.get("live_context_percent")
    try:
        pct = float(val)
    except (TypeError, ValueError):
        return None
    if pct <= 0:
        return None
    return max(0, min(100, int(round(pct))))


def _norm_ref(value) -> str:
    """One ref per session across every list. The attention feed uses the
    dashboard's `session_<id>` row ids while /api/sessions uses the bare id,
    so a remote client could never join an attention item to its session."""
    ref = str(value or "")
    return ref[len("session_"):] if ref.startswith("session_") else ref


def is_subagent_ref(ref) -> bool:
    """Task-tool sub-agent refs: `<parent>:agent-<hex>` or bare `agent-<hex>`."""
    r = str(ref or "")
    return ":agent-" in r or r.startswith("agent-")


def _action_text(tool, target) -> str:
    """`Bash: git push origin main` style preview of a pending tool call."""
    tool = str(tool or "").strip()
    target = str(target or "").strip()
    if not tool:
        return ""
    return f"{tool}: {target}" if target else tool


def build_snapshot(local_api: LocalAPI, config: dict, ccc_version: str = "0.0.0",
                   paths: CloudPaths | None = None) -> dict:
    """Pull local read models over loopback, minimize + cap, and assemble the
    §4.1 state body. `share_titles` off replaces titles/repo labels with opaque
    'Session on <machine>' labels."""
    share_titles = bool(config.get("share_titles", True))
    host = _hostname()
    m_label = machine_label()

    # --- sessions ---
    # Perf: /api/sessions?all=1 is served from the persisted archive response
    # cache (no O(all sessions) rebuild per push); compact+limit+fields keep
    # the per-push JSON to MAX_SESSIONS projected rows.
    _, s_data = local_api.get("/api/sessions", query=SNAPSHOT_SESSION_QUERY)
    rows = []
    if isinstance(s_data, dict):
        rows = s_data.get("sessions") or s_data.get("conversations") or []
    sessions = []
    questions = []
    labels = {}  # ref -> (title, repo_label), reused by attention items
    rinfo = {}   # ref -> sub-agent / approval context, reused by attention items
    aliases = {}  # bare `agent-<hex>` session id -> composite session ref
    for row in rows[:MAX_SESSIONS]:
        if not isinstance(row, dict):
            continue
        ref = _norm_ref(row.get("id") or row.get("session_id"))
        if not ref:
            continue
        q_waiting = bool(row.get("question_waiting"))
        if share_titles:
            title = redact_cap(row.get("display_name"), CAP_TITLE)
            title_source = "redacted"
            repo_label = redact_cap(row.get("folder_label"), CAP_LABEL)
        else:
            title = f"Session on {host}"
            title_source = "opaque"
            repo_label = None
        session = {
            "ref": ref,
            "title": title,
            "title_source": title_source,
            "engine": _norm_engine(row.get("engine")),
            "state": str(row.get("state") or "idle"),
            "is_live": bool(row.get("is_live")),
            "recency": _iso_from_epoch(row.get("mtime") or row.get("modified")),
            "context_pct": _context_pct(row),
            "machine_label": m_label,
            "question_waiting": q_waiting,
        }
        if repo_label is not None:
            session["repo_label"] = repo_label
        sessions.append(session)
        labels[ref] = (title, repo_label)
        info = {
            "parent": _norm_ref(row.get("parent_session_id")),
            "tool": row.get("pending_tool"),
            "target": row.get("pending_file"),
            "name": row.get("display_name") or row.get("first_message"),
            "live": bool(row.get("is_live")) or session["state"] == "working",
        }
        rinfo[ref] = info
        bare = str(row.get("session_id") or "")
        if bare and bare != ref:
            aliases[bare] = ref

        if q_waiting and len(questions) < MAX_QUESTIONS:
            opts_raw = row.get("question_options") or []
            options = []
            if isinstance(opts_raw, list):
                for o in opts_raw[:MAX_OPTIONS]:
                    label = o.get("label") if isinstance(o, dict) else o
                    options.append({"label": redact_cap(label, CAP_LABEL)})
            questions.append({
                "session_ref": ref,
                "question_id": str(row.get("question_id") or ref),
                "text": redact_cap(row.get("question_text"), CAP_QUESTION),
                "options": options,
                "asked_at": _iso_from_epoch(row.get("mtime")),
            })

    # --- attention (recent live feed) ---
    _, a_data = local_api.get("/api/attention",
                              query={"scope": "recent", "limit": str(MAX_ATTENTION)})
    items = a_data.get("items") if isinstance(a_data, dict) else None
    attention = []
    for it in (items or [])[:MAX_ATTENTION]:
        if not isinstance(it, dict):
            continue
        # Current feed items carry question_text / where / next_step rather
        # than a ready-made summary; pick the most informative available.
        # A prose soft-block's question_text is a scraped transcript line;
        # the session's own next step is the clean ask, so it wins there.
        if it.get("kind") in ("soft_block", "sidecar_waiting"):
            summary = it.get("next_step") or it.get("question_text") or ""
        else:
            summary = it.get("question_text") or ""
        summary = (it.get("summary") or summary or it.get("where")
                   or it.get("next_step") or "")
        a_ref = _norm_ref(it.get("session_id") or it.get("session_ref"))
        # The feed names a sub-agent by its bare id; join it to the composite
        # ref the sessions list uses.
        a_ref = aliases.get(a_ref, a_ref)
        # Name the session even when it fell outside the capped sessions list.
        if a_ref in labels:
            a_title, a_repo = labels[a_ref]
        elif share_titles:
            a_title = redact_cap(it.get("name"), CAP_TITLE)
            a_repo = redact_cap(it.get("folder_label"), CAP_LABEL)
        else:
            a_title, a_repo = f"Session on {host}", None
        kind = _map_attention_kind(it.get("kind"))
        info = rinfo.get(a_ref) or {}
        extra = {}
        sub = is_subagent_ref(a_ref) or is_subagent_ref(it.get("session_id"))
        if sub:
            parent = (info.get("parent") or _norm_ref(it.get("parent_session_id"))
                      or (a_ref.split(":agent-")[0] if ":agent-" in a_ref else ""))
            extra["subagent"] = True
            if parent:
                extra["parent_ref"] = parent
                extra["covered_by_parent"] = bool(
                    (rinfo.get(parent) or {}).get("live"))
            if share_titles:
                label = redact_cap(info.get("name") or it.get("name"), CAP_LABEL)
                p_title, p_repo = labels.get(parent, (None, None))
                if label:
                    extra["agent_label"] = label
                if p_title:
                    extra["parent_title"] = p_title
                if label:
                    a_title = (f"{label} (sub-agent of {p_title})" if p_title
                               else f"{label} (sub-agent)")
                    a_title = redact_cap(a_title, CAP_TITLE)
                if not a_repo and p_repo:
                    a_repo = p_repo
        if share_titles and kind == "approval":
            act = _action_text(info.get("tool"), info.get("target"))
            if act:
                extra["action"] = redact_cap(act, CAP_SUMMARY)
        item = {
            "kind": kind,
            "priority": int(it.get("priority") or 9),
            "session_ref": a_ref,
            "title": a_title,
            "summary": redact_cap(summary, CAP_SUMMARY),
            "created": _iso_from_epoch(
                it.get("mtime") or it.get("modified")) or _iso_now(),
        }
        if a_repo:
            item["repo_label"] = a_repo
        item.update(extra)
        attention.append(item)

    # --- session replies (peer messages to CCC on an open relay thread) ---
    # Replies go up as a top-level `replies` list (the relay's ask API matches
    # them to a waiting caller). Each also gets a visible attention item of
    # kind "reply" (not "question": a reply must not fire question_waiting).
    # Answering the session (session.send_input / question.answer) clears it.
    replies = []
    if paths is not None:
        for r in pending_replies(paths):
            r_ref = r.get("session_ref") or ""
            r_title = labels.get(r_ref, (f"Session on {host}", None))[0]
            r_at = _iso_from_epoch(r.get("at")) or _iso_now()
            if len(replies) < MAX_REPLIES_UP:
                replies.append({
                    "id": str(r.get("id") or ""),
                    "session_ref": r_ref,
                    "text": redact_cap(r.get("text"), CAP_REPLY),
                    "at": r_at,
                })
            if len(attention) < MAX_ATTENTION:
                attention.insert(0, {
                    "kind": "reply",
                    "priority": 1,
                    "session_ref": r_ref,
                    "title": r_title,
                    "summary": redact_cap("Reply: " + str(r.get("text") or ""), CAP_SUMMARY),
                    "created": r_at,
                })

    # --- workers (per-queue rollup via the existing HTTP endpoint) ---
    # NOTE: no dedicated /api/queue-health route exists; queue health ships
    # inside GET /api/ux-fixes/health as `queues[]`. We consume that (HTTP,
    # in keeping with "prefer HTTP") rather than importing server internals.
    _, q_data = local_api.get("/api/ux-fixes/health")
    queues = q_data.get("queues") if isinstance(q_data, dict) else None
    workers = []
    for q in (queues or [])[:MAX_QUEUES]:
        if not isinstance(q, dict):
            continue
        workers.append({
            "queue": redact_cap(q.get("queue"), CAP_LABEL),
            "depth": int(q.get("total", sum(int(q.get(k) or 0) for k in
                                            ("depth", "in_progress", "closed"))) or 0),
            "open": int(q.get("depth") or 0),         # open (unclosed) count
            "workers_live": int(q.get("workers", q.get("workers_live", 0)) or 0),
            "state": _norm_queue_state(q.get("state")),
            "oldest_open_age_s": q.get("oldest_open_age_seconds"),
        })

    # --- schedules (omitted entirely when none found) ---
    schedules = collect_schedules()

    capabilities = ["sessions", "attention", "workers", "questions"]
    if schedules:
        capabilities.append("schedules")
    capabilities += ["session_input", "question_answer", "session_wake",
                     "session_detail", "session_history"]

    snapshot = {
        "v": PROTOCOL_VERSION,
        "snapshot_at": _iso_now(),
        "device": {
            "ccc_version": ccc_version,
            "platform": _platform(),
            "capabilities": capabilities,
        },
        "sessions": sessions,
        "attention": attention,
        "questions": questions,
        "workers": workers,
    }
    if schedules:
        snapshot["schedules"] = schedules
    if replies:
        snapshot["replies"] = replies
    return snapshot


def snapshot_digest(snapshot: dict) -> str:
    """Content hash of a snapshot, ignoring its own timestamp."""
    body = {k: v for k, v in snapshot.items() if k != "snapshot_at"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def heartbeat_body(snapshot: dict) -> dict:
    """The small state body that says "nothing changed since my last push"."""
    return {"v": snapshot.get("v", PROTOCOL_VERSION),
            "snapshot_at": snapshot.get("snapshot_at") or _iso_now(),
            "unchanged": True}


_DELTA_SKIP = ("v", "snapshot_at", "device", "sessions", "digest")


def build_delta(prev: dict, new: dict, base: str, digest: str) -> dict | None:
    """State body carrying only what changed between two snapshots, or None
    when a delta is not possible (sessions without unique refs). Sessions are
    keyed by ``ref`` (upsert/remove, plus ``order`` only when the resulting
    order differs from prev-minus-removed with new rows appended). Every
    other list section is sent whole when it changed and omitted otherwise
    (an emptied section is sent as ``[]``). The cloud reconstructs the full
    snapshot and runs its normal full-push path on it."""
    body = {"v": new.get("v", PROTOCOL_VERSION),
            "snapshot_at": new.get("snapshot_at") or _iso_now(),
            "delta": True, "base": base, "digest": digest}
    if new.get("device") != prev.get("device"):
        body["device"] = new.get("device")
    old_rows, new_rows = prev.get("sessions") or [], new.get("sessions") or []
    old_refs = [r.get("ref") if isinstance(r, dict) else None for r in old_rows]
    new_refs = [r.get("ref") if isinstance(r, dict) else None for r in new_rows]
    for refs in (old_refs, new_refs):
        if not all(isinstance(x, str) and x for x in refs) \
                or len(set(refs)) != len(refs):
            return None
    if old_rows != new_rows:
        old_by = dict(zip(old_refs, old_rows))
        upsert = [r for r in new_rows if old_by.get(r["ref"]) != r]
        new_set = set(new_refs)
        remove = [x for x in old_refs if x not in new_set]
        sess = {"upsert": upsert, "remove": remove}
        gone = set(remove)
        implied = [x for x in old_refs if x not in gone]
        implied += [x for x in new_refs if x not in set(old_refs)]
        if implied != new_refs:
            sess["order"] = new_refs
        body["sessions"] = sess
    for k in sorted((set(prev) | set(new)) - set(_DELTA_SKIP)):
        if prev.get(k) != new.get(k):
            body[k] = new.get(k) or []
    return body


# ---------------------------------------------------------------------------
# Command validation (PROTOCOL §4.3) — pure, fail-closed, in order
# ---------------------------------------------------------------------------

def validate_command(env: dict, last_seq: int, now: float | None = None) -> str | None:
    """Validate a command envelope for a NEW request_id (idempotency is handled
    by the executor, which short-circuits a seen request_id BEFORE this runs).

    Returns None when valid, else a PROTOCOL §6 error code. Order:
    version → capability → expiry → seq → payload schema/size.
    """
    if now is None:
        now = time.time()
    if not isinstance(env, dict):
        return "invalid_payload"
    if env.get("v") != PROTOCOL_VERSION:
        return "invalid_payload"
    capability = env.get("capability")
    if capability not in ALLOWED_CAPABILITIES:
        return "unknown_capability"

    exp = _parse_iso(env.get("expires"))
    if exp is None:
        return "invalid_payload"
    if exp < now - COMMAND_SKEW_GRACE_S:
        return "expired"
    if exp > now + COMMAND_MAX_LIFETIME_S + COMMAND_SKEW_GRACE_S:
        # Lifetime exceeds the 10-min cap — fail closed rather than honour a
        # command that could sit valid far longer than the protocol allows.
        return "expired"

    try:
        int(env.get("seq"))
    except (TypeError, ValueError):
        return "invalid_payload"
    # NOTE: seq is NOT a rejection gate. Replay defense is the idempotency
    # store (a seen request_id returns its recorded result) plus the expiry
    # window above. Rejecting a lower-than-last seq here would drop a command
    # that the relay legitimately re-queued out of order after a delivery to a
    # dead socket (an earlier, never-executed command redelivered after a later
    # one advanced last_seq) — a lost action. `last_seq` is retained only as an
    # advisory high-water mark, not a filter. See PROTOCOL §4.3.
    _ = last_seq

    payload = env.get("payload")
    if not isinstance(payload, dict):
        return "invalid_payload"
    if not payload.get("session_ref"):
        return "invalid_payload"
    text = payload.get("text")
    if text is not None:
        if not isinstance(text, str):
            return "invalid_payload"
        if len(text.encode("utf-8")) > COMMAND_TEXT_MAX_BYTES:
            return "payload_too_large"
    return None


# ---------------------------------------------------------------------------
# Command executor (PROTOCOL §4.3–§4.5)
# ---------------------------------------------------------------------------

class CommandExecutor:
    def __init__(self, local_api, store: IdempotencyStore, paths: CloudPaths | None = None,
                 share_titles_fn=None):
        self.local_api = local_api
        self.store = store
        self.paths = paths
        self._share_titles_fn = share_titles_fn

    def _result(self, request_id, status, error_code=None, detail_code=None, extra=None):
        res = {
            "request_id": request_id,
            "status": status,
            "error_code": error_code,
            "detail_code": detail_code,
            "completed_at": _iso_now(),
        }
        if extra:
            res.update(extra)
        return res

    def execute(self, env: dict) -> dict:
        """Full pipeline. Returns the §4.4 result body to POST to /v1/result."""
        request_id = ""
        capability = ""
        session_ref = ""
        if isinstance(env, dict):
            request_id = str(env.get("request_id") or "")
            capability = str(env.get("capability") or "")
            payload = env.get("payload") if isinstance(env.get("payload"), dict) else {}
            session_ref = str(payload.get("session_ref") or "")

        # --- basic envelope shape ---
        if not isinstance(env, dict) or env.get("v") != PROTOCOL_VERSION:
            return self._reject(request_id, capability, session_ref, "invalid_payload")
        if capability not in ALLOWED_CAPABILITIES:
            return self._reject(request_id, capability, session_ref, "unknown_capability")

        # --- idempotency short-circuit: a finalized request_id returns its
        # recorded result without re-executing. ---
        recorded = self.store.get_result(request_id)
        if recorded is not None:
            self._audit(request_id, capability, session_ref, "duplicate")
            return recorded

        # --- crash-recovery: a request_id reserved on a prior attempt but never
        # finalized means we dispatched (or were about to) and then died. We do
        # NOT re-dispatch — that would double-apply the action. Resolve it to an
        # interrupted result (at-most-once). ---
        if self.store.is_reserved(request_id):
            result = self._result(
                request_id, "error", error_code="interrupted",
                detail_code="interrupted")
            self.store.record_result(request_id, result)
            self._audit(request_id, capability, session_ref, "interrupted")
            return result

        # --- ordered validation for a genuinely new command (no seq gate) ---
        err = validate_command(env, self.store.get_last_seq())
        if err is not None:
            return self._reject(request_id, capability, session_ref, err)

        # --- reserve BEFORE dispatch so a crash mid-execution cannot cause a
        # re-execution on redelivery. ---
        if not self.store.reserve(request_id):
            # Another executor won the race (or finalized it meanwhile).
            recorded = self.store.get_result(request_id)
            if recorded is not None:
                self._audit(request_id, capability, session_ref, "duplicate")
                return recorded
            self._audit(request_id, capability, session_ref, "in_progress")
            return self._result(request_id, "error", error_code="interrupted",
                                detail_code="in_progress")

        # --- dispatch via loopback (§4.5) ---
        result = self._dispatch(env, capability, session_ref)
        # Advance the advisory high-water mark and persist the terminal result
        # (record_result clears the reservation atomically).
        try:
            cur = self.store.get_last_seq()
            self.store.set_last_seq(max(cur, int(env.get("seq"))))
        except (TypeError, ValueError):
            pass
        self.store.record_result(request_id, result)
        self._audit(request_id, capability, session_ref, result.get("status") or "ok")
        return result

    def _reject(self, request_id, capability, session_ref, error_code) -> dict:
        status = "expired" if error_code == "expired" else "rejected"
        result = self._result(request_id, status, error_code=error_code)
        # Record so a retry of the same rejected request_id stays idempotent.
        self.store.record_result(request_id, result)
        self._audit(request_id, capability, session_ref, status)
        return result

    def _audit(self, request_id, capability, session_ref, outcome):
        if self.paths is not None:
            audit_append(self.paths, request_id, capability, session_ref, outcome)

    def _dispatch(self, env, capability, session_ref) -> dict:
        payload = env.get("payload") or {}
        request_id = str(env.get("request_id") or "")
        text = payload.get("text") or ""
        if capability == "session.send_input":
            return self._inject(request_id, session_ref, text, mode="send")
        if capability == "session.wake":
            return self._inject(request_id, session_ref, text, mode="send",
                                detail_ok="queued_for_wake")
        if capability == "question.answer":
            if self.paths is not None:
                open_reply_thread(self.paths, session_ref)
            return self._answer(request_id, session_ref, payload)
        if capability == "session.detail":
            return self._detail(request_id, session_ref)
        if capability == "session.history":
            return self._history(request_id, session_ref)
        return self._result(request_id, "rejected", error_code="unknown_capability")

    def _inject(self, request_id, session_ref, text, mode, detail_ok="delivered") -> dict:
        if self.paths is not None:
            open_reply_thread(self.paths, session_ref)
        status, data = self.local_api.post("/api/inject-input", {
            "session_id": session_ref,
            "text": f"{text}\n\n{RELAY_REPLY_HINT}",
            "mode": mode,
            "origin": "cloud-relay",
        })
        return self._from_inject(request_id, status, data, default_detail=detail_ok)

    def _answer(self, request_id, session_ref, payload) -> dict:
        # Prefer the relayed-question path; fall back to answer-mode injection.
        # The relay's /v1/questions/answer envelope carries `answer` as a dict
        # {index: int} and/or {text: str}; map it onto the aligned-answers
        # shape /api/answer-question expects.
        answers = payload.get("answers")
        if not isinstance(answers, list) or not answers:
            ans = payload.get("answer")
            if isinstance(ans, dict):
                idx = ans.get("index")
                answers = [{
                    "index": idx if isinstance(idx, int) else -1,
                    "text": str(ans.get("text") or ""),
                }]
            else:
                answers = [{"index": -1,
                            "text": str(ans or payload.get("text") or "")}]
        status, data = self.local_api.post("/api/answer-question", {
            "session_id": session_ref,
            "answers": answers,
        })
        if isinstance(data, dict) and data.get("ok"):
            return self._result(request_id, "ok", detail_code="delivered")
        # No pending relayed question → fall back to answer-mode injection.
        text = ""
        if isinstance(answers, list) and answers:
            text = str(answers[0].get("text") or "")
        status, data = self.local_api.post("/api/inject-input", {
            "session_id": session_ref,
            "text": text,
            "mode": "answer",
            "origin": "cloud-relay",
        })
        return self._from_inject(request_id, status, data, default_detail="delivered")

    def _from_inject(self, request_id, status_code, data, default_detail) -> dict:
        if isinstance(data, dict) and data.get("ok"):
            detail = default_detail
            if data.get("resumed") or data.get("resume"):
                detail = "resumed"
            return self._result(request_id, "ok", detail_code=detail)
        err = ""
        local_code = ""
        if isinstance(data, dict):
            err = str(data.get("error") or data.get("message") or "")
            local_code = str(data.get("code") or "")
        code = self._error_code(status_code, err, local_code)
        extra = None
        if err or local_code:
            # Carry the loopback endpoint's own diagnostic so the issuer sees
            # WHY delivery failed. It leaves the machine, so it is redacted.
            extra = {"detail": {"local_error": redact_cap(err, 300),
                                "local_code": local_code[:80] or None}}
        return self._result(request_id, "error", error_code=code, extra=extra)

    @staticmethod
    def _error_code(status_code, err, local_code="") -> str:
        # A snake_case code from the loopback endpoint is the most honest
        # code; propagate it verbatim when present.
        if re.fullmatch(r"[a-z0-9_]{2,60}", local_code or ""):
            return local_code
        low = (err or "").lower()
        if status_code == 409 or "handoff" in low or "lease" in low:
            return "handoff_leased"
        if "no session" in low or "not found" in low or "missing session" in low:
            return "session_not_found"
        if "invalid" in low or "missing" in low:
            return "invalid_payload"
        return "delivery_failed"

    def _detail(self, request_id, session_ref) -> dict:
        """Assemble a bounded recent-turn window by tail-reading the session's
        own transcript (<=20 turns, <=2000 chars each, redacted), plus the
        session context an agent needs in one call: title, repo, parent,
        state, and the pending question / approval with its tool action. A
        sub-agent whose transcript cannot be read falls back to its parent's
        turns plus the sub-agent label."""
        row = self._find_row(session_ref)
        jsonl_path = row.get("jsonl_path") if row else None
        turns = None
        if jsonl_path:
            turns = tail_read_turns(jsonl_path, max_turns=MAX_TURNS, cap=CAP_TURN_TEXT)
        elif is_subagent_ref(session_ref):
            parent = self._parent_ref(session_ref, row)
            prow = self._find_row(parent) if parent else None
            if prow and prow.get("jsonl_path"):
                turns = tail_read_turns(prow["jsonl_path"], max_turns=MAX_TURNS,
                                        cap=CAP_TURN_TEXT)
                row = dict(row or {})
                row.setdefault("parent_session_id", parent)
                row["_parent_row"] = prow
                row["_fallback"] = "parent"
        if turns is None:
            return self._result(request_id, "error", error_code="session_not_found")
        detail = _bounded_detail(session_ref, turns,
                                 context=self._detail_context(session_ref, row))
        return self._result(request_id, "ok", detail_code="delivered",
                            extra={"detail": detail})

    def _history(self, request_id, session_ref) -> dict:
        """session.history: a longer bounded window from the same local
        tail-read, trimmed oldest-first until it fits the relay's
        detail_json bound. Never a fake 'ok' on an unreadable transcript."""
        row = self._find_row(session_ref)
        jsonl_path = row.get("jsonl_path") if row else None
        if not jsonl_path:
            return self._result(request_id, "error", error_code="session_not_found")
        turns = tail_read_turns(jsonl_path, max_turns=HISTORY_MAX_TURNS + 1,
                                cap=HISTORY_TURN_CAP, tail_bytes=HISTORY_TAIL_BYTES)
        truncated = len(turns) > HISTORY_MAX_TURNS
        detail = _bounded_detail(session_ref, turns[-HISTORY_MAX_TURNS:],
                                 truncated=truncated)
        return self._result(request_id, "ok", detail_code="delivered",
                            extra={"detail": detail})

    @staticmethod
    def _parent_ref(session_ref, row) -> str:
        if row and row.get("parent_session_id"):
            return _norm_ref(row.get("parent_session_id"))
        if ":agent-" in session_ref:
            return session_ref.split(":agent-")[0]
        return ""

    def _detail_context(self, session_ref, row) -> dict:
        """Title / repo / parent / state / pending question or approval."""
        row = row or {}
        share = True if self._share_titles_fn is None else bool(self._share_titles_fn())
        ctx: dict = {}
        sub = is_subagent_ref(session_ref)
        parent = self._parent_ref(session_ref, row) if sub else ""
        prow = row.get("_parent_row") or {}
        if sub:
            ctx["subagent"] = True
            if parent:
                ctx["parent_ref"] = parent
            if row.get("_fallback"):
                ctx["fallback"] = row["_fallback"]
        state = row.get("state") or prow.get("state")
        if state:
            ctx["state"] = str(state)
        ctx["is_live"] = bool(row.get("is_live") if row else prow.get("is_live"))
        if share:
            label = redact_cap(row.get("display_name") or row.get("first_message"),
                               CAP_LABEL)
            if sub and label:
                ctx["agent_label"] = label
            if sub and parent:
                if not prow:
                    prow = self._find_row(parent) or {}
                ptitle = redact_cap(prow.get("display_name"), CAP_TITLE)
                if ptitle:
                    ctx["parent_title"] = ptitle
            title = label or ""
            if sub and label:
                ptitle = ctx.get("parent_title")
                title = (f"{label} (sub-agent of {ptitle})" if ptitle
                         else f"{label} (sub-agent)")
            if title:
                ctx["title"] = redact_cap(title, CAP_TITLE)
            repo = redact_cap(row.get("folder_label") or prow.get("folder_label"),
                              CAP_LABEL)
            if repo:
                ctx["repo_label"] = repo
        else:
            ctx["title"] = f"Session on {_hostname()}"
        if row.get("question_waiting"):
            opts = []
            raw = row.get("question_options") or []
            if isinstance(raw, list):
                for o in raw[:MAX_OPTIONS]:
                    lab = o.get("label") if isinstance(o, dict) else o
                    opts.append({"label": redact_cap(lab, CAP_LABEL)})
            pending = {"type": "question", "options": opts}
            if share:
                pending["text"] = redact_cap(row.get("question_text"), CAP_QUESTION)
            ctx["pending"] = pending
        elif row.get("pending_tool") and (row.get("is_live") or sub):
            pending = {"type": "approval", "tool": str(row.get("pending_tool"))[:60]}
            act = _action_text(row.get("pending_tool"), row.get("pending_file"))
            if share and act:
                pending["action"] = redact_cap(act, CAP_SUMMARY)
            ctx["pending"] = pending
        return ctx

    def _find_row(self, session_ref) -> dict | None:
        # On-demand only (one session.detail command), cached archive feed,
        # projected to the fields needed.
        _, data = self.local_api.get("/api/sessions", query={
            "all": "1", "compact": "1", "fields": ",".join((
                "id", "session_id", "jsonl_path", "display_name", "first_message",
                "folder_label", "state", "is_live", "parent_session_id",
                "pending_tool", "pending_file", "question_waiting",
                "question_text", "question_options"))})
        rows = []
        if isinstance(data, dict):
            rows = data.get("sessions") or data.get("conversations") or []
        for row in rows:
            if isinstance(row, dict) and (row.get("id") == session_ref
                                          or row.get("session_id") == session_ref):
                return row
        return None

    def _find_jsonl(self, session_ref) -> str | None:
        row = self._find_row(session_ref)
        return row.get("jsonl_path") if row else None


def _detail_json_bytes(detail: dict) -> int:
    return len(json.dumps(detail, separators=(",", ":")).encode("utf-8"))


def _bounded_detail(session_ref: str, turns: list, truncated: bool = False,
                    context: dict | None = None) -> dict:
    """Trim oldest turns until the detail body fits DETAIL_MAX_BYTES."""
    turns = list(turns)

    def build():
        d = {"session_ref": session_ref}
        d.update(context or {})
        d["turns"] = turns
        return d

    detail = build()
    while turns and _detail_json_bytes(detail) > DETAIL_MAX_BYTES:
        turns.pop(0)
        truncated = True
        detail = build()
    detail["truncated"] = truncated
    return detail


def tail_read_turns(jsonl_path: str, max_turns: int = MAX_TURNS,
                    cap: int = CAP_TURN_TEXT, tail_bytes: int = 64 * 1024) -> list[dict]:
    """Read the last ~tail_bytes of a JSONL transcript and return the last
    `max_turns` role/text turns, each redacted and capped. Engine-agnostic and
    defensive: unknown line shapes are skipped."""
    try:
        size = os.path.getsize(jsonl_path)
        with open(jsonl_path, "rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                f.readline()  # drop partial first line after the seek
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    turns: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        role, text = _extract_turn(obj)
        if role is None or not text:
            continue
        turns.append({"role": role, "text": redact_cap(text, cap)})
    return turns[-max_turns:]


def _extract_turn(obj: dict):
    role = obj.get("role")
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else None
    if role is None and msg is not None:
        role = msg.get("role")
    if role is None:
        t = str(obj.get("type") or "").lower()
        if t in ("user", "assistant", "system"):
            role = t
    if role not in ("user", "assistant", "system"):
        return None, ""
    content = None
    if msg is not None:
        content = msg.get("content")
    if content is None:
        content = obj.get("content") or obj.get("text")
    text = _content_to_text(content)
    return role, text


def _content_to_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
                elif block.get("text"):
                    parts.append(str(block["text"]))
        return "\n".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# Relay HTTP client (device → relay)
# ---------------------------------------------------------------------------

def _url_origin(url: str) -> str:
    try:
        u = urllib.parse.urlsplit((url or "").strip())
        port = u.port or {"https": 443, "http": 80}.get((u.scheme or "").lower())
    except ValueError:
        return ""
    if not u.scheme or not u.hostname:
        return ""
    return f"{u.scheme.lower()}://{u.hostname.lower()}:{port}"


def _pin_relay_url(chosen: str, reported) -> str:
    """The origin the user chose is authoritative: a pairing answer naming a
    DIFFERENT origin must not redirect the device secret elsewhere (path /
    trailing-slash differences on the same origin are accepted)."""
    reported = (reported or "").strip().rstrip("/") if isinstance(reported, str) else ""
    if reported and _url_origin(reported) == _url_origin(chosen) \
            and validate_relay_url(reported):
        return reported
    return chosen.rstrip("/")


def default_relay_url() -> str:
    """User-configured default relay (CCC_CLOUD_SERVICE_URL), else "".
    Public CCC ships no baked-in service URL; the user types it or sets it."""
    url = (os.environ.get("CCC_CLOUD_SERVICE_URL") or "").strip().rstrip("/")
    return url if validate_relay_url(url) else ""


def signup_url(relay_url: str = "") -> str:
    """The configured service's own /signup page, or "" when no acceptable
    relay URL is known. Derived, never guessed."""
    base = validate_relay_url(relay_url) if (relay_url or "").strip() \
        else default_relay_url()
    return base + "/signup" if base else ""


def relay_url_acceptable(url: str) -> bool:
    return validate_relay_url(url) is not None


def validate_relay_url(url: str) -> str | None:
    """Return a normalized relay base URL, or None when it is unsafe.

    The device secret rides in the Authorization header of every relay call,
    so plain http is accepted only for a loopback relay (local staging);
    anything else must be https. No userinfo, query, or fragment."""
    url = (url or "").strip().rstrip("/")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        return None
    if scheme == "https":
        return url
    if scheme == "http" and host in ("127.0.0.1", "localhost", "::1"):
        return url
    return None


class RelayError(Exception):
    def __init__(self, code: str, status: int = 0, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.status = status


class RelayClient:
    """Outbound-only HTTP to the hosted relay. Holds device credentials in
    memory; the manager owns persistence."""

    def __init__(self, relay_url: str, device_id: str = "", device_secret: str = "",
                 timeout: float = 30.0):
        self.relay_url = (relay_url or "").rstrip("/")
        self.device_id = device_id
        self.device_secret = device_secret
        self.timeout = timeout
        self._pending_rotation_id = None  # set after rotate(), sent on next call

    # -- low-level request ---------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None,
                 auth: bool = False, timeout: float | None = None) -> dict:
        url = f"{self.relay_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization",
                           f"Bearer {self.device_id}.{self.device_secret}")
            if self._pending_rotation_id:
                req.add_header("X-CCC-Rotation-Confirm", self._pending_rotation_id)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                parsed = json.loads(raw) if raw else {}
                # A successful authed call confirms an in-flight rotation.
                if auth and self._pending_rotation_id:
                    self._pending_rotation_id = None
                return parsed if isinstance(parsed, dict) else {}
        except urllib.error.HTTPError as e:
            self._raise_http(e)
        except (OSError, ValueError) as e:
            raise RelayError("network_error", 0, str(e))
        return {}

    def _raise_http(self, e: urllib.error.HTTPError):
        try:
            parsed = json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            parsed = {}
        code = ""
        if isinstance(parsed, dict):
            code = parsed.get("error") or ""
        raise RelayError(code or f"http_{e.code}", e.code,
                         json.dumps(parsed)[:200] if parsed else "")

    # -- pairing (§3) --------------------------------------------------------

    def pair_complete(self, pair_code: str, platform: str, ccc_version: str,
                      capabilities: list, display_name: str) -> dict:
        return self._request("POST", "/v1/pair/complete", {
            "pair_code": pair_code,
            "platform": platform,
            "ccc_version": ccc_version,
            "capabilities": capabilities,
            "display_name": display_name,
        })

    def pair_abandon(self, device_id: str) -> dict:
        try:
            # The relay authenticates this call by device Bearer credentials
            # (a third party must not abandon someone else's just-issued
            # device); the body device_id is informational only.
            return self._request("POST", "/v1/pair/abandon",
                                 {"device_id": device_id}, auth=True)
        except RelayError:
            return {"ok": False}

    def self_revoke(self) -> dict:
        """Device-initiated tombstone (PROTOCOL Appendix A #2). Best-effort:
        callers wipe local credentials regardless of the outcome."""
        return self._request("POST", "/v1/device/self-revoke", {}, auth=True)

    # -- loop (§4) -----------------------------------------------------------

    def post_state(self, snapshot: dict) -> dict:
        return self._request("POST", "/v1/state", snapshot, auth=True, timeout=15.0)

    def poll(self, wait: int = POLL_WAIT_S) -> dict:
        return self._request("GET", f"/v1/poll?wait={int(wait)}", auth=True,
                             timeout=float(wait) + 10.0)

    def post_result(self, result: dict) -> dict:
        return self._request("POST", "/v1/result", result, auth=True, timeout=15.0)

    # -- rotation (§2) -------------------------------------------------------

    def rotate(self) -> dict:
        """Request a new secret. Returns {new_secret, rotation_id}. The caller
        MUST persist the new secret to disk BEFORE the next authed call (which
        carries X-CCC-Rotation-Confirm) invalidates the old one."""
        return self._request("POST", "/v1/device/rotate", {}, auth=True, timeout=15.0)

    def stage_rotation(self, new_secret: str, rotation_id: str) -> None:
        self.device_secret = new_secret
        self._pending_rotation_id = rotation_id


# ---------------------------------------------------------------------------
# Relay loop (two daemon threads + backoff)
# ---------------------------------------------------------------------------

def _backoff_next(base: float) -> tuple[float, float]:
    """Return (sleep_seconds, next_base). Exponential 1s→60s with ±20% jitter."""
    nxt = BACKOFF_MIN_S if base <= 0 else min(BACKOFF_MAX_S, base * 2.0)
    # jitter in [-0.2, +0.2] * nxt
    frac = (secrets.randbelow(4001) / 10000.0) - 0.2  # [-0.2, 0.2]
    sleep = max(0.25, nxt * (1.0 + frac))
    return sleep, nxt


class RelayLoop:
    def __init__(self, manager: "CloudRelayManager"):
        self.m = manager
        self._stop = threading.Event()
        self._push_now = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_push_ts = 0.0
        self._last_full_ts = 0.0
        self._last_digest = None
        self._unchanged_ok = False
        self._delta_ok = False
        self._last_snapshot = None
        self.state = "off"
        self.last_push = None
        self.last_poll = None
        self.last_push_error = None
        self.last_poll_error = None
        self._cred_rejects = 0
        self._fatal_lock = threading.Lock()

    @property
    def last_error(self):
        return self.last_push_error or self.last_poll_error

    def start(self):
        self._stop.clear()
        self.state = "connecting"
        t1 = threading.Thread(target=self._push_loop, daemon=True, name="ccc-cloud-push")
        t2 = threading.Thread(target=self._poll_loop, daemon=True, name="ccc-cloud-poll")
        self._threads = [t1, t2]
        t1.start()
        t2.start()

    def stop(self):
        self._stop.set()
        self._push_now.set()
        self.state = "off"

    def request_push(self):
        self._push_now.set()

    def _dead(self) -> bool:
        return self._stop.is_set() or cloud_disabled_env()

    def _handle_fatal(self, err: RelayError) -> bool:
        """Return True if the loop must stop permanently (revoked/deleted).

        ``bad_device_credentials`` (401: the service no longer knows this
        device/secret) is never transient under persist-before-confirm
        rotation, so a short run of them is treated like revocation instead
        of retrying forever."""
        reason = None
        if err.code in ("device_revoked", "account_deleted"):
            reason = err.code
        elif err.code == "bad_device_credentials":
            with self._fatal_lock:
                self._cred_rejects += 1
                if self._cred_rejects >= CREDENTIAL_REJECTS_FATAL:
                    reason = "credentials_rejected"
        if reason:
            self.m.mark_disabled(reason)
            self.state = "disabled"
            self.last_push_error = reason
            self.last_poll_error = reason
            self._stop.set()
            self._push_now.set()
            return True
        return False

    def _push_once(self):
        """Send one state push: a small heartbeat when the snapshot is
        unchanged and the service supports it, else the full snapshot."""
        snapshot = build_snapshot(self.m.local_api, self.m.config,
                                  self.m.ccc_version, self.m.paths)
        digest = snapshot_digest(snapshot)
        heartbeat = (self._unchanged_ok
                     and digest == self._last_digest
                     and time.time() - self._last_full_ts
                     < STATE_FULL_REFRESH_S)
        if heartbeat:
            resp = self.m.client.post_state(heartbeat_body(snapshot))
            if isinstance(resp, dict) and resp.get("need_full"):
                resp = self._post_full(snapshot, digest)
                heartbeat = False
        else:
            resp = None
            delta = None
            if (self._delta_ok and self._last_digest and self._last_snapshot
                    and time.time() - self._last_full_ts
                    < STATE_FULL_REFRESH_S):
                delta = build_delta(self._last_snapshot, snapshot,
                                    self._last_digest, digest)
                if delta is not None and (
                        len(json.dumps(delta, default=str))
                        >= len(json.dumps(snapshot, default=str))):
                    delta = None
            if delta is not None:
                resp = self.m.client.post_state(delta)
                if isinstance(resp, dict) and resp.get("need_full"):
                    resp = None
                else:
                    self._last_digest = digest
                    self._last_snapshot = snapshot
                    self._delta_ok = bool(isinstance(resp, dict)
                                          and resp.get("delta_ok"))
                    self._unchanged_ok = bool(isinstance(resp, dict)
                                              and resp.get("unchanged_ok"))
                    return resp
            resp = self._post_full(snapshot, digest)
        return resp

    def _post_full(self, snapshot, digest):
        """Full push (carries its digest so the cloud can verify deltas)."""
        resp = self.m.client.post_state(dict(snapshot, digest=digest))
        self._last_full_ts = time.time()
        self._last_digest = digest
        self._last_snapshot = snapshot
        self._unchanged_ok = bool(isinstance(resp, dict)
                                  and resp.get("unchanged_ok"))
        self._delta_ok = bool(isinstance(resp, dict)
                              and resp.get("delta_ok"))
        return resp

    def _push_loop(self):
        base = 0.0
        while not self._dead():
            # Coalesce: honour a 3s floor between pushes even on-demand.
            since = time.time() - self._last_push_ts
            if since < STATE_PUSH_MIN_INTERVAL_S:
                self._stop.wait(STATE_PUSH_MIN_INTERVAL_S - since)
            if self._dead():
                break
            try:
                resp = self._push_once()
                self._last_push_ts = time.time()
                self.last_push = _iso_now()
                self.last_push_error = None
                self._cred_rejects = 0
                self.state = "online"
                base = 0.0
                # queued>0 hints pending commands; nudge the poller.
                if isinstance(resp, dict) and resp.get("queued"):
                    self._push_now.clear()
                self._maybe_rotate(resp)
            except RelayError as e:
                if self._handle_fatal(e):
                    break
                self.last_push_error = e.code
                self._last_digest = None  # next push after a failure is full
                self._last_snapshot = None
                self.state = "error" if self.state == "online" else "offline"
                sleep, base = _backoff_next(base)
                self._stop.wait(sleep)
                continue
            # Wait up to the push interval, but wake early on an on-demand push.
            self._push_now.wait(STATE_PUSH_INTERVAL_S)
            self._push_now.clear()

    def _poll_loop(self):
        base = 0.0
        while not self._dead():
            try:
                resp = self.m.client.poll(POLL_WAIT_S)
                self.last_poll = _iso_now()
                self.last_poll_error = None
                self._cred_rejects = 0
                base = 0.0
                commands = resp.get("commands") if isinstance(resp, dict) else None
                for env in (commands or []):
                    if self._dead():
                        break
                    self._run_command(env)
            except RelayError as e:
                if self._handle_fatal(e):
                    break
                self.last_poll_error = e.code
                sleep, base = _backoff_next(base)
                self._stop.wait(sleep)

    def _run_command(self, env):
        try:
            result = self.m.executor.execute(env)
        except Exception as e:  # never let one bad command kill the loop
            rid = ""
            if isinstance(env, dict):
                rid = str(env.get("request_id") or "")
            result = {
                "request_id": rid, "status": "error",
                "error_code": "invalid_payload", "detail_code": None,
                "completed_at": _iso_now(), "internal": str(e)[:120],
            }
        try:
            self.m.client.post_result({k: v for k, v in result.items()
                                       if k != "internal"})
        except RelayError as e:
            if not self._handle_fatal(e):
                self.last_poll_error = e.code

    def _maybe_rotate(self, resp):
        """Rotate the device secret when the relay asks. Persist the new secret
        BEFORE the next authed call confirms it (§2)."""
        if not isinstance(resp, dict) or not resp.get("rotate_requested"):
            return
        try:
            r = self.m.client.rotate()
        except RelayError:
            return
        new_secret = r.get("new_secret")
        rotation_id = r.get("rotation_id")
        if not new_secret or not rotation_id:
            return
        # Persist first; only then stage the confirm header for the next call.
        self.m.persist_rotated_secret(new_secret)
        self.m.client.stage_rotation(new_secret, rotation_id)


# ---------------------------------------------------------------------------
# Manager — the single object server.py talks to
# ---------------------------------------------------------------------------

class CloudRelayManager:
    def __init__(self, port: int, ccc_version: str = "0.0.0",
                 state_dir: str | None = None):
        self.port = int(port)
        self.ccc_version = ccc_version
        self.paths = CloudPaths(state_dir)
        self.config = load_config(self.paths)
        self.local_api = LocalAPI(self.port)
        self.store = IdempotencyStore(self.paths.idempotency)
        self.executor = CommandExecutor(
            self.local_api, self.store, self.paths,
            share_titles_fn=lambda: bool(self.config.get("share_titles", True)))
        self.client: RelayClient | None = None
        self.loop: RelayLoop | None = None
        self._loop_lock_fd = None  # flock held while this process owns the loop
        self._pending = None  # in-memory pending pair creds (pre-confirm)
        self._lock = threading.RLock()

    # -- status / config -----------------------------------------------------

    def status(self) -> dict:
        creds = load_credentials(self.paths)
        loop_state = {
            "state": self.loop.state if self.loop else "off",
            "last_push": self.loop.last_push if self.loop else None,
            "last_poll": self.loop.last_poll if self.loop else None,
            # Per-loop errors so a healthy push can't mask a broken poll.
            "last_push_error": self.loop.last_push_error if self.loop else None,
            "last_poll_error": self.loop.last_poll_error if self.loop else None,
            "last_error": self.loop.last_error if self.loop else None,
        }
        return {
            "enabled": bool(self.config.get("enabled")),
            "paired": bool(creds),
            "device_id": self.config.get("device_id") or "",
            "account_email_masked": self.config.get("account_email_masked") or "",
            "relay_url": self.config.get("relay_url") or "",
            "default_relay_url": default_relay_url(),
            "signup_url": signup_url(self.config.get("relay_url") or ""),
            "protocol_version": PROTOCOL_VERSION,
            "share_titles": bool(self.config.get("share_titles", True)),
            "disabled_reason": self.config.get("disabled_reason") or "",
            "env_disabled": cloud_disabled_env(),
            "loop": loop_state,
        }

    def get_config(self) -> dict:
        return {
            "enabled": bool(self.config.get("enabled")),
            "relay_url": self.config.get("relay_url") or "",
            "share_titles": bool(self.config.get("share_titles", True)),
            "paired": bool(load_credentials(self.paths)),
        }

    def set_config(self, enabled=None, share_titles=None) -> dict:
        with self._lock:
            if share_titles is not None:
                self.config["share_titles"] = bool(share_titles)
            if enabled is not None:
                enabled = bool(enabled)
                if enabled and not load_credentials(self.paths):
                    save_config(self.paths, self.config)
                    return {"ok": False, "error": "not_paired"}
                self.config["enabled"] = enabled
                if enabled:
                    self.config["disabled_reason"] = ""
            save_config(self.paths, self.config)
            # Reconcile the loop with the new state.
            if self.config.get("enabled") and load_credentials(self.paths):
                self._start_loop()
            else:
                self._stop_loop()
            if share_titles is not None and self.loop:
                self.loop.request_push()  # push the relabelled snapshot promptly
            return {"ok": True, **self.get_config()}

    # -- pairing -------------------------------------------------------------

    def start_pairing(self, pair_code: str, relay_url: str) -> dict:
        pair_code = (pair_code or "").strip()
        if not pair_code:
            return {"ok": False, "error": "pair_code_invalid"}
        relay_url = (relay_url or "").strip() or default_relay_url()
        if not relay_url:
            return {"ok": False, "error": "relay_url_required"}
        relay_url = validate_relay_url(relay_url)
        if not relay_url:
            # The device secret travels on this connection: never over
            # plaintext to a non-loopback host.
            return {"ok": False, "error": "relay_url_insecure"}
        client = RelayClient(relay_url)
        try:
            resp = client.pair_complete(
                pair_code=pair_code,
                platform=_platform(),
                ccc_version=self.ccc_version,
                capabilities=list(DEVICE_CAPABILITIES),
                display_name=machine_label(),
            )
        except RelayError as e:
            code = e.code if e.code in (
                "pair_code_invalid", "pair_code_expired", "rate_limited",
                "plan_limit_machines") else "pair_failed"
            return {"ok": False, "error": code}
        masked = resp.get("account_email_masked") or ""
        device_id = resp.get("device_id") or ""
        device_secret = resp.get("device_secret") or ""
        if not device_id or not device_secret:
            return {"ok": False, "error": "pair_failed"}
        base_url = _pin_relay_url(relay_url, resp.get("relay_base_url"))
        # Hold in memory only — nothing persisted until the user confirms.
        with self._lock:
            self._pending = {
                "relay_url": base_url,
                "device_id": device_id,
                "device_secret": device_secret,
                "account_email_masked": masked or mask_email(resp.get("account_email") or ""),
                "account_ref": resp.get("account_ref") or "",
            }
        return {"ok": True, "masked_email": self._pending["account_email_masked"],
                "device_id": device_id}

    def confirm_pairing(self) -> dict:
        with self._lock:
            if not self._pending:
                return {"ok": False, "error": "not_paired"}
            p = self._pending
            save_credentials(self.paths, {
                "device_id": p["device_id"],
                "device_secret": p["device_secret"],
            })
            self.config.update({
                "enabled": True,
                "relay_url": p["relay_url"],
                "device_id": p["device_id"],
                "account_email_masked": p["account_email_masked"],
                "account_ref": p["account_ref"],
                "disabled_reason": "",
            })
            save_config(self.paths, self.config)
            self._pending = None
            audit_append(self.paths, "", "pairing", "", "paired")
            self._start_loop()
            return {"ok": True, **self.status()}

    def abandon_pairing(self) -> dict:
        with self._lock:
            p = self._pending
            self._pending = None
        if p:
            RelayClient(p["relay_url"], p["device_id"], p["device_secret"]).pair_abandon(
                p["device_id"])
            audit_append(self.paths, "", "pairing", "", "abandoned")
        return {"ok": True}

    def unpair(self) -> dict:
        with self._lock:
            self._stop_loop()
            creds = load_credentials(self.paths)
            relay_url = validate_relay_url(self.config.get("relay_url") or "")
            if creds and relay_url:
                # Tell the relay to revoke this device, best-effort; local
                # credentials are wiped whether or not the call lands.
                try:
                    RelayClient(relay_url, creds["device_id"],
                                creds["device_secret"]).self_revoke()
                except (RelayError, KeyError, OSError):
                    pass
            wipe_credentials(self.paths)
            self.config.update({
                "enabled": False,
                "device_id": "",
                "account_email_masked": "",
                "account_ref": "",
                "disabled_reason": "unpaired",
            })
            save_config(self.paths, self.config)
            audit_append(self.paths, "", "unpair", "", "credentials_wiped")
            return {"ok": True}

    # -- loop lifecycle ------------------------------------------------------

    def maybe_start(self) -> bool:
        """Start the loop iff enabled, paired, and not env-disabled. Called
        from server main()."""
        if cloud_disabled_env():
            return False
        creds = load_credentials(self.paths)
        if self.config.get("enabled") and creds:
            self._start_loop()
            return True
        return False

    def _start_loop(self):
        if cloud_disabled_env():
            return
        creds = load_credentials(self.paths)
        if not creds:
            return
        if self.loop and self.loop.state not in ("off", "disabled"):
            return  # already running
        relay_url = validate_relay_url(self.config.get("relay_url") or "")
        if not relay_url:
            self.config["disabled_reason"] = "relay_url_insecure"
            return
        # One loop per machine: a second CCC process on the same state dir (a
        # preview/branch server, a sibling port) must not push and poll with
        # the same device credentials.
        if not self._acquire_loop_lock():
            self.config["disabled_reason"] = "another_ccc_process_owns_relay"
            return
        self.client = RelayClient(
            self.config.get("relay_url") or "",
            creds["device_id"], creds["device_secret"],
        )
        self.loop = RelayLoop(self)
        self.loop.start()

    def _acquire_loop_lock(self) -> bool:
        if self._loop_lock_fd is not None:
            return True
        if fcntl is None or not hasattr(fcntl, "flock"):
            return True
        try:
            self.paths.ensure()
            fd = os.open(self.paths.loop_lock, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._loop_lock_fd = fd
        return True

    def _release_loop_lock(self):
        fd, self._loop_lock_fd = self._loop_lock_fd, None
        if fd is None:
            return
        try:
            if fcntl is not None and hasattr(fcntl, "flock"):
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def _stop_loop(self):
        if self.loop:
            self.loop.stop()
            self.loop = None
        self._release_loop_lock()

    def mark_disabled(self, reason: str):
        """Called from the loop threads when the service refuses this
        machine. For a service-side disconnect (revoked, account deleted,
        credentials rejected) the stored credential can never work again:
        wipe it like a local unpair so the settings panel shows the reason
        next to a fresh pairing form. Keeps relay_url so re-pairing is one
        code away. Runs on the loop thread: must not join the loop."""
        with self._lock:
            already = (self.config.get("disabled_reason") == reason
                       and not self.config.get("enabled"))
            self.config["enabled"] = False
            self.config["disabled_reason"] = reason
            if reason in SERVICE_DISCONNECT_REASONS:
                wipe_credentials(self.paths)
                self.config.update({"device_id": "", "account_email_masked": "",
                                    "account_ref": ""})
            save_config(self.paths, self.config)
            if not already:
                audit_append(self.paths, "", "loop", "", f"disabled:{reason}")
        self._release_loop_lock()

    def persist_rotated_secret(self, new_secret: str):
        creds = load_credentials(self.paths) or {}
        creds["device_secret"] = new_secret
        if self.config.get("device_id"):
            creds.setdefault("device_id", self.config["device_id"])
        save_credentials(self.paths, creds)


# ---------------------------------------------------------------------------
# Self-test — in-process stub relay, no network
# ---------------------------------------------------------------------------

class _StubLocalAPI:
    """Counts inject/answer dispatches so the idempotency test can prove the
    executor body runs exactly once for a duplicated request_id."""

    def __init__(self):
        self.calls = 0
        self.sessions = [{
            "id": "sess-1", "display_name": "Fix /Users/alice/proj login bug",
            "engine": "claude", "is_live": True, "mtime": time.time(),
            "state": "waiting", "question_waiting": True,
            "question_text": "Deploy to prod at /home/bob/deploy now? token abcdef0123456789abcd",
            "question_options": ["Yes", "No"], "folder_label": "myrepo",
            "jsonl_path": "/nonexistent/sess-1.jsonl",
        }]

    def get(self, path, query=None):
        if path == "/api/sessions":
            return 200, {"ok": True, "sessions": self.sessions}
        if path == "/api/attention":
            return 200, {"ok": True, "items": [{
                "kind": "soft_block", "priority": 1, "session_id": "sess-1",
                "question_text": "waiting on you: email me at bob@example.com",
                "mtime": time.time(),
            }]}
        if path == "/api/ux-fixes/health":
            return 200, {"ok": True, "queues": [{
                "queue": "UX", "depth": 2, "total": 5, "workers": 1,
                "state": "draining", "oldest_open_age_seconds": 1200,
            }]}
        return 200, {"ok": True}

    def call(self, method, path, body=None, query=None, timeout=None):
        return self.get(path, query) if method == "GET" else self.post(path, body)

    def post(self, path, body=None):
        self.calls += 1
        return 200, {"ok": True}


def _selftest() -> int:
    import tempfile
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))

    # 1. redaction
    r = redact("path /Users/alice/secret and email a@b.com and tok deadbeefdeadbeef1234abcd")
    check("redact_path", "/Users/alice" not in r)
    check("redact_email", "a@b.com" not in r)
    check("redact_token", "deadbeefdeadbeef1234abcd" not in r)
    check("redact_keeps_words", "path" in r and "email" in r)

    # 2. truncation caps
    check("cap_title", len(redact_cap("x" * 500, CAP_TITLE)) <= CAP_TITLE)
    check("cap_summary", len(redact_cap("y" * 900, CAP_SUMMARY)) <= CAP_SUMMARY)
    check("cap_question", len(redact_cap("z" * 5000, CAP_QUESTION)) <= CAP_QUESTION)

    tmp = tempfile.mkdtemp(prefix="ccc-cloud-selftest-")
    paths = CloudPaths(tmp)
    paths.ensure()
    store = IdempotencyStore(paths.idempotency)
    stub = _StubLocalAPI()
    execu = CommandExecutor(stub, store, paths)

    now = time.time()

    def env(cap, seq, request_id, expires_delta=300, session_ref="sess-1", text="hi"):
        return {
            "v": 1, "request_id": request_id, "capability": cap,
            "created": _iso_from_epoch(now), "expires": _iso_from_epoch(now + expires_delta),
            "seq": seq, "idempotency_key": request_id,
            "payload": {"session_ref": session_ref, "text": text},
        }

    # 3. expiry
    check("expired", validate_command(
        env("session.send_input", 10, "r-exp", expires_delta=-120), 0) == "expired")

    # 4. seq is NOT a rejection gate (C1 fix): a below-high-water-mark seq on a
    # new request_id must still validate — the relay legitimately re-queues an
    # earlier, never-delivered command after a later one advanced the mark.
    store.set_last_seq(50)
    check("seq_lower_not_replay", validate_command(
        env("session.send_input", 50, "r-lower"), store.get_last_seq()) is None)
    check("seq_ok", validate_command(
        env("session.send_input", 51, "r-ok"), store.get_last_seq()) is None)

    # 5. unknown capability
    check("unknown_cap", validate_command(
        env("session.stop", 999, "r-cap"), 0) == "unknown_capability")

    # 6. idempotency dedup — same request_id executes once, replays recorded result
    store2 = IdempotencyStore(os.path.join(tmp, "idem2.sqlite3"))
    stub2 = _StubLocalAPI()
    ex2 = CommandExecutor(stub2, store2, paths)
    e1 = env("session.send_input", 100, "dup-1")
    res_a = ex2.execute(e1)
    res_b = ex2.execute(e1)  # duplicate
    check("idem_runs_once", stub2.calls == 1)
    check("idem_same_result", res_a == res_b)
    check("idem_status_ok", res_a.get("status") == "ok")

    # 7. expiry path through executor returns status expired
    store3 = IdempotencyStore(os.path.join(tmp, "idem3.sqlite3"))
    ex3 = CommandExecutor(_StubLocalAPI(), store3, paths)
    res_exp = ex3.execute(env("session.send_input", 5, "e-1", expires_delta=-500))
    check("exec_expired_status", res_exp.get("status") == "expired")

    # 8. snapshot minimization
    cfg_on = dict(_CONFIG_DEFAULTS, share_titles=True)
    snap_on = build_snapshot(_StubLocalAPI(), cfg_on)
    s0 = snap_on["sessions"][0]
    check("snap_title_present", s0["title_source"] == "redacted")
    check("snap_title_redacted", "/Users/alice" not in s0["title"])
    check("snap_repo_present", "repo_label" in s0)
    check("snap_question_redacted",
          "abcdef0123456789abcd" not in snap_on["questions"][0]["text"])
    check("snap_attention_redacted",
          "bob@example.com" not in snap_on["attention"][0]["summary"])
    check("snap_workers", snap_on["workers"][0]["state"] in QUEUE_STATE_ENUM)

    cfg_off = dict(_CONFIG_DEFAULTS, share_titles=False)
    snap_off = build_snapshot(_StubLocalAPI(), cfg_off)
    s0off = snap_off["sessions"][0]
    check("snap_opaque_title", s0off["title_source"] == "opaque"
          and s0off["title"].startswith("Session on "))
    check("snap_opaque_no_repo", "repo_label" not in s0off)

    # 9. env kill switch gate
    prev = os.environ.get("CCC_CLOUD_DISABLED")
    os.environ["CCC_CLOUD_DISABLED"] = "1"
    check("env_disabled_true", cloud_disabled_env() is True)
    if prev is None:
        del os.environ["CCC_CLOUD_DISABLED"]
    else:
        os.environ["CCC_CLOUD_DISABLED"] = prev
    check("env_disabled_false", cloud_disabled_env() is False)

    # report
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="CCC Cloud Relay local client")
    ap.add_argument("--selftest", action="store_true",
                    help="run in-process stub-relay self-test")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    print("ccc_server/cloud_relay.py: imported by server.py; run with --selftest to verify.")
