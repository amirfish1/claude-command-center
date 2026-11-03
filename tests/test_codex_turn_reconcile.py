"""A timed-out turn/start that landed anyway is not re-sent from the queue.

Incident 2026-11-02: a peer report reached the rollout at 16:55:15, its
turn/start timed out at 16:55:34, the queue restored it, and the pump wrote
it to the rollout a second time at 17:02:25.
"""
import json

import pytest

from ccc_server import codex_turn_reconcile as r

SID = "01a199ff-595e-7fb0-a394-6e696ae07aaa"
TEXT = "[sender: 01a19455 (session) · msg c8955d22]\nApplied only Settings search visibility fix"
TIMEOUT = {"error": "Codex app-server request timed out: turn/start"}


@pytest.fixture(autouse=True)
def clean():
    r.reset_for_tests()
    yield
    r.reset_for_tests()


def _line(role, text):
    return json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": role,
        "content": [{"type": "input_text", "text": text}]}}) + "\n"


def _send(path, text=TEXT, response=TIMEOUT):
    ctx = r.before_send("turn/start", {"threadId": SID, "input": [{"type": "text", "text": text}]}, str(path))
    r.after_response(ctx, response)
    return ctx


def test_landed_timeout_is_reconciled_once(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(_line("user", TEXT))  # an OLDER identical message
    _send(rollout)
    with rollout.open("a") as fh:
        fh.write(_line("user", TEXT))  # the timed-out send landed
    assert r.already_delivered(SID, TEXT) is True
    assert r.already_delivered(SID, TEXT) is False  # one record, one reconcile


def test_timeout_that_never_landed_is_resent(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(_line("user", TEXT))  # only before the send: not proof
    _send(rollout)
    with rollout.open("a") as fh:
        fh.write(_line("assistant", TEXT))
    assert r.already_delivered(SID, TEXT) is False


def test_successful_send_records_nothing(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("")
    _send(rollout, response={"result": {"turn": {"id": "t1"}}})
    with rollout.open("a") as fh:
        fh.write(_line("user", TEXT))
    assert r.already_delivered(SID, TEXT) is False


def test_other_messages_are_not_reconciled(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("")
    _send(rollout)
    with rollout.open("a") as fh:
        fh.write(_line("user", TEXT))
    assert r.already_delivered(SID, "a different queued message") is False
    assert r.already_delivered("other-thread", TEXT) is False


def test_non_turn_start_requests_are_ignored():
    assert r.before_send("thread/resume", {"threadId": SID}, None) is None


def test_queue_commits_a_landed_message_instead_of_resending(monkeypatch, tmp_path):
    import importlib
    importlib.import_module("server")
    from ccc_server import core, queue_events

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("")
    _send(rollout)
    with rollout.open("a") as fh:
        fh.write(_line("user", TEXT))
    monkeypatch.setattr(core, "_retry_pending_input_recovery", lambda sid: True)
    monkeypatch.setattr(core, "_apply_pending_input_operations", lambda sid, ops: {
        "ok": True, "value": [{"item": TEXT, "claim_sequence": 1}]})
    committed, sent = [], []
    monkeypatch.setattr(core, "_commit_pending_input_claim", lambda c: committed.append(c) or True)
    monkeypatch.setattr(core, "_resume_session_codex_native_delivery",
                        lambda *a, **k: sent.append(a) or {"ok": True})
    monkeypatch.setattr(core, "_log_activity", lambda *a, **k: None)
    result = queue_events._codex_queued_delivery_transaction(SID)
    assert result["via"] == "codex-reconciled-timeout" and result["delivered"]
    assert len(committed) == 1 and sent == []
