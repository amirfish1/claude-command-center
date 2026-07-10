"""CCC Cloud Relay — local (device) client.

Stdlib-only implementation of the device side of the CCC Cloud Relay protocol
(docs/cloud-relay/PROTOCOL.md, §2/§3/§4/§7). The device only ever makes
OUTBOUND HTTPS requests: it pushes a minimized state snapshot and long-polls
for commands, then validates and executes every command itself against its own
loopback CCC API — exactly as a local caller would. The relay is not a proxy.

Design rules honoured here:

- No inbound listener; no pip deps (urllib/http.client/json/sqlite3/hashlib/
  hmac/secrets/threading/re/os/time/datetime/socket only).
- Disabled by default. Enabling and pairing are localhost-origin-gated in
  server.py; this module never widens network trust, never touches
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

import hashlib  # noqa: F401  (available for future signed calls; kept per spec import set)
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
CAP_LABEL = 120
CAP_TURN_TEXT = 2000
MAX_SESSIONS = 200
MAX_ATTENTION = 50
MAX_QUEUES = 50
MAX_SCHEDULES = 50
MAX_QUESTIONS = 50
MAX_OPTIONS = 4
MAX_TURNS = 20

# Command validation (PROTOCOL §4.3).
ALLOWED_CAPABILITIES = (
    "session.send_input",
    "question.answer",
    "session.wake",
    "session.detail",
)
COMMAND_MAX_LIFETIME_S = 600      # 10 min
COMMAND_SKEW_GRACE_S = 60         # clock-skew grace
COMMAND_TEXT_MAX_BYTES = 16 * 1024  # 16 KiB input cap

# Loop timing (PROTOCOL §7).
STATE_PUSH_INTERVAL_S = 20.0
STATE_PUSH_MIN_INTERVAL_S = 3.0   # coalesce on-demand pushes
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

    def ensure(self) -> None:
        os.makedirs(self.dir, exist_ok=True)


# ---------------------------------------------------------------------------
# Redaction & truncation (PROTOCOL §4.1 / data-classification "class E")
# ---------------------------------------------------------------------------

_RE_URL_USERINFO = re.compile(r"\bhttps?://[^\s/@]+@[^\s]+", re.IGNORECASE)
_RE_PATH_POSIX = re.compile(
    r"(?:/Users/|/home/|/private/|/var/|/tmp/|/opt/|/etc/)[^\s\"'<>|]+"
)
_RE_PATH_WIN = re.compile(r"[A-Za-z]:\\[^\s\"'<>|]+")
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_RE_HEX = re.compile(r"\b[0-9a-fA-F]{20,}\b")
# base64/urlsafe-ish token >=20 chars that carries at least one digit AND one
# letter (so plain long words are not scrubbed).
_RE_TOKEN = re.compile(
    r"\b(?=[A-Za-z0-9+/_=\-]*\d)(?=[A-Za-z0-9+/_=\-]*[A-Za-z])[A-Za-z0-9+/_=\-]{20,}\b"
)

_REDACTED = "[redacted]"


def redact(text) -> str:
    """Strip things that look like absolute paths, URLs with credentials,
    emails, and long hex/base64 tokens. Order matters: URLs-with-userinfo and
    paths first (they can contain @ / hex), then emails, then bare tokens."""
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
    k = str(kind or "").strip().lower()
    if k in ATTENTION_KIND_ENUM:
        return k
    if "question" in k or "soft_block" in k or "waiting" in k or "ask" in k:
        return "question"
    if "approval" in k or "permission" in k:
        return "approval"
    if "fail" in k or "error" in k:
        return "failed"
    if "complete" in k or "done" in k or "pushed" in k or "committed" in k:
        return "completed"
    if "offline" in k:
        return "offline"
    return "stuck"


def _norm_queue_state(state) -> str:
    s = str(state or "").strip().lower()
    return s if s in QUEUE_STATE_ENUM else "backlog"


def build_snapshot(local_api: LocalAPI, config: dict, ccc_version: str = "0.0.0") -> dict:
    """Pull local read models over loopback, minimize + cap, and assemble the
    §4.1 state body. `share_titles` off replaces titles/repo labels with opaque
    'Session on <machine>' labels."""
    share_titles = bool(config.get("share_titles", True))
    host = _hostname()
    m_label = machine_label()

    # --- sessions ---
    _, s_data = local_api.get("/api/sessions", query={"all": "1"})
    rows = []
    if isinstance(s_data, dict):
        rows = s_data.get("sessions") or s_data.get("conversations") or []
    sessions = []
    questions = []
    for row in rows[:MAX_SESSIONS]:
        if not isinstance(row, dict):
            continue
        ref = row.get("id") or row.get("session_id") or ""
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
            "recency": _iso_from_epoch(row.get("mtime")),
            "context_pct": row.get("context_pct"),
            "machine_label": m_label,
            "question_waiting": q_waiting,
        }
        if repo_label is not None:
            session["repo_label"] = repo_label
        sessions.append(session)

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
    _, a_data = local_api.get("/api/attention", query={"scope": "recent"})
    items = a_data.get("items") if isinstance(a_data, dict) else None
    attention = []
    for it in (items or [])[:MAX_ATTENTION]:
        if not isinstance(it, dict):
            continue
        summary = it.get("summary") or it.get("question_text") or ""
        attention.append({
            "kind": _map_attention_kind(it.get("kind")),
            "priority": int(it.get("priority") or 9),
            "session_ref": it.get("session_id") or it.get("session_ref") or "",
            "summary": redact_cap(summary, CAP_SUMMARY),
            "created": _iso_from_epoch(it.get("mtime")) or _iso_now(),
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
            "depth": int(q.get("total") or 0),        # total items
            "open": int(q.get("depth") or 0),         # open (unclosed) count
            "workers_live": int(q.get("workers") or 0),
            "state": _norm_queue_state(q.get("state")),
            "oldest_open_age_s": q.get("oldest_open_age_seconds"),
        })

    # --- schedules (omitted entirely when none found) ---
    schedules = collect_schedules()

    capabilities = ["sessions", "attention", "workers", "questions"]
    if schedules:
        capabilities.append("schedules")
    capabilities += ["session_input", "question_answer", "session_wake", "session_detail"]

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
    return snapshot


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
        seq = int(env.get("seq"))
    except (TypeError, ValueError):
        return "invalid_payload"
    if seq <= int(last_seq):
        return "replay"

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
    def __init__(self, local_api, store: IdempotencyStore, paths: CloudPaths | None = None):
        self.local_api = local_api
        self.store = store
        self.paths = paths

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
        if env.get("v") != PROTOCOL_VERSION:
            return self._reject(request_id, capability, session_ref, "invalid_payload")
        if capability not in ALLOWED_CAPABILITIES:
            return self._reject(request_id, capability, session_ref, "unknown_capability")

        # --- idempotency short-circuit (a retried request_id carries the SAME
        # seq it did on first execution, so it must return the recorded result
        # BEFORE the strictly-greater seq check would wrongly flag it replay). ---
        recorded = self.store.get_result(request_id)
        if recorded is not None:
            self._audit(request_id, capability, session_ref, "duplicate")
            return recorded

        # --- ordered validation for a genuinely new command ---
        err = validate_command(env, self.store.get_last_seq())
        if err is not None:
            return self._reject(request_id, capability, session_ref, err)

        # --- dispatch via loopback (§4.5) ---
        result = self._dispatch(env, capability, session_ref)
        # Consume the sequence and persist the terminal result (idempotent).
        try:
            self.store.set_last_seq(int(env.get("seq")))
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
            return self._answer(request_id, session_ref, payload)
        if capability == "session.detail":
            return self._detail(request_id, session_ref)
        return self._result(request_id, "rejected", error_code="unknown_capability")

    def _inject(self, request_id, session_ref, text, mode, detail_ok="delivered") -> dict:
        status, data = self.local_api.post("/api/inject-input", {
            "session_id": session_ref,
            "text": text,
            "mode": mode,
            "origin": "cloud-relay",
        })
        return self._from_inject(request_id, status, data, default_detail=detail_ok)

    def _answer(self, request_id, session_ref, payload) -> dict:
        # Prefer the relayed-question path; fall back to answer-mode injection.
        answers = payload.get("answers")
        if not isinstance(answers, list) or not answers:
            ans_text = payload.get("answer") or payload.get("text") or ""
            answers = [{"index": 0, "text": ans_text}]
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
        if isinstance(data, dict):
            err = str(data.get("error") or "")
        code = self._error_code(status_code, err)
        return self._result(request_id, "error", error_code=code)

    @staticmethod
    def _error_code(status_code, err) -> str:
        low = (err or "").lower()
        if status_code == 409 or "handoff" in low or "lease" in low:
            return "handoff_leased"
        if "no session" in low or "not found" in low or "missing session" in low:
            return "session_not_found"
        if "invalid" in low or "missing" in low:
            return "invalid_payload"
        return "invalid_payload"

    def _detail(self, request_id, session_ref) -> dict:
        """Assemble a bounded recent-turn window by tail-reading the session's
        own transcript (≤20 turns, ≤2000 chars each, redacted)."""
        jsonl_path = self._find_jsonl(session_ref)
        if not jsonl_path:
            return self._result(request_id, "error", error_code="session_not_found")
        turns = tail_read_turns(jsonl_path, max_turns=MAX_TURNS, cap=CAP_TURN_TEXT)
        return self._result(request_id, "ok", detail_code="delivered",
                            extra={"detail": {"session_ref": session_ref, "turns": turns}})

    def _find_jsonl(self, session_ref) -> str | None:
        _, data = self.local_api.get("/api/sessions", query={"all": "1"})
        rows = []
        if isinstance(data, dict):
            rows = data.get("sessions") or data.get("conversations") or []
        for row in rows:
            if isinstance(row, dict) and (row.get("id") == session_ref
                                          or row.get("session_id") == session_ref):
                return row.get("jsonl_path")
        return None


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
            return self._request("POST", "/v1/pair/abandon", {"device_id": device_id})
        except RelayError:
            return {"ok": False}

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
        self.state = "off"
        self.last_push = None
        self.last_poll = None
        self.last_error = None

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
        """Return True if the loop must stop permanently (revoked/deleted)."""
        if err.code in ("device_revoked", "account_deleted"):
            reason = err.code
            self.m.mark_disabled(reason)
            self.state = "disabled"
            self.last_error = reason
            self._stop.set()
            self._push_now.set()
            return True
        return False

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
                snapshot = build_snapshot(self.m.local_api, self.m.config,
                                          self.m.ccc_version)
                resp = self.m.client.post_state(snapshot)
                self._last_push_ts = time.time()
                self.last_push = _iso_now()
                self.state = "online"
                base = 0.0
                # queued>0 hints pending commands; nudge the poller.
                if isinstance(resp, dict) and resp.get("queued"):
                    self._push_now.clear()
                self._maybe_rotate(resp)
            except RelayError as e:
                if self._handle_fatal(e):
                    break
                self.last_error = e.code
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
                base = 0.0
                commands = resp.get("commands") if isinstance(resp, dict) else None
                for env in (commands or []):
                    if self._dead():
                        break
                    self._run_command(env)
            except RelayError as e:
                if self._handle_fatal(e):
                    break
                self.last_error = e.code
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
                self.last_error = e.code

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
        self.executor = CommandExecutor(self.local_api, self.store, self.paths)
        self.client: RelayClient | None = None
        self.loop: RelayLoop | None = None
        self._pending = None  # in-memory pending pair creds (pre-confirm)
        self._lock = threading.RLock()

    # -- status / config -----------------------------------------------------

    def status(self) -> dict:
        creds = load_credentials(self.paths)
        loop_state = {
            "state": self.loop.state if self.loop else "off",
            "last_push": self.loop.last_push if self.loop else None,
            "last_poll": self.loop.last_poll if self.loop else None,
            "last_error": self.loop.last_error if self.loop else None,
        }
        return {
            "enabled": bool(self.config.get("enabled")),
            "paired": bool(creds),
            "device_id": self.config.get("device_id") or "",
            "account_email_masked": self.config.get("account_email_masked") or "",
            "relay_url": self.config.get("relay_url") or "",
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
        relay_url = (relay_url or "").strip().rstrip("/")
        if not pair_code or not relay_url:
            return {"ok": False, "error": "pair_code_invalid"}
        client = RelayClient(relay_url)
        try:
            resp = client.pair_complete(
                pair_code=pair_code,
                platform=_platform(),
                ccc_version=self.ccc_version,
                capabilities=["sessions", "attention", "workers", "questions",
                              "session_input", "question_answer", "session_wake",
                              "session_detail"],
                display_name=machine_label(),
            )
        except RelayError as e:
            code = e.code if e.code in (
                "pair_code_invalid", "pair_code_expired", "rate_limited") else "pair_failed"
            return {"ok": False, "error": code}
        masked = resp.get("account_email_masked") or ""
        device_id = resp.get("device_id") or ""
        device_secret = resp.get("device_secret") or ""
        if not device_id or not device_secret:
            return {"ok": False, "error": "pair_failed"}
        # Hold in memory only — nothing persisted until the user confirms.
        with self._lock:
            self._pending = {
                "relay_url": resp.get("relay_base_url") or relay_url,
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
            wipe_credentials(self.paths)
            self.config.update({
                "enabled": False,
                "device_id": "",
                "account_email_masked": "",
                "account_ref": "",
                "disabled_reason": "unpaired",
            })
            save_config(self.paths, self.config)
            # Device-side revoke: the protocol's device auth has no self-revoke
            # endpoint (revocation is browser-facing via /v1/devices/<id>/revoke),
            # so we wipe local credentials and note it. The cloud row is revoked
            # from the web privacy center.
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
        self.client = RelayClient(
            self.config.get("relay_url") or "",
            creds["device_id"], creds["device_secret"],
        )
        self.loop = RelayLoop(self)
        self.loop.start()

    def _stop_loop(self):
        if self.loop:
            self.loop.stop()
            self.loop = None

    def mark_disabled(self, reason: str):
        with self._lock:
            self.config["enabled"] = False
            self.config["disabled_reason"] = reason
            save_config(self.paths, self.config)
            audit_append(self.paths, "", "loop", "", f"disabled:{reason}")

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
    r = redact("path /Users/amir/secret and email a@b.com and tok deadbeefdeadbeef1234abcd")
    check("redact_path", "/Users/amir" not in r)
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

    # 4. seq replay (new request_id, stale seq)
    store.set_last_seq(50)
    check("seq_replay", validate_command(
        env("session.send_input", 50, "r-replay"), store.get_last_seq()) == "replay")
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
    print("cloud_relay.py — import from server.py; run with --selftest to verify.")
