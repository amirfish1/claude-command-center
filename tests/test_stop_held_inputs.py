# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""CCC-237: an explicit Stop holds queued after-turn input.

Repro (OPS-155): two `ccc send` calls queue behind a running Codex turn, then
`ccc send --steer "STOP ... end your turn"` lands. When the stopped turn ended
the queue pump replayed both stale messages as new turns. These tests pin the
fix at the real queue pump: held input never delivers on its own -- not when
the turn ends, not when a newer message is sent -- only on explicit release.
"""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import server
from ccc_server import stop_holds

SID = "01a180da-fafe-76f3-a478-dbee5de098fd"


@pytest.fixture
def queue_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", Path(tmp_path) / "pending-inputs.json")
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()
    server._load_pending_inputs()
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    monkeypatch.setattr(server, "_schedule_codex_queue_pump", lambda sid: None)
    yield tmp_path
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()


def _enqueue(text, field="resume"):
    tx = server._apply_pending_input_operations(SID, [
        {"field": field, "action": "append_tail", "value": text},
    ])
    assert tx.get("ok"), tx


def _pump(monkeypatch):
    """Run the real Codex resume-queue pump with the engine idle (the stopped
    turn has ended) and record what it would deliver."""
    deliveries = []

    def deliver(sid, text, _from_queue=False, **kwargs):
        deliveries.append(server._queued_text_key(text))
        return {"ok": True, "accepted": True, "confirmed": True}

    server._pending_resume_retry_after.clear()
    monkeypatch.setattr(server, "_resume_queue_engine_busy", lambda sid: False)
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(server, "_resume_session_codex_native_delivery", deliver)
    for _ in range(4):
        server._pump_codex_resume_queue(SID)
        server._pending_resume_retry_after.clear()
    return deliveries


def test_stop_steer_text_detection():
    assert stop_holds.is_stop_steer_text("STOP. Do not run the review; end your turn.")
    assert stop_holds.is_stop_steer_text("HALT now")
    assert stop_holds.is_stop_steer_text("Please end your turn now.")
    assert stop_holds.is_stop_steer_text("  ABORT: wrong branch")
    # Ordinary redirect prose is not a Stop.
    assert not stop_holds.is_stop_steer_text("stop using sed and edit the file instead")
    assert not stop_holds.is_stop_steer_text("Also fix the typo in the README")
    assert not stop_holds.is_stop_steer_text("")


def test_held_input_survives_turn_end_and_new_sends(queue_env, monkeypatch):
    _enqueue("STALE1 run the full review")
    _enqueue("STALE2 then post the report")

    assert server._hold_queued_inputs_for_stop(SID, "stop-steer") == 2
    assert not server._pending_resume_queue.get(SID)

    # The stopped turn ends: the pump must not replay the held messages.
    assert _pump(monkeypatch) == []

    # A new message after the Stop delivers on its own; held ones stay held.
    _enqueue("FRESH do this instead")
    assert _pump(monkeypatch) == ["FRESH do this instead"]
    view = server._stop_held_inputs_view(SID)
    assert [i["text"] for i in view["items"]] == [
        "STALE1 run the full review", "STALE2 then post the report",
    ]

    # The hold is durable: a reload of the queue file does not resurrect them.
    server._pending_resume_queue.clear()
    server._load_pending_inputs()
    assert _pump(monkeypatch) == []


def test_selective_release_and_cancel(queue_env, monkeypatch):
    _enqueue("ONE")
    _enqueue("TWO")
    _enqueue("THREE", field="terminal")
    assert server._hold_queued_inputs_for_stop(SID) == 3
    ids = {i["text"]: i["id"] for i in server._stop_held_inputs_view(SID)["items"]}

    sent = []

    def fake_inject(sid, text, **kwargs):
        sent.append((sid, server._queued_text_key(text), kwargs.get("mode")))
        return {"ok": True, "queued": True}

    monkeypatch.setattr(server, "_inject_text_into_session", fake_inject)
    result = server._release_stop_held_inputs(SID, [ids["TWO"]])
    assert result["released"] == 1
    # Released through the normal after-turn send path, nothing else.
    assert sent == [(SID, "TWO", "send")]
    assert [i["text"] for i in result["held"]["items"]] == ["ONE", "THREE"]

    result = server._cancel_stop_held_inputs(SID, [ids["ONE"]])
    assert result["cancelled"] == 1
    assert [i["text"] for i in result["held"]["items"]] == ["THREE"]
    assert sent == [(SID, "TWO", "send")]

    result = server._release_stop_held_inputs(SID)
    assert result["released"] == 1 and result["held"]["items"] == []
    assert [s[1] for s in sent] == ["TWO", "THREE"]


def test_failed_release_stays_held(queue_env, monkeypatch):
    _enqueue("KEEP ME")
    server._hold_queued_inputs_for_stop(SID)
    monkeypatch.setattr(server, "_inject_text_into_session",
                        lambda sid, text, **k: {"ok": False, "error": "engine down"})
    result = server._release_stop_held_inputs(SID)
    assert result["ok"] is False and result["failed"] == 1
    assert [i["text"] for i in result["held"]["items"]] == ["KEEP ME"]


def test_overlay_event_replaces_and_clears(queue_env, monkeypatch):
    _enqueue("PARKED")
    server._hold_queued_inputs_for_stop(SID)
    events = [e for e in server._get_queued_events_for_session(SID)
              if e.get("subtype") == "stop_held_inputs"]
    assert len(events) == 1 and [i["text"] for i in events[0]["items"]] == ["PARKED"]
    first_line = events[0]["line"]
    # No ordinary queued row: a held message is not "will send shortly".
    assert not [e for e in server._get_queued_events_for_session(SID) if e.get("pending")]

    server._cancel_stop_held_inputs(SID)
    events = [e for e in server._get_queued_events_for_session(SID)
              if e.get("subtype") == "stop_held_inputs"]
    assert len(events) == 1 and events[0]["items"] == []
    assert events[0]["line"] != first_line


@pytest.fixture
def httpd(queue_env):
    srv = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _post(srv, path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.server_port}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_stop_steer_holds_queue_before_delivering(httpd, monkeypatch):
    _enqueue("STALE1")
    _enqueue("STALE2")
    seen = {}

    def fake_inject(sid, text, **kwargs):
        # The hold must already have happened when the steer is delivered,
        # or the stopped turn could end and drain the queue first.
        seen["queue_at_steer"] = list(server._pending_resume_queue.get(sid) or [])
        seen["mode"] = kwargs.get("mode")
        return {"ok": True, "via": "codex-steer"}

    monkeypatch.setattr(server, "_inject_text_into_session", fake_inject)
    status, body = _post(httpd, "/api/inject-input", {
        "session_id": SID, "mode": "steer",
        "text": "STOP. Do not run the review; end your turn.",
    })
    assert status == 200 and body["ok"] and body["held_queued"] == 2
    assert seen == {"queue_at_steer": [], "mode": "steer"}


def test_plain_steer_and_send_leave_queue_alone(httpd, monkeypatch):
    _enqueue("STILL QUEUED")
    monkeypatch.setattr(server, "_inject_text_into_session",
                        lambda sid, text, **k: {"ok": True, "queued": k.get("mode") == "send"})
    for payload in (
        {"session_id": SID, "mode": "steer", "text": "also fix the typo"},
        {"session_id": SID, "text": "an ordinary after-turn send"},
    ):
        status, body = _post(httpd, "/api/inject-input", payload)
        assert status == 200 and "held_queued" not in body
    assert list(server._pending_resume_queue.get(SID)) == ["STILL QUEUED"]


def test_inject_esc_holds_and_endpoints_release(httpd, monkeypatch):
    _enqueue("AFTER TURN")
    monkeypatch.setattr(server, "_interrupt_session",
                        lambda sid: {"ok": True, "via": "codex-interrupt",
                                     "queue_at_interrupt": list(server._pending_resume_queue.get(sid) or [])})
    status, body = _post(httpd, "/api/inject-esc", {"session_id": SID})
    assert status == 200 and body["held_queued"] == 1 and body["queue_at_interrupt"] == []

    with urllib.request.urlopen(
        f"http://127.0.0.1:{httpd.server_port}/api/session/{SID}/stop-held", timeout=5,
    ) as resp:
        view = json.loads(resp.read())
    assert [i["text"] for i in view["items"]] == ["AFTER TURN"]

    sent = []
    monkeypatch.setattr(server, "_inject_text_into_session",
                        lambda sid, text, **k: sent.append(server._queued_text_key(text)) or {"ok": True})
    status, body = _post(httpd, "/api/stop-held/release", {"session_id": SID, "ids": [view["items"][0]["id"]]})
    assert status == 200 and body["released"] == 1 and sent == ["AFTER TURN"]
    status, body = _post(httpd, "/api/stop-held/cancel", {"session_id": SID, "ids": "nope"})
    assert status == 400
