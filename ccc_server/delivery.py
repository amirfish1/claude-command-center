# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""One id for one message on its way into a session.

A message that enters ``/api/inject-input`` used to be known by four unrelated
names: the attribution ``message_id``, the worker ledger ``idempotency_key``,
the receipt ``inject_id`` and the queue row ``pending_id``. None of them
reached the browser, so queued cards were matched to the user's own echo by
whitespace-normalised text. This module mints the one ``delivery_id`` the
whole path shares and answers "which queued message is this" by that id.

Slice 1 of the delivery-owner work: mint, bind, lookup, cancel. The durable
store is still ``pending-inputs.json`` behind ``_mutate_pending_inputs``; the
Codex claim transaction remains the sole delivery authority.
"""

from __future__ import annotations

import contextvars
import re
import uuid

from ccc_server import core as _core

# What a client may hand us as its own key. Anything else gets a server id.
_CLIENT_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

_binding = contextvars.ContextVar("ccc_delivery_binding", default=None)


class _Binding:
    __slots__ = ("delivery_id", "text", "used")

    def __init__(self, delivery_id, text):
        self.delivery_id = str(delivery_id)
        self.text = _core._queued_text_key(text)
        self.used = False


def mint(*, attr=None, idempotency_key=None):
    """The delivery id for one send.

    Prefers the attribution ``message_id`` because it already survives the
    dashboard->worker hop unchanged and is persisted with the queue row; then
    the caller's own durable key; then a fresh id. Never writes back into
    ``idempotency_key``: the un-keyed text dedupe window must keep working.
    """
    message_id = attr.get("message_id") if isinstance(attr, dict) else None
    if message_id:
        return str(message_id)
    key = str(idempotency_key or "").strip()
    if _CLIENT_KEY_RE.fullmatch(key):
        return key
    return uuid.uuid4().hex


def within(delivery_id, text, fn):
    """Run ``fn`` with ``delivery_id`` offered to the one queue row it creates."""
    token = _binding.set(_Binding(delivery_id, text))
    try:
        return fn()
    finally:
        _binding.reset(token)


def bound_id():
    """The delivery_id of the send being routed on this thread, or None.

    Lets a cross-process hop (dashboard -> worker) carry the id along, since
    the queue row is created in whichever process the engine adapter runs.
    """
    binding = _binding.get()
    return binding.delivery_id if binding is not None else None


def adopt(text):
    """The bound id for a new queue row holding this message, once.

    Called only by enqueue sites (``_new_pending_entry``), never by decode
    paths that rebuild rows from disk. The text check (attribution header
    ignored) keeps a router that queues something else mid-send, such as an
    auto-continue marker, from taking the id meant for the user's message.
    """
    binding = _binding.get()
    if binding is None or binding.used:
        return None
    if _core._queued_text_key(text) != binding.text:
        return None
    binding.used = True
    return binding.delivery_id


def entry_id(entry):
    """The delivery id of an in-memory queue row, or None."""
    value = getattr(entry, "pending_id", None)
    return str(value) if value else None


def _queues():
    return (
        ("resume", _core._pending_resume_queue, _core._pending_resume_lock),
        ("terminal", _core._pending_terminal_input_queue,
         _core._pending_terminal_input_lock),
    )


def lookup(session_id, delivery_id):
    """Where one message currently waits: queued, held, or None."""
    want = str(delivery_id or "").strip()
    if not session_id or not want:
        return None
    _core._refresh_queued_view_if_file_changed(session_id)
    for queue_name, queue, lock in _queues():
        with lock:
            for item in queue.get(session_id) or ():
                if entry_id(item) == want:
                    return {
                        "state": "queued",
                        "queue": queue_name,
                        "text": _core._queued_text_key(item),
                    }
    from ccc_server import stop_holds
    snap = stop_holds.snapshot(session_id) or {}
    for item in snap.get("items") or ():
        if str(item.get("id") or "") == want:
            return {
                "state": "held",
                "queue": "stop_held",
                "text": _core._queued_text_key(item.get("text")),
            }
    return None


def cancel(session_id, delivery_id):
    """Discard one waiting message by id, whichever queue holds it.

    Idempotent: a message that already drained reports ``cancelled: 0`` with
    ``ok: True``, matching the text-based cancel the browser used before.
    """
    want = str(delivery_id or "").strip()
    if not session_id or not want:
        return {"ok": False, "cancelled": 0, "code": "delivery_id_required"}

    def pop_one():
        for queue_name, queue, lock in _queues():
            with lock:
                items = queue.get(session_id)
                if not items:
                    continue
                for index, item in enumerate(items):
                    if entry_id(item) != want:
                        continue
                    removed = items.pop(index)
                    if not items:
                        queue.pop(session_id, None)
                    return {"queue": queue_name, "item": removed}
        return None

    transaction = _core._mutate_pending_inputs({session_id}, pop_one)
    if not transaction.get("ok"):
        return {
            "ok": False,
            "cancelled": 0,
            "code": transaction.get("code") or "pending_input_persist_failed",
            "error": transaction.get("error") or "could not persist cancel",
        }
    hit = transaction.get("value")
    if hit:
        _core._complete_pending_input_handoff(hit["item"])
        _core._log_activity(
            "inject", "Q_CANCELLED",
            f"session={session_id} delivery_id={want} queue={hit['queue']}",
        )
        return {"ok": True, "cancelled": 1, "queue": hit["queue"]}
    held = _core._cancel_stop_held_inputs(session_id, [want])
    if held.get("cancelled"):
        return {"ok": True, "cancelled": int(held["cancelled"]), "queue": "stop_held"}
    return {"ok": True, "cancelled": 0}
