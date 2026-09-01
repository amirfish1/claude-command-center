from types import SimpleNamespace
from unittest import mock
import threading
import time

import pytest

import server


@pytest.fixture(autouse=True)
def clean_pending_queues():
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()
    yield
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()


@pytest.fixture
def router_env(monkeypatch):
    resume = mock.Mock()
    monkeypatch.setattr(server, "_save_pending_inputs", mock.Mock(return_value=True))
    monkeypatch.setattr(server, "_inject_budget_check", lambda *args: None)
    monkeypatch.setattr(server, "_is_codex_session", lambda sid: True)
    monkeypatch.setattr(server, "find_session_cwd", lambda sid: "/tmp")
    monkeypatch.setattr(
        server,
        "session_live_status",
        lambda sid, cwd=None: {"live": True, "status": "working", "tty": None},
    )
    monkeypatch.setattr(server, "_is_cursor_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_hermes_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_kimi_session", lambda sid: False)
    monkeypatch.setattr(server, "_session_acp_harness", lambda sid: None)
    monkeypatch.setattr(server, "_is_opencode_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_devin_cli_session", lambda sid: False)
    monkeypatch.setattr(server, "resume_session_codex", resume)
    return SimpleNamespace(resume=resume)


def test_claim_removes_one_duplicate_and_restore_preserves_fifo(monkeypatch):
    sid = "claim-restore"
    server._pending_resume_queue[sid] = ["first", "target", "target", "last"]
    monkeypatch.setattr(server, "_save_pending_inputs", mock.Mock(return_value=True))

    claim = server._claim_matching_pending_input(sid, "target")

    assert claim == {
        "session_id": sid,
        "queue_name": "resume",
        "index": 1,
        "item": "target",
    }
    assert server._pending_resume_queue[sid] == ["first", "target", "last"]
    assert server._restore_pending_input_claim(claim)
    assert server._pending_resume_queue[sid] == ["first", "target", "target", "last"]


def test_claim_prefers_resume_queue_and_claims_at_most_one(monkeypatch):
    sid = "claim-precedence"
    server._pending_resume_queue[sid] = ["target", "keep"]
    server._pending_terminal_input_queue[sid] = ["target"]
    monkeypatch.setattr(server, "_save_pending_inputs", mock.Mock(return_value=True))

    claim = server._claim_matching_pending_input(sid, "target")

    assert claim["queue_name"] == "resume"
    assert server._pending_resume_queue[sid] == ["keep"]
    assert server._pending_terminal_input_queue[sid] == ["target"]
