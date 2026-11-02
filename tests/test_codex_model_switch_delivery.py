"""A pending model choice must not be bypassed by Send now (turn/steer)."""
import json
from unittest import mock

import pytest

import server


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    sid = "model-switch-thread"
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(json.dumps({
        "type": "turn_context",
        "payload": {"model": "model-before", "turn_id": "running-turn"},
    }) + "\n")
    monkeypatch.delenv("CCC_CODEX_MODEL", raising=False)
    monkeypatch.setattr(server, "_spawned_sessions", [])
    monkeypatch.setattr(server, "_codex_wt_app_worker_delivery", lambda *a: None)
    monkeypatch.setattr(server, "_codex_headless_exec_refusal", lambda *a: None)
    monkeypatch.setattr(server, "_resolve_codex_bin", lambda: {"available": True})
    # thread/resume can update the default before the active turn changes.
    monkeypatch.setattr(server, "_codex_thread_row", lambda *a: {
        "model": "model-after", "cwd": str(tmp_path),
    })
    monkeypatch.setattr(server, "_spawn_registry_entry_for_session", lambda *a: {})
    monkeypatch.setattr(server, "_codex_capture_thread_row", lambda *a: {})
    monkeypatch.setattr(server, "_resolve_codex_rollout_path", lambda *a: rollout)
    monkeypatch.setattr(server, "_get_session_override", lambda *a: {
        "model": "model-after", "reasoning_effort": "medium",
    })
    monkeypatch.setattr(server, "_validate_codex_model", lambda model, **kw: (model, None))
    monkeypatch.setattr(server, "_resume_ledger_append", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_queue_codex_resume", lambda *a, **kw: None)
    from ccc_server import codex_client
    desktop = mock.Mock(return_value=None)
    steer = mock.Mock(return_value={"ok": True, "via": "codex-steer"})
    start = mock.Mock(return_value={"ok": True, "accepted": True})
    monkeypatch.setattr(codex_client, "resume_desktop_conversation", desktop)
    monkeypatch.setattr(server, "_codex_steer_via_app_server", steer)
    monkeypatch.setattr(server, "_codex_resume_or_steer_via_app_server", start)
    return sid, rollout, desktop, steer, start


def test_pending_model_blocks_steer_before_either_transport(delivery):
    sid, _, desktop, steer, start = delivery
    result = server.resume_session_codex(sid, "use the new model", steer=True, _native_delivery=True)
    assert result["ok"] is False
    assert result["code"] == "codex_model_switch_pending"
    assert result["current_model"] == "model-before"
    assert result["requested_model"] == "model-after"
    assert "queued" in result["error"]
    desktop.assert_not_called()
    steer.assert_not_called()
    start.assert_not_called()


def test_normal_send_passes_selected_model_to_next_turn(delivery):
    sid, _, _, steer, start = delivery
    result = server.resume_session_codex(sid, "next turn", _native_delivery=True)
    assert result["accepted"] is True
    assert start.call_args.kwargs["model"] == "model-after"
    steer.assert_not_called()


def test_steer_allowed_after_rollout_confirms_new_model(delivery):
    sid, rollout, desktop, steer, _ = delivery
    with rollout.open("a") as stream:
        stream.write(json.dumps({"type": "turn_context", "payload": {"model": "model-after"}}) + "\n")
    result = server.resume_session_codex(sid, "nudge", steer=True, _native_delivery=True)
    assert result["ok"] is True
    desktop.assert_called_once()
    steer.assert_called_once()


def test_model_refusal_restores_queued_message(delivery, monkeypatch, tmp_path):
    sid, _, desktop, steer, _ = delivery
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(server, "PENDING_INPUT_HANDOFF_DIR", tmp_path / "handoffs")
    monkeypatch.setattr(server, "_refresh_pending_inputs_for_session", lambda *a: True)
    monkeypatch.setattr(server, "_schedule_codex_queue_pump", lambda *a: None)
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **kw: None)
    server._pending_resume_queue[sid] = ["first", "target", "last"]
    try:
        assert server._save_pending_inputs({sid})
        from ccc_server.queue_events import _codex_queued_steer_transaction
        result = _codex_queued_steer_transaction(
            sid, "target", preserve_queued_steer=True, idempotency_key=None,
        )
        assert result["code"] == "codex_model_switch_pending"
        assert result["queued_preserved"] is True
        assert server._pending_resume_queue[sid] == ["first", "target", "last"]
        assert json.loads(server.PENDING_INPUTS_FILE.read_text())["resume_queue"][sid] == ["first", "target", "last"]
        desktop.assert_not_called()
        steer.assert_not_called()
    finally:
        server._pending_resume_queue.pop(sid, None)
