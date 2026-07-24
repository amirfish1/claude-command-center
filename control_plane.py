"""Durable execution-control-plane primitives for CCC.

This module is deliberately stdlib-only.  The dashboard and the persistent
worker both import it, but only the worker writes execution state.  Keeping the
ledger and IPC contract outside ``server.py`` lets the dashboard restart
without becoming the owner of agent processes again.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional, Union


SCHEMA_VERSION = 1
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
ACTIVE_STATES = frozenset({"dispatching", "running"})
WORK_STATES = frozenset({
    "queued", "dispatching", "running", "completed", "failed", "cancelled",
    "interrupted", "uncertain",
})
ALLOWED_TRANSITIONS = {
    "queued": {"dispatching", "cancelled"},
    "dispatching": {"running", "queued", "failed", "interrupted", "uncertain"},
    "running": {"completed", "failed", "cancelled", "interrupted", "uncertain"},
    "interrupted": {"queued", "cancelled", "uncertain"},
    "uncertain": {"queued", "completed", "failed", "cancelled", "interrupted"},
    "completed": set(),
    "failed": {"queued"},
    "cancelled": {"queued"},
}


def state_dir() -> Path:
    override = os.environ.get("CCC_STATE_DIR", "").strip()
    return (
        Path(os.path.expanduser(override))
        if override
        else Path.home() / ".claude" / "command-center"
    )


def socket_path() -> Path:
    override = os.environ.get("CCC_WORKER_SOCKET", "").strip()
    return Path(os.path.expanduser(override)) if override else state_dir() / "worker.sock"


def token_path() -> Path:
    return state_dir() / "worker.token"


def ledger_path() -> Path:
    override = os.environ.get("CCC_WORK_LEDGER", "").strip()
    return (
        Path(os.path.expanduser(override))
        if override
        else state_dir() / "control-plane.sqlite3"
    )


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode_json(value, fallback):
    if not value:
        return fallback
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return decoded


def ensure_token(path: Optional[Path] = None) -> str:
    """Return the local IPC bearer token, creating it with mode 0600 once."""
    path = Path(path or token_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if len(token) >= 32:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return token
    token = secrets.token_hex(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) < 32:
            raise RuntimeError("CCC worker token exists but is invalid")
        return existing
    try:
        os.write(fd, (token + "\n").encode("ascii"))
    finally:
        os.close(fd)
    return token


class WorkLedger:
    """SQLite-backed work graph with idempotent submission and leases."""

    def __init__(self, path: Optional[Union[Path, str]] = None):
        self.path = Path(path or ledger_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _initialize(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS control_meta (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS work_items (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    engine TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'turn',
                    state TEXT NOT NULL,
                    prompt_hash TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT '',
                    owner_epoch TEXT NOT NULL DEFAULT '',
                    lease_expires_at REAL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    dispatched_at REAL,
                    completed_at REAL
                );
                CREATE INDEX IF NOT EXISTS work_items_state_idx
                    ON work_items(state, updated_at);
                CREATE INDEX IF NOT EXISTS work_items_session_idx
                    ON work_items(session_id, created_at);
                CREATE TABLE IF NOT EXISTS work_edges (
                    parent_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
                    child_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
                    relation TEXT NOT NULL DEFAULT 'spawned',
                    created_at REAL NOT NULL,
                    PRIMARY KEY(parent_id, child_id)
                );
                CREATE INDEX IF NOT EXISTS work_edges_child_idx
                    ON work_edges(child_id);
                CREATE TABLE IF NOT EXISTS work_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS work_events_work_idx
                    ON work_events(work_id, seq);
                """
            )
            self._set_meta_conn(conn, "schema_version", SCHEMA_VERSION)
            if self._get_meta_conn(conn, "drain") is None:
                self._set_meta_conn(conn, "drain", {
                    "enabled": False, "reason": "", "requested_at": None,
                })

    @staticmethod
    def _set_meta_conn(conn, key, value):
        now = time.time()
        conn.execute(
            """
            INSERT INTO control_meta(key, value_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE
            SET value_json=excluded.value_json, updated_at=excluded.updated_at
            """,
            (key, _json(value), now),
        )

    @staticmethod
    def _get_meta_conn(conn, key):
        row = conn.execute(
            "SELECT value_json FROM control_meta WHERE key=?", (key,)
        ).fetchone()
        return _decode_json(row["value_json"], None) if row else None

    def set_drain(self, enabled, reason=""):
        value = {
            "enabled": bool(enabled),
            "reason": str(reason or "")[:500],
            "requested_at": time.time() if enabled else None,
        }
        with self._connect() as conn:
            self._set_meta_conn(conn, "drain", value)
        return value

    def drain_state(self):
        with self._connect() as conn:
            return self._get_meta_conn(conn, "drain") or {
                "enabled": False, "reason": "", "requested_at": None,
            }

    @staticmethod
    def _row(row):
        if row is None:
            return None
        item = dict(row)
        item["payload"] = _decode_json(item.pop("payload_json", "{}"), {})
        item["result"] = _decode_json(item.pop("result_json", "{}"), {})
        return item

    def submit(
        self,
        *,
        engine,
        idempotency_key,
        session_id="",
        kind="turn",
        payload=None,
        parent_id=None,
        relation="spawned",
        work_id=None,
    ):
        if not str(engine or "").strip():
            raise ValueError("engine is required")
        if not str(idempotency_key or "").strip():
            raise ValueError("idempotency_key is required")
        payload = payload if isinstance(payload, dict) else {}
        prompt = str(payload.get("prompt") or payload.get("text") or "")
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else ""
        now = time.time()
        work_id = str(work_id or uuid.uuid4())
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM work_items WHERE idempotency_key=?",
                (str(idempotency_key),),
            ).fetchone()
            if existing:
                return self._row(existing), False
            if parent_id:
                parent = conn.execute(
                    "SELECT id FROM work_items WHERE id=?", (str(parent_id),)
                ).fetchone()
                if parent is None:
                    raise ValueError("parent work item does not exist")
            conn.execute(
                """
                INSERT INTO work_items(
                    id, idempotency_key, engine, session_id, kind, state,
                    prompt_hash, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    work_id, str(idempotency_key), str(engine),
                    str(session_id or ""), str(kind or "turn"), prompt_hash,
                    _json(payload), now, now,
                ),
            )
            if parent_id:
                conn.execute(
                    """
                    INSERT INTO work_edges(parent_id, child_id, relation, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(parent_id), work_id, str(relation or "spawned"), now),
                )
            conn.execute(
                """
                INSERT INTO work_events(work_id, event_type, payload_json, created_at)
                VALUES (?, 'submitted', ?, ?)
                """,
                (work_id, _json({"parent_id": parent_id}), now),
            )
            row = conn.execute(
                "SELECT * FROM work_items WHERE id=?", (work_id,)
            ).fetchone()
        return self._row(row), True

    def get(self, work_id):
        with self._connect() as conn:
            return self._row(conn.execute(
                "SELECT * FROM work_items WHERE id=?", (str(work_id),)
            ).fetchone())

    def list(self, states=None, session_id=None, limit=200):
        clauses = []
        args = []
        if states:
            states = [s for s in states if s in WORK_STATES]
            if states:
                clauses.append("state IN (%s)" % ",".join("?" for _ in states))
                args.extend(states)
        if session_id:
            clauses.append("session_id=?")
            args.append(str(session_id))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(max(1, min(int(limit or 200), 2000)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM work_items" + where
                + " ORDER BY created_at DESC LIMIT ?",
                args,
            ).fetchall()
        return [self._row(row) for row in rows]

    def transition(
        self,
        work_id,
        new_state,
        *,
        owner_epoch=None,
        lease_seconds=None,
        result=None,
        error=None,
        event_payload=None,
    ):
        if new_state not in WORK_STATES:
            raise ValueError("invalid work state")
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM work_items WHERE id=?", (str(work_id),)
            ).fetchone()
            if row is None:
                raise KeyError("work item not found")
            old_state = row["state"]
            if new_state != old_state and new_state not in ALLOWED_TRANSITIONS[old_state]:
                raise ValueError(f"invalid transition: {old_state} -> {new_state}")
            lease_expires = (
                now + max(1.0, float(lease_seconds))
                if lease_seconds is not None else row["lease_expires_at"]
            )
            dispatched_at = row["dispatched_at"]
            completed_at = row["completed_at"]
            attempt = row["attempt"]
            if new_state == "dispatching" and old_state != "dispatching":
                dispatched_at = now
                attempt += 1
            if new_state in TERMINAL_STATES:
                completed_at = now
                lease_expires = None
            elif new_state not in ACTIVE_STATES:
                lease_expires = None
            conn.execute(
                """
                UPDATE work_items
                SET state=?, owner_epoch=?, lease_expires_at=?, attempt=?,
                    result_json=?, last_error=?, updated_at=?,
                    dispatched_at=?, completed_at=?
                WHERE id=?
                """,
                (
                    new_state,
                    str(owner_epoch if owner_epoch is not None else row["owner_epoch"]),
                    lease_expires,
                    attempt,
                    _json(result if isinstance(result, dict)
                          else _decode_json(row["result_json"], {})),
                    str(error if error is not None else row["last_error"]),
                    now, dispatched_at, completed_at, str(work_id),
                ),
            )
            conn.execute(
                """
                INSERT INTO work_events(work_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(work_id), f"state.{new_state}",
                    _json(event_payload if isinstance(event_payload, dict) else {}),
                    now,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM work_items WHERE id=?", (str(work_id),)
            ).fetchone()
        return self._row(updated)

    def renew_lease(self, work_id, owner_epoch, lease_seconds=30):
        now = time.time()
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE work_items SET lease_expires_at=?, updated_at=?
                WHERE id=? AND owner_epoch=? AND state IN ('dispatching','running')
                """,
                (
                    now + max(1.0, float(lease_seconds)), now,
                    str(work_id), str(owner_epoch),
                ),
            )
            if result.rowcount != 1:
                raise ValueError("work lease is not owned by this worker")
            row = conn.execute(
                "SELECT * FROM work_items WHERE id=?", (str(work_id),)
            ).fetchone()
        return self._row(row)

    def recover_orphaned_running(self, current_epoch):
        """Mark work owned by a previous worker as uncertain, never replay it."""
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM work_items
                WHERE state IN ('dispatching','running')
                  AND owner_epoch != ?
                """,
                (str(current_epoch),),
            ).fetchall()
            ids = [row["id"] for row in rows]
            for work_id in ids:
                conn.execute(
                    """
                    UPDATE work_items
                    SET state='uncertain', lease_expires_at=NULL, updated_at=?,
                        last_error='worker restarted before completion was confirmed'
                    WHERE id=?
                    """,
                    (now, work_id),
                )
                conn.execute(
                    """
                    INSERT INTO work_events(
                        work_id, event_type, payload_json, created_at
                    ) VALUES (?, 'recovery.uncertain', '{}', ?)
                    """,
                    (work_id, now),
                )
        return ids

    def graph(self, root_id):
        root_id = str(root_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH RECURSIVE graph(id, depth) AS (
                    SELECT ?, 0
                    UNION ALL
                    SELECT e.child_id, graph.depth + 1
                    FROM work_edges e JOIN graph ON e.parent_id = graph.id
                )
                SELECT w.*, graph.depth
                FROM graph JOIN work_items w ON w.id = graph.id
                ORDER BY graph.depth, w.created_at
                """,
                (root_id,),
            ).fetchall()
            edges = conn.execute(
                """
                WITH RECURSIVE graph(id) AS (
                    SELECT ?
                    UNION ALL
                    SELECT e.child_id FROM work_edges e
                    JOIN graph ON e.parent_id = graph.id
                )
                SELECT e.* FROM work_edges e JOIN graph ON e.parent_id = graph.id
                ORDER BY e.created_at
                """,
                (root_id,),
            ).fetchall()
        return {
            "root_id": root_id,
            "items": [self._row(row) for row in rows],
            "edges": [dict(row) for row in edges],
        }

    def summary(self):
        with self._connect() as conn:
            counts = {
                row["state"]: row["n"]
                for row in conn.execute(
                    "SELECT state, COUNT(*) AS n FROM work_items GROUP BY state"
                ).fetchall()
            }
        return {
            "counts": counts,
            "active": sum(counts.get(state, 0) for state in ACTIVE_STATES),
            "queued": counts.get("queued", 0),
            "uncertain": counts.get("uncertain", 0),
            "drain": self.drain_state(),
        }


class ControlPlaneClient:
    """One-request-per-connection client for the worker's Unix socket."""

    def __init__(
        self,
        path: Optional[Union[Path, str]] = None,
        token_file=None,
        timeout=2.0,
    ):
        self.path = Path(path or socket_path())
        self.token_file = Path(token_file or token_path())
        self.timeout = float(timeout)

    def request(self, method, params=None):
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return {"ok": False, "available": False, "error": str(exc)}
        payload = _json({
            "auth": token,
            "method": str(method or ""),
            "params": params if isinstance(params, dict) else {},
        }).encode("utf-8") + b"\n"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.path))
            sock.sendall(payload)
            chunks = bytearray()
            while b"\n" not in chunks:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) > 4 * 1024 * 1024:
                    raise ValueError("CCC worker response too large")
        except (OSError, ValueError) as exc:
            return {"ok": False, "available": False, "error": str(exc)}
        finally:
            sock.close()
        try:
            response = json.loads(bytes(chunks).split(b"\n", 1)[0])
        except (ValueError, UnicodeDecodeError) as exc:
            return {"ok": False, "available": False, "error": str(exc)}
        if isinstance(response, dict):
            response.setdefault("available", True)
            return response
        return {"ok": False, "available": True, "error": "invalid worker response"}


def authenticated(candidate, expected):
    return hmac.compare_digest(str(candidate or ""), str(expected or ""))
