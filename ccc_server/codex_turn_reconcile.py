"""Don't re-send a queued Codex message whose timed-out turn/start landed anyway.

A turn/start that times out client-side is ambiguous: a slow daemon can still
accept it and write the user message to the rollout. The queue then restores
the message and the pump sends it again -- observed 2026-11-02, a peer report
written to the rollout at 16:55:15 (its turn/start timed out at 16:55:34) and
again at 17:02:25.

The app-server choke point records each timed-out turn/start (thread, exact
input text, rollout size before the send). Before the queue re-sends a
message, ``already_delivered`` checks the rollout from that offset for a user
message with the same text; a hit means the earlier attempt landed, so the
queued copy is committed instead of being delivered twice.
"""
from __future__ import annotations

import json
import os
import threading
import time

RECORD_TTL_S = 6 * 3600.0
MAX_RECORDS_PER_THREAD = 16
MAX_SCAN_BYTES = 64 * 1024 * 1024

_lock = threading.Lock()
_records: dict = {}  # sid -> [{"text", "offset", "ts"}]


def input_text(params) -> str:
    """The text items of a turn/start input, joined the way Codex stores them."""
    items = (params or {}).get("input") if isinstance(params, dict) else None
    if not isinstance(items, list):
        return ""
    parts = [str(i.get("text") or "") for i in items
             if isinstance(i, dict) and i.get("type") == "text"]
    return "\n".join(p for p in parts if p)


def before_send(method, params, rollout_path):
    """Snapshot the rollout size for a turn/start; None for anything else."""
    if method != "turn/start" or not isinstance(params, dict):
        return None
    text = input_text(params)
    sid = str(params.get("threadId") or "")
    if not sid or not text:
        return None
    try:
        offset = os.path.getsize(rollout_path) if rollout_path else 0
    except OSError:
        offset = 0
    return {"sid": sid, "text": text, "offset": offset, "path": rollout_path}


def after_response(ctx, response, *, now=None) -> None:
    """Remember a timed-out turn/start so a later re-send can be reconciled."""
    if not ctx:
        return
    err = (response or {}).get("error") if isinstance(response, dict) else None
    text = err if isinstance(err, str) else (err or {}).get("message", "") if isinstance(err, dict) else ""
    if "timed out" not in str(text).lower():
        return
    now = time.time() if now is None else float(now)
    with _lock:
        rows = [r for r in _records.get(ctx["sid"], []) if now - r["ts"] < RECORD_TTL_S]
        rows.append({"text": ctx["text"], "offset": ctx["offset"],
                     "path": ctx.get("path"), "ts": now})
        _records[ctx["sid"]] = rows[-MAX_RECORDS_PER_THREAD:]


def _matches(queued, sent) -> bool:
    q, s = str(queued or "").strip(), str(sent or "").strip()
    if not q or not s:
        return False
    # The sent text can differ from the queued row only by CCC's own
    # wrapping (session-state instruction stripped, image paths split out).
    return q == s or (len(q) >= 40 and q in s) or (len(s) >= 40 and s in q)


def _rollout_has_user_text(path, offset, text) -> bool:
    try:
        size = os.path.getsize(path)
        start = max(0, min(int(offset or 0), size))
        if size - start > MAX_SCAN_BYTES:
            start = size - MAX_SCAN_BYTES
        with open(path, "rb") as fh:
            fh.seek(start)
            data = fh.read()
    except (OSError, TypeError):
        return False
    needle = text.strip()
    for raw in data.splitlines():
        if b'"role":"user"' not in raw.replace(b" ", b""):
            continue
        try:
            payload = json.loads(raw).get("payload") or {}
        except ValueError:
            continue
        if payload.get("type") != "message" or payload.get("role") != "user":
            continue
        got = "\n".join(
            str(c.get("text") or "") for c in payload.get("content") or []
            if isinstance(c, dict) and c.get("type") == "input_text"
        ).strip()
        if got == needle:
            return True
    return False


def already_delivered(sid, queued_text, *, rollout_path=None, now=None) -> bool:
    """True when a timed-out turn/start of this message reached the rollout.

    Consumes the matching record either way it resolves to "landed", so one
    ambiguous send can reconcile at most one queued copy.
    """
    now = time.time() if now is None else float(now)
    with _lock:
        rows = [r for r in _records.get(str(sid or ""), []) if now - r["ts"] < RECORD_TTL_S]
        _records[str(sid or "")] = rows
        candidates = [r for r in rows if _matches(queued_text, r["text"])]
    for row in candidates:
        path = row.get("path") or rollout_path
        if path and _rollout_has_user_text(path, row["offset"], row["text"]):
            with _lock:
                live = _records.get(str(sid), [])
                if row in live:
                    live.remove(row)
            return True
    return False


def reset_for_tests() -> None:
    with _lock:
        _records.clear()
