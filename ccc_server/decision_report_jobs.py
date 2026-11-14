"""Durable local receipts; slow evidence collection and queue filing run off-request.

A browser submission ID is also the queue dedupe key, so restarting after an
ambiguous enqueue cannot create a second ticket from a newer transcript.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from ccc_server import core

_guard = threading.Lock()
_worker = None
_ID = re.compile(r"[a-zA-Z0-9-]{16,80}")


def _directory():
    directory = Path(core.COMMAND_CENTER_STATE_DIR) / "decision-report-jobs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


@contextmanager
def _connect():
    path = _directory() / "receipts.sqlite3"
    # Create with owner-only permissions before sqlite opens it.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    connection = sqlite3.connect(path, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, digest TEXT NOT NULL, payload TEXT NOT NULL,
        state TEXT NOT NULL, result TEXT, created REAL NOT NULL)""")
    connection.execute("CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state, created)")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _receipt(row):
    result = json.loads(row["result"]) if row["result"] else {}
    return {**result, "ok": row["state"] != "failed", "saved": True,
            "submission_id": row["id"], "state": row["state"],
            "status_url": "/api/decisions-fix/report/" + row["id"]}


def submit(payload):
    from ccc_server.decision_reports import validate_report, MAX_REQUEST
    invalid = validate_report(payload)
    if invalid:
        return invalid
    submission_id = payload.get("submission_id")
    if not isinstance(submission_id, str) or not _ID.fullmatch(submission_id):
        return {"ok": False, "error": "Invalid report submission ID"}, 400
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    if len(encoded.encode()) > MAX_REQUEST:
        return {"ok": False, "error": "Report exceeds the capture size limit"}, 413
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with _connect() as connection:
        connection.execute("INSERT OR IGNORE INTO jobs VALUES (?, ?, ?, 'queued', NULL, ?)",
                           (submission_id, digest, encoded, time.time()))
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (submission_id,)).fetchone()
        if row["digest"] != digest:
            return {"ok": False, "error": "This receipt belongs to a different report; retry the original captured report"}, 409
        # Explicit retry keeps the original payload and the queue identity.
        if row["state"] == "failed":
            connection.execute("UPDATE jobs SET state='queued', result=NULL WHERE id=?", (submission_id,))
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (submission_id,)).fetchone()
    kick_worker()
    return _receipt(row), 200 if row["state"] == "complete" else 202


def status(submission_id):
    if not _ID.fullmatch(submission_id):
        return {"ok": False, "error": "Unknown report receipt"}, 404
    with _connect() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (submission_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "Unknown report receipt"}, 404
    kick_worker()
    return _receipt(row), 200


def kick_worker():
    global _worker
    with _guard:
        if _worker is not None:
            return
        _worker = threading.Thread(target=_drain, name="decision-report-filing", daemon=True)
        _worker.start()


def _drain():
    global _worker
    try:
        _drain_jobs()
    finally:
        with _guard:
            if _worker is threading.current_thread():
                _worker = None


def _drain_jobs():
    global _worker
    from ccc_server.decision_reports import create_report
    # Also exclude another dashboard process sharing the same state directory.
    with (_directory() / "filing.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            with _guard:
                _worker = None
            return
        try:
            # A killed process may have queued a ticket before saving its receipt.
            with _connect() as connection:
                connection.execute("UPDATE jobs SET state='queued' WHERE state='working'")
            while True:
                with _guard:
                    with _connect() as connection:
                        row = connection.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
                        if row is None:
                            fcntl.flock(lock, fcntl.LOCK_UN)
                            _worker = None
                            return
                        connection.execute("UPDATE jobs SET state='working' WHERE id=?", (row["id"],))
                try:
                    result, code = create_report(json.loads(row["payload"]), submission_id=row["id"])
                except Exception:
                    result, code = {"ok": False, "error": "Evidence is saved; ticket filing failed. Retry this saved report."}, 500
                with _connect() as connection:
                    connection.execute("UPDATE jobs SET state=?, result=? WHERE id=?",
                                       ("complete" if code == 200 and result.get("ref") else "failed",
                                        json.dumps(result), row["id"]))
        finally:
            with _guard:
                # Do not clear a replacement started after an empty drain.
                if _worker is threading.current_thread():
                    _worker = None
