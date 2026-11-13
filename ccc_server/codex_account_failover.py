# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Move a Codex session off an account that refuses its model.

A lapsed subscription keeps its login but loses models, so the turn dies at
once: "The 'x' model is not supported when using Codex with a ChatGPT account."
No model ran, so the message is unanswered rather than half done. CCC moves the
thread to a connected account whose catalog lists the model and sends the
message again. With no such account, a continuation on a fallback engine takes
the message instead.

This is an unattended turn, and CCC-863 showed what those cost, so it is held
to: this exact refusal, one retry per failed turn (a claim file survives
restarts), recent failures only, and the shared wake_guard ledger. A usage
limit is deliberately out of scope; that lane stays approval-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time

from ccc_server import core as _core
from ccc_server import limit_events as _limit_events

REFUSAL = "model is not supported when using Codex"
_MODEL = re.compile(r"'([^']+)' model is not supported")
MAX_AGE_S = 30 * 60
SCAN_BYTES = 32 * 1024
TURN_BYTES = 2 * 1024 * 1024
_FALLBACK_ENGINES = ("claude", "kimi")

_lock = threading.Lock()
_pending = {}


def _user_text(payload):
    if payload.get("type") == "user_message":
        return payload.get("message") if isinstance(payload.get("message"), str) else None
    item = payload.get("item")
    if payload.get("type") == "item_completed" and isinstance(item, dict) and item.get("type") == "UserMessage":
        parts = [part.get("text") for part in item.get("content") or []
                 if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)]
        return "\n".join(parts) or None
    return None


def detect(path):
    """The refused turn at the end of a rollout, or None.

    A later turn clears it, so a session that moved on is never retried.
    """
    if not any(REFUSAL in line for line in _core._tail_read_lines(path, SCAN_BYTES)):
        return None
    found = text = None
    for line in _core._tail_read_lines(path, TURN_BYTES):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        payload = event.get("payload") if isinstance(event, dict) else None
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind in ("task_started", "turn.started"):
            found = text = None
        elif kind == "task_complete":
            error = payload.get("error")
            message = str(error.get("message") or "") if isinstance(error, dict) else ""
            match = _MODEL.search(message) if REFUSAL in message else None
            found = {"turn_id": str(payload.get("turn_id") or ""), "model": match.group(1),
                     "text": text, "at": _limit_events._timestamp(event, path)} if match else None
        else:
            text = _user_text(payload) or text
    return found


def note(sid, path):
    """Queue a fresh refusal. Called under the limit cache lock, so never acts."""
    try:
        found = detect(path)
    except OSError:
        return
    if found and time.time() - found["at"] <= MAX_AGE_S:
        with _lock:
            _pending[sid] = dict(found, path=str(path))


def run_pending(now=None):
    with _lock:
        batch = dict(_pending)
        _pending.clear()
    for sid, found in batch.items():
        try:
            handle(sid, found)
        except Exception as exc:
            _log("FAILED", sid, repr(exc))
            _tell(sid, "Codex session needs you", "CCC could not move it off the account that refused "
                  f"{found['model']}. Pick another account or engine and send the message again.", "needs_input")


def _log(event, sid, detail):
    _core._log_activity("codex-account-failover", event, f"session={sid} {detail}")


def _tell(sid, title, body, kind="info"):
    from ccc_server import notify
    notify.post({"kind": kind, "title": title, "body": body, "session_id": sid, "url": f"/?session={sid}"})


def _claim(sid, turn_id):
    """True for the one caller that owns this failed turn, ever."""
    root = _core.COMMAND_CENTER_STATE_DIR / "codex-account-failover"
    root.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(f"{sid}:{turn_id}".encode()).hexdigest()[:32]
    try:
        os.close(os.open(root / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        return False
    return True


def _fallback_engine():
    saved = (_core._load_spawn_defaults() or {}).get("engine")
    return saved if saved in _FALLBACK_ENGINES else _FALLBACK_ENGINES[0]


def handle(sid, found):
    from ccc_server import codex_accounts, continuation, wake_guard
    if os.environ.get("CCC_CODEX_ACCOUNT_KEY") or not _claim(sid, found["turn_id"]):
        return None
    model, path, text = found["model"], found["path"], found.get("text")
    account = codex_accounts.session_account(sid)
    current = account["home"] if account else codex_accounts.default_home()
    target = next(iter(codex_accounts.movable_homes(model, current)), None)
    if target is not None:
        # Not a wake: the next send needs this even if the retry is refused.
        codex_accounts.rebind(sid, codex_accounts.account_for_home(target))
    label = codex_accounts.label_for_home(target) if target is not None else ""
    allowed, why = (False, "no_text") if not text else wake_guard.request(
        sid, source="codex-account-failover", incident=found["turn_id"], transcript_path=path,
        path=str(_core.COMMAND_CENTER_STATE_DIR / "session-wakes.json"))
    if not allowed:
        _log("RETRY-REFUSED", sid, f"model={model} moved_to={label or '-'} why={why}")
        moved = f"It now uses {label}. " if label else ""
        _tell(sid, "Codex session needs you", f"The account refused {model}. {moved}"
              "CCC did not retry the message; send it again.", "needs_input")
        return {"ok": False, "code": why}
    if target is not None:
        result = _core._inject_text_into_session(sid, text, source="codex-account-failover")
        ok = bool(isinstance(result, dict) and result.get("ok"))
        _log("MOVED" if ok else "RETRY-FAILED", sid, f"model={model} moved_to={label}")
        if ok:
            _tell(sid, "Codex session switched account", f"The previous account refused {model}. "
                  f"CCC moved the session to {label} and sent the message again.")
        else:
            _tell(sid, "Codex session needs you", f"CCC moved the session to {label}, but could not resend "
                  "the message. Send it again.", "needs_input")
        return result
    engine = _fallback_engine()
    result = continuation.spawn_continuation(sid, prompt=text, engine=engine)
    ok = bool(result.get("ok"))
    _log("ENGINE-FALLBACK" if ok else "ENGINE-FALLBACK-FAILED", sid,
         f"model={model} engine={engine} new={result.get('new_session_id') or '-'}")
    new_sid = result.get("new_session_id")
    if ok:
        _tell(new_sid, f"Codex session continued on {engine}", f"No connected Codex account offers {model}, "
              f"so CCC continued session {sid[:8]} on {engine}.")
    else:
        _tell(sid, "Codex session needs you", f"No connected Codex account offers {model}, and CCC could "
              f"not start {engine}. Pick another model and send the message again.", "needs_input")
    return result
