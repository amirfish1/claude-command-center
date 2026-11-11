# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""On-call state: one config file, one SQLite file, the shared message types.

Layout under ``<state dir>/oncall/``:

    config.json      what the person chose (0600). Never holds a secret value:
                     a field named ``<x>_vault`` names a CCC Vault entry.
    oncall.sqlite3   alerts, deliveries, inbound messages, reports, customer
                     messages, cursors and a bounded activity log.

Every function takes the root explicitly so tests use a temp directory and the
live files are never touched. Stdlib-only; no side effects at import.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets as _secrets
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_VERSION = 1
CONFIG_NAME = "config.json"
DB_NAME = "oncall.sqlite3"
LOG_KEEP = 500

CHANNEL_TYPES = ("email", "telegram", "ntfy", "imessage", "sms", "whatsapp", "command", "slack", "discord", "matrix", "mattermost")
SOURCE_TYPES = ("ci", "github", "sentry", "posthog", "http", "command")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_QUEUE_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# -- shared message types ---------------------------------------------------

@dataclass
class Outbound:
    """One message to send. ``to`` empty means the channel's owner address."""
    text: str
    to: str = ""
    subject: str = ""
    thread: str = ""      # opaque token; a reply that carries it is trusted
    reply_to: str = ""    # provider id of an earlier message to thread under
    choices: tuple = ()   # short fixed answers a channel may render as buttons


@dataclass
class Receipt:
    ok: bool
    external_id: str = ""  # the provider's id for the sent message
    error: str = ""
    # Retry metadata does not change the legacy receipt value comparison.
    uncertain: bool = field(default=False, compare=False)


@dataclass
class Inbound:
    """One received message, already stripped of quoted text and signatures."""
    channel: str           # channel id from config
    external_id: str       # unique per channel; the dedupe key
    sender: str            # normalized address, chat id or phone
    text: str
    subject: str = ""
    thread: str = ""       # token recovered from the message, if any
    reply_to: str = ""     # provider id of the message this one replies to
    received_at: float = 0.0
    sender_name: str = ""
    raw: dict = field(default_factory=dict)


# -- paths ------------------------------------------------------------------

def default_root():
    """The live state directory. ``CCC_ONCALL_STATE_DIR`` overrides it."""
    override = os.environ.get("CCC_ONCALL_STATE_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    from ccc_server.paths import COMMAND_CENTER_STATE_DIR
    return Path(COMMAND_CENTER_STATE_DIR) / "oncall"


# -- config -----------------------------------------------------------------

def default_config():
    return {
        "version": CONFIG_VERSION,
        "enabled": False,
        "queue": "",
        "repo": "",
        "watch": [],
        "hourly_cap": 12,
        "daily_summary": {"enabled": False, "time": "09:00"},
        "channels": [],
        "quiet_hours": None,
        "alerts": {"decisions": True, "stopped": True, "stuck": True, "fixed": True},
        "customers": {"approve_sends": True, "hold_unknown": True,
                      "notify_fixed": True, "known": []},
        "sources": [],
        "minor_decisions": {"enabled": False},
    }


def _merge_defaults(value, default):
    if not isinstance(default, dict):
        return value if value is not None else copy.deepcopy(default)
    out = {}
    value = value if isinstance(value, dict) else {}
    for key, dflt in default.items():
        if isinstance(dflt, dict) and key != "quiet_hours":
            out[key] = _merge_defaults(value.get(key), dflt)
        else:
            out[key] = value[key] if key in value else copy.deepcopy(dflt)
    for key, extra in value.items():
        out.setdefault(key, extra)
    return out


def load_config(root):
    """The saved config merged over defaults. Missing or corrupt -> defaults."""
    try:
        raw = json.loads((Path(root) / CONFIG_NAME).read_text())
    except (OSError, ValueError):
        raw = {}
    cfg = _merge_defaults(raw if isinstance(raw, dict) else {}, default_config())
    cfg["channels"] = [c for c in cfg.get("channels") or [] if isinstance(c, dict)]
    cfg["sources"] = [s for s in cfg.get("sources") or [] if isinstance(s, dict)]
    return cfg


def validate_config(cfg):
    """Return a list of human-readable problems; empty means it can be saved."""
    problems = []
    if not isinstance(cfg, dict):
        return ["Config must be an object."]
    queue = str(cfg.get("queue") or "")
    if queue and not _QUEUE_RE.match(queue):
        problems.append("Queue names are capital letters, digits, - and _ (for example STOREFRONT).")
    seen = set()
    for ch in cfg.get("channels") or []:
        cid, ctype = str(ch.get("id") or ""), str(ch.get("type") or "")
        if not _ID_RE.match(cid):
            problems.append(f"Channel id {cid!r} must be lowercase letters, digits, - or _.")
        if cid in seen:
            problems.append(f"Two channels are named {cid!r}.")
        seen.add(cid)
        if ctype not in CHANNEL_TYPES:
            problems.append(f"Channel {cid!r} has unknown type {ctype!r}.")
        problems += [f"Channel {cid!r}: {p}" for p in _secret_fields(ch)]
    seen = set()
    for src in cfg.get("sources") or []:
        sid, stype = str(src.get("id") or ""), str(src.get("type") or "")
        if not _ID_RE.match(sid):
            problems.append(f"Source id {sid!r} must be lowercase letters, digits, - or _.")
        if sid in seen:
            problems.append(f"Two sources are named {sid!r}.")
        seen.add(sid)
        if stype not in SOURCE_TYPES:
            problems.append(f"Source {sid!r} has unknown type {stype!r}.")
        squeue = str(src.get("queue") or "")
        if squeue and not _QUEUE_RE.match(squeue):
            problems.append(f"Source {sid!r} has an invalid queue name.")
        problems += [f"Source {sid!r}: {p}" for p in _secret_fields(src)]
    summary = cfg.get("daily_summary", {})
    if not isinstance(summary, dict) or not isinstance(summary.get("enabled", False), bool) or not _HHMM_RE.fullmatch(str(summary.get("time", "09:00"))):
        problems.append("Daily summary needs an on/off value and a time as HH:MM.")
    quiet = cfg.get("quiet_hours")
    if quiet is not None:
        if (not isinstance(quiet, dict) or not _HHMM_RE.match(str(quiet.get("start") or ""))
                or not _HHMM_RE.match(str(quiet.get("end") or ""))):
            problems.append("Quiet hours need a start and an end as HH:MM.")
    return problems


_SECRET_WORDS = ("password", "token", "secret", "api_key", "auth")


def _secret_fields(entry):
    """A secret value must live in the Vault, never in config.json."""
    out = []
    for key, value in entry.items():
        low = key.lower()
        if low.endswith("_vault") or not isinstance(value, str) or not value:
            continue
        if any(word in low for word in _SECRET_WORDS):
            out.append(f"{key!r} looks like a secret; save it in the Vault and set {key}_vault to its name.")
    return out


def save_config(root, cfg):
    """Validate and write atomically with 0600. Returns the saved config."""
    cfg = _merge_defaults(cfg, default_config())
    cfg["version"] = CONFIG_VERSION
    problems = validate_config(cfg)
    if problems:
        raise ValueError(" ".join(problems))
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(cfg, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, root / CONFIG_NAME)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return cfg


def public_config(cfg):
    """The config as the UI may see it. It never held secret values anyway."""
    return copy.deepcopy(cfg)


def channel_by_id(cfg, cid):
    for ch in cfg.get("channels") or []:
        if ch.get("id") == cid:
            return ch
    return None


def known_customer(cfg, channel_id, address):
    """The known-customer entry for this sender, or None."""
    address = normalize_address(address)
    for entry in (cfg.get("customers") or {}).get("known") or []:
        if not isinstance(entry, dict):
            continue
        want = normalize_address(entry.get("address"))
        if not want:
            continue
        if entry.get("channel") and entry.get("channel") != channel_id:
            continue
        if want == address or (want.startswith("@") and address.endswith(want)):
            return entry
    return None


def normalize_address(value):
    """Lowercase, trimmed; ``Name <a@b>`` becomes ``a@b``; phone keeps + and digits."""
    text = str(value or "").strip()
    m = re.search(r"<([^<>\s]+)>", text)
    if m:
        text = m.group(1)
    text = text.strip().lower()
    if text.startswith("whatsapp:"):
        text = text[len("whatsapp:"):]
    if re.fullmatch(r"\+?[\d\s().-]{7,}", text):
        text = ("+" if text.startswith("+") else "") + re.sub(r"\D", "", text)
    return text


# -- secrets ----------------------------------------------------------------

def vault_secrets(name):
    """Resolve a Vault entry name to its value ('' when missing).

    ``CCC_ONCALL_SECRET_<NAME>`` wins so a headless box can run without a
    Keychain. The value is never logged or returned over HTTP.
    """
    name = str(name or "").strip()
    if not name:
        return ""
    env = os.environ.get("CCC_ONCALL_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper())
    if env:
        return env
    try:
        from ccc_server import vault
        return vault.vault_get_value(name) or ""
    except Exception:
        return ""


def new_token():
    """An unguessable thread token: lowercase hex, safe in a subject line."""
    return _secrets.token_hex(8)


# -- database ---------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    token TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    needs_reply INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    sent_at REAL,
    closed_at REAL,
    answer TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS alerts_state ON alerts(state);
CREATE INDEX IF NOT EXISTS alerts_unsent ON alerts(id) WHERE state='open' AND sent_at IS NULL;
CREATE INDEX IF NOT EXISTS alerts_token ON alerts(token);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    sent_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS deliveries_ext ON deliveries(channel, external_id);
CREATE INDEX IF NOT EXISTS deliveries_alert_channel ON deliveries(alert_id, channel, id);
CREATE INDEX IF NOT EXISTS deliveries_time ON deliveries(sent_at, alert_id, ok);
CREATE TABLE IF NOT EXISTS inbound (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    external_id TEXT NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    received_at REAL NOT NULL,
    handled TEXT NOT NULL DEFAULT '',
    UNIQUE(channel, external_id)
);
CREATE INDEX IF NOT EXISTS inbound_unhandled ON inbound(id) WHERE handled='';
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'held',
    queue TEXT NOT NULL DEFAULT '',
    ticket_ref TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    reporter_channel TEXT NOT NULL DEFAULT '',
    reporter_address TEXT NOT NULL DEFAULT '',
    reporter_name TEXT NOT NULL DEFAULT '',
    thread TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source, fingerprint)
);
CREATE INDEX IF NOT EXISTS reports_ref ON reports(ticket_ref);
CREATE INDEX IF NOT EXISTS reports_reporter ON reports(reporter_channel, reporter_address);
CREATE TABLE IF NOT EXISTS customer_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    thread TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    alert_id INTEGER,
    created_at REAL NOT NULL,
    sent_at REAL,
    external_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS customer_messages_state ON customer_messages(state, id);
CREATE INDEX IF NOT EXISTS reports_created ON reports(created_at);
CREATE INDEX IF NOT EXISTS reports_state ON reports(state);
CREATE INDEX IF NOT EXISTS alerts_created ON alerts(created_at);
CREATE INDEX IF NOT EXISTS alerts_kind_state ON alerts(kind,state);
CREATE INDEX IF NOT EXISTS alerts_closed ON alerts(closed_at);
CREATE INDEX IF NOT EXISTS deliveries_sent ON deliveries(sent_at);
CREATE INDEX IF NOT EXISTS customer_messages_created ON customer_messages(created_at);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class Store:
    """SQLite state for one On-call root. Safe to share across threads."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / DB_NAME
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            if "message_kind" not in {r[1] for r in self._db.execute("PRAGMA table_info(deliveries)")}:
                self._db.execute("ALTER TABLE deliveries ADD COLUMN message_kind TEXT NOT NULL DEFAULT 'unknown'")
            columns = {r[1] for r in self._db.execute("PRAGMA table_info(alerts)")}
            if "owner_action_at" not in columns:
                self._db.execute("ALTER TABLE alerts ADD COLUMN owner_action_at REAL")
                self._db.execute("ALTER TABLE alerts ADD COLUMN owner_action TEXT NOT NULL DEFAULT ''")
            self._db.execute("CREATE INDEX IF NOT EXISTS alerts_owner_action ON alerts(owner_action_at)")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self):
        with self._lock:
            self._db.close()

    # -- generic helpers --
    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _run(self, sql, args=()):
        with self._lock, self._db:
            return self._db.execute(sql, args)

    # -- kv (cursors, markers) --
    def kv_get(self, key, default=None):
        row = self._one("SELECT value FROM kv WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def kv_set(self, key, value):
        self._run("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))

    # -- alerts --
    def alert_upsert(self, key, *, kind, title, body, ref="", session_id="",
                     needs_reply=True, meta=None, now=None):
        """Create the alert for this incident key once. Returns (row, created)."""
        existing = self._one("SELECT * FROM alerts WHERE key=?", (key,))
        if existing:
            return _alert_row(existing), False
        now = time.time() if now is None else now
        cur = self._run(
            "INSERT INTO alerts(key,kind,ref,session_id,title,body,token,needs_reply,created_at,meta)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (key, kind, ref, session_id, title, body, new_token(), 1 if needs_reply else 0,
             now, json.dumps(meta or {})))
        return self.alert(cur.lastrowid), True

    def alert(self, alert_id):
        row = self._one("SELECT * FROM alerts WHERE id=?", (alert_id,))
        return _alert_row(row) if row else None

    def alert_by_key(self, key):
        row = self._one("SELECT * FROM alerts WHERE key=?", (key,))
        return _alert_row(row) if row else None

    def alert_by_token(self, token):
        row = self._one("SELECT * FROM alerts WHERE token=?", (str(token or ""),))
        return _alert_row(row) if row else None

    def alerts(self, *, state=None, needs_reply=None, limit=200):
        sql, args = "SELECT * FROM alerts WHERE 1=1", []
        if state:
            sql += " AND state=?"
            args.append(state)
        if needs_reply is not None:
            sql += " AND needs_reply=?"
            args.append(1 if needs_reply else 0)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        return [_alert_row(r) for r in self._all(sql, args)]

    def unsent_alerts(self, limit=50):
        # Rotate bounded batches so failed or parked entries cannot starve older work.
        after = self.kv_get("delivery_cursor", 0)
        rows = self._all("SELECT * FROM alerts INDEXED BY alerts_unsent WHERE state='open' AND sent_at IS NULL "
                         "AND id>? ORDER BY id LIMIT ?", (after, limit))
        if not rows:
            rows = self._all("SELECT * FROM alerts INDEXED BY alerts_unsent WHERE state='open' AND sent_at IS NULL "
                             "ORDER BY id LIMIT ?", (limit,))
        if rows:
            self.kv_set("delivery_cursor", rows[-1]["id"])
        return [_alert_row(r) for r in rows]

    def recent_alert_ids(self, since, limit):
        return {row["alert_id"] for row in self._all(
            "SELECT DISTINCT alert_id FROM deliveries WHERE sent_at>=? "
            "AND ok=1 AND alert_id IS NOT NULL LIMIT ?", (since, limit))}

    def alert_mark_sent(self, alert_id, now=None):
        self._run("UPDATE alerts SET sent_at=? WHERE id=? AND sent_at IS NULL",
                  (time.time() if now is None else now, alert_id))

    def alert_close(self, alert_id, state, answer="", now=None):
        """Move an open alert to ``answered`` or ``cleared``. True if it moved."""
        cur = self._run("UPDATE alerts SET state=?, answer=?, closed_at=? WHERE id=? AND state='open'",
                        (state, answer, time.time() if now is None else now, alert_id))
        return cur.rowcount == 1

    def alert_owner_action(self, alert_id, action, now=None):
        self._run("UPDATE alerts SET owner_action=?, owner_action_at=? WHERE id=? AND state='open'",
                  (action, time.time() if now is None else now, alert_id))

    # -- deliveries --
    def delivery_add(self, alert_id, channel, recipient, receipt, now=None, message_kind="direct"):
        self._run("INSERT INTO deliveries(alert_id,channel,recipient,external_id,ok,error,sent_at,message_kind)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (alert_id, channel, recipient, receipt.external_id, 1 if receipt.ok else 0,
                   receipt.error[:500], time.time() if now is None else now, message_kind))

    def delivery_latest(self, alert_id, channel):
        return self._one("SELECT * FROM deliveries WHERE alert_id=? AND channel=? "
                         "ORDER BY id DESC LIMIT 1", (alert_id, channel))

    def delivery_finish(self, delivery_id, receipt):
        self._run("UPDATE deliveries SET external_id=?, ok=?, error=? WHERE id=?",
                  (receipt.external_id, int(receipt.ok), receipt.error[:500], delivery_id))

    def delivery_alert(self, channel, external_id):
        """The alert a provider message id belongs to, or None."""
        if not external_id:
            return None
        row = self._one("SELECT alert_id FROM deliveries WHERE channel=? AND external_id=? AND ok=1"
                        " ORDER BY id DESC LIMIT 1", (channel, str(external_id)))
        return self.alert(row["alert_id"]) if row and row["alert_id"] else None

    def deliveries_since(self, since):
        return self._all("SELECT * FROM deliveries WHERE sent_at>=? ORDER BY id", (since,))

    def last_delivery(self, channel):
        return self._one("SELECT * FROM deliveries WHERE channel=? ORDER BY id DESC LIMIT 1", (channel,))

    # -- inbound --
    def reply_action_claim(self, alert_id):
        return self._run("INSERT OR IGNORE INTO kv(key,value) VALUES(?, 'true')",
                         (f"reply-action:{alert_id}",)).rowcount == 1

    def reply_action_release(self, alert_id):
        self._run("DELETE FROM kv WHERE key=?", (f"reply-action:{alert_id}",))

    def inbound_claim(self, msg):
        """Record an inbound message once. False when it was already seen."""
        try:
            self._run("INSERT INTO inbound(channel,external_id,sender,text,received_at) VALUES(?,?,?,?,?)",
                      (msg.channel, msg.external_id, msg.sender, msg.text[:8000],
                       msg.received_at or time.time()))
            return True
        except sqlite3.IntegrityError:
            return False

    def park_unhandled_inbound(self, now=None):
        """Expose interrupted routing without replaying a possibly applied approval."""
        for row in self._all("SELECT * FROM inbound WHERE handled='' ORDER BY id LIMIT 50"):
            self.alert_upsert(
                f"inbound-review:{row['id']}", kind="reply_problem",
                title="Reply delivery needs review",
                body=f"Reply on {row['channel']} from {row['sender']} was not confirmed. "
                     "Check the ticket or session before applying it again.\n\n" + row['text'],
                needs_reply=False, now=now)
            self._run("UPDATE inbound SET handled='needs_review' WHERE id=? AND handled=''",
                      (row['id'],))

    def inbound_handled(self, msg, how):
        self._run("UPDATE inbound SET handled=? WHERE channel=? AND external_id=?",
                  ("" if how.startswith("error:") or how.endswith(" failed") else how[:200],
                   msg.channel, msg.external_id))

    # -- reports --
    def report_upsert(self, source, fingerprint, *, title, text, queue="", state="held",
                      reporter_channel="", reporter_address="", reporter_name="",
                      thread="", subject="", meta=None, now=None):
        """Create a report once per (source, fingerprint). Returns (row, created)."""
        existing = self._one("SELECT * FROM reports WHERE source=? AND fingerprint=?",
                             (source, fingerprint))
        if existing:
            return _meta_row(existing), False
        now = time.time() if now is None else now
        cur = self._run(
            "INSERT INTO reports(source,fingerprint,state,queue,title,text,reporter_channel,"
            "reporter_address,reporter_name,thread,subject,created_at,updated_at,meta)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source, fingerprint, state, queue, title[:300], text[:20000], reporter_channel,
             normalize_address(reporter_address), reporter_name[:200], thread or new_token(),
             subject[:300], now, now, json.dumps(meta or {})))
        return self.report(cur.lastrowid), True

    def report(self, report_id):
        row = self._one("SELECT * FROM reports WHERE id=?", (report_id,))
        return _meta_row(row) if row else None

    def report_by_ref(self, ref):
        row = self._one("SELECT * FROM reports WHERE ticket_ref=? ORDER BY id DESC LIMIT 1",
                        (str(ref or ""),))
        return _meta_row(row) if row else None

    def report_by_thread(self, thread):
        if not thread:
            return None
        row = self._one("SELECT * FROM reports WHERE thread=? ORDER BY id DESC LIMIT 1", (thread,))
        return _meta_row(row) if row else None

    def reports(self, *, state=None, reporter=None, limit=200):
        sql, args = "SELECT * FROM reports WHERE 1=1", []
        if state:
            sql += " AND state=?"
            args.append(state)
        if reporter:
            sql += " AND reporter_channel=? AND reporter_address=?"
            args += [reporter[0], normalize_address(reporter[1])]
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        return [_meta_row(r) for r in self._all(sql, args)]

    def report_update(self, report_id, **fields):
        allowed = {"state", "queue", "ticket_ref", "title", "text", "meta"}
        sets, args = ["updated_at=?"], [time.time()]
        for key, value in fields.items():
            if key not in allowed:
                raise KeyError(key)
            sets.append(f"{key}=?")
            args.append(json.dumps(value) if key == "meta" else value)
        args.append(report_id)
        self._run(f"UPDATE reports SET {', '.join(sets)} WHERE id=?", args)
        return self.report(report_id)

    # -- customer messages --
    def customer_message_add(self, *, ref, kind, channel, address, text, state, name="",
                             subject="", thread="", now=None):
        cur = self._run(
            "INSERT INTO customer_messages(ref,kind,channel,address,name,text,subject,thread,state,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ref, kind, channel, normalize_address(address), name[:200], text[:8000], subject[:300],
             thread, state, time.time() if now is None else now))
        return self.customer_message(cur.lastrowid)

    def customer_message(self, message_id):
        return self._one("SELECT * FROM customer_messages WHERE id=?", (message_id,))

    def customer_messages(self, *, state=None, ref=None, limit=100):
        sql, args = "SELECT * FROM customer_messages WHERE 1=1", []
        if state:
            sql += " AND state=?"
            args.append(state)
        if ref:
            sql += " AND ref=?"
            args.append(ref)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        return self._all(sql, args)

    def customer_delivery_problems(self, state, limit=100):
        after = self.kv_get("problem_cursor:" + state, 0)
        rows = self._all("SELECT * FROM customer_messages WHERE state=? AND id>? "
                         "ORDER BY id LIMIT ?", (state, after, limit))
        if not rows:
            rows = self._all("SELECT * FROM customer_messages WHERE state=? ORDER BY id LIMIT ?",
                             (state, limit))
        if rows:
            self.kv_set("problem_cursor:" + state, rows[-1]["id"])
        return rows

    def customer_message_claim(self, message_id):
        """Reserve a send before touching the provider; an uncertain send is not retried."""
        return self._run("UPDATE customer_messages SET state='uncertain', "
                         "error='Delivery may have started; not retried.' "
                         "WHERE id=? AND state='pending'", (message_id,)).rowcount == 1

    def customer_message_update(self, message_id, **fields):
        allowed = {"state", "text", "alert_id", "sent_at", "external_id", "error"}
        sets, args = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise KeyError(key)
            sets.append(f"{key}=?")
            args.append(value)
        args.append(message_id)
        self._run(f"UPDATE customer_messages SET {', '.join(sets)} WHERE id=?", args)
        return self.customer_message(message_id)

    # -- activity log --
    def log(self, kind, detail, now=None):
        self._run("INSERT INTO log(at,kind,detail) VALUES(?,?,?)",
                  (time.time() if now is None else now, kind, str(detail)[:1000]))
        self._run("DELETE FROM log WHERE id <= (SELECT MAX(id) FROM log) - ?", (LOG_KEEP,))

    def recent_log(self, limit=50):
        return self._all("SELECT at,kind,detail FROM log ORDER BY id DESC LIMIT ?", (int(limit),))


def _meta_row(row):
    out = dict(row)
    try:
        out["meta"] = json.loads(out.get("meta") or "{}")
    except ValueError:
        out["meta"] = {}
    return out


def _alert_row(row):
    out = _meta_row(row)
    out["needs_reply"] = bool(out.get("needs_reply"))
    return out
