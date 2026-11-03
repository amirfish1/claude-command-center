"""Queued rows say who sent them when they did not come from the composer.

Peer sessions, WatchTower and services carry a sender header; composer input
does not. The queued event exposes ``queued_from`` so the tray can frame and
label non-composer messages instead of showing them as the user's own words.
"""
import importlib
from pathlib import Path

import pytest

import sender_envelope as attribution

server = importlib.import_module("server")
pending_inputs = importlib.import_module("ccc_server.pending_inputs")

SID = "sess-queued-from"


@pytest.fixture
def queue_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("WATCHTOWER_STATE_DIR", str(tmp_path / "wt"))
    monkeypatch.setenv("CCC_ATTRIBUTION_EMIT", "1")
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", Path(tmp_path) / "pending-inputs.json")
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()
    server._load_pending_inputs()
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    yield tmp_path
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()


def _queued(sid):
    return [ev for ev in server._get_queued_events_for_session(sid) if ev.get("pending")]


def test_composer_text_has_no_origin(queue_env):
    assert server._queue_terminal_input(SID, "typed in the composer").get("ok")
    (ev,) = _queued(SID)
    assert ev["text"] == "typed in the composer"
    assert "queued_from" not in ev


def test_peer_session_header_becomes_origin(queue_env):
    attr = attribution.make(
        SID,
        who=attribution.author("session", "01a19455-2e62-72d1-bfc5-929aae0ccece", "01a19455", "codex"),
        basis="claimed",
    )
    attr["reply_to"] = {"addr": "rr_test", "method": "ccc send <addr> <reply>"}
    text = attribution.carry("Settings verifier found a bug", attr, app="ccc")
    assert text.startswith(attribution.COMPACT)
    assert server._queue_terminal_input(SID, text).get("ok")
    (ev,) = _queued(SID)
    assert ev["text"] == "Settings verifier found a bug"
    origin = ev["queued_from"]
    assert origin["kind"] == "session"
    assert origin["name"] == "01a19455"
    assert origin["msg_id"] == attr["message_id"]
    assert origin["reply"] == "rr_test"


def test_unjournaled_unknown_header_still_marks_external():
    text = ("[sender: unnamed (unknown) · msg af82da67-8e82-42aa-addf-10fc9f491dad]\n"
            "[WATCHTOWER] A new comment")
    origin = pending_inputs._queued_origin(text)
    assert origin is not None
    assert origin["kind"] == "unknown"
    assert origin["msg_id"] == "af82da67-8e82-42aa-addf-10fc9f491dad"


def test_relay_labels_are_human_readable(monkeypatch):
    monkeypatch.setattr(attribution, "from_text", lambda raw: {
        "message_id": "m1",
        "author": {"kind": "unknown"},
        "relay": [{"via": "wt-deliver"}, {"via": "wt-deliver"}, {"via": "ccc-inject"}],
    })
    origin = pending_inputs._queued_origin(attribution.COMPACT + "x]\nbody")
    assert origin["via"] == ["WatchTower", "ccc send"]


def test_queue_removals_are_logged(queue_env, monkeypatch):
    lines = []
    monkeypatch.setattr(server, "_log_activity", lambda cat, verb, detail: lines.append((verb, detail)))
    text = "[sender: x (session) · msg ef4d9ec5-03ae-430c-ba7f-234bf8926119]\nreport"
    server._apply_pending_input_operations(SID, [{"field": "resume", "action": "append_tail", "value": text}])
    server._apply_pending_input_operations(SID, [{"field": "resume", "action": "pop_head_claim"}])
    assert lines == []  # adds and temporary claims are not removals
    server._apply_pending_input_operations(SID, [{"field": "resume", "action": "insert_front", "value": text}])
    server._apply_pending_input_operations(SID, [{"field": "resume", "action": "clear"}])
    (verb, detail), = lines
    assert verb == "Q_REMOVE"
    assert "msg=ef4d9ec5-03ae-430c-ba7f-234bf8926119" in detail and "resume:clear" in detail
