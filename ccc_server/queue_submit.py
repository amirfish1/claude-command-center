"""Durable receipts for dashboard ticket submissions, including lost replies."""
import hashlib
import json
import re
import sqlite3
from contextlib import closing


class PendingSubmission(Exception):
    """The write may have happened; do not claim failure or create a duplicate."""


def enqueue(queue, payload, receipt_path):
    kwargs = dict(
        note=str(payload.get("note") or payload.get("text") or "").strip(),
        source=str(payload.get("source") or "ccc"),
        project=str(payload.get("project") or "").strip(),
        repo_path=str(payload.get("repo_path") or "").strip(),
        lane=str(payload.get("lane") or "normal"),
    )
    if not kwargs["note"]:
        raise ValueError("note required")
    key = payload.get("idempotency_key")
    if key is None:
        return queue.enqueue(**kwargs)  # Existing API clients remain compatible.
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
        raise ValueError("idempotency_key must be 16-128 letters, digits, _ or -")
    fingerprint = hashlib.sha256(json.dumps(kwargs, sort_keys=True).encode()).hexdigest()
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(receipt_path, timeout=10)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS submissions "
                   "(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, item TEXT)")
        with db:
            inserted = db.execute("INSERT OR IGNORE INTO submissions VALUES (?, ?, NULL)",
                                  (key, fingerprint)).rowcount
            old_hash, item = db.execute(
                "SELECT fingerprint, item FROM submissions WHERE key = ?", (key,)).fetchone()
        if old_hash != fingerprint:
            raise ValueError("This submission key belongs to a different ticket")
        if item:
            return json.loads(item)
        project = queue._project_for(kwargs["source"], kwargs["repo_path"], kwargs["project"])
        local = queue._github_backend_for_project(project) is None
        if not local and not inserted:
            # Remote queues lack an atomic dedupe primitive. A pending receipt
            # survives a process death and prevents an unsafe second write.
            raise PendingSubmission("Ticket confirmation is still pending")
        try:
            item = queue.enqueue(**kwargs, **({"dedupe_key": "dashboard:" + key} if local else {}))
        except Exception:
            # Even an exception may follow a successful remote write.
            if not local:
                raise PendingSubmission("Ticket confirmation is still pending") from None
            raise
        with db:
            db.execute("UPDATE submissions SET item = ? WHERE key = ?",
                       (json.dumps(item), key))
        return item
