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


def test_successful_explicit_queued_steer_claims_before_delivery(router_env):
    sid = "steer-success"
    server._pending_resume_queue[sid] = ["target", "target", "last"]
    seen_during_delivery = []

    def deliver(*args, **kwargs):
        seen_during_delivery.append(list(server._pending_resume_queue[sid]))
        return {"ok": True, "via": "codex-steer"}

    router_env.resume.side_effect = deliver
    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert seen_during_delivery == [["target", "last"]]
    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_failed_queued_steer_restores_original_fifo_position(router_env):
    sid = "steer-failure"
    server._pending_resume_queue[sid] = ["first", "target", "last"]
    router_env.resume.return_value = {
        "ok": False,
        "via": "codex-steer",
        "code": "codex_steer_failed",
    }

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert not result["ok"]
    assert result["queued_preserved"]
    assert server._pending_resume_queue[sid] == ["first", "target", "last"]


def test_legacy_steer_claims_matching_queue_copy(router_env):
    sid = "stale-client"
    server._pending_resume_queue[sid] = ["target"]
    router_env.resume.return_value = {"ok": True, "via": "codex-steer"}

    result = server._inject_text_into_session_router(sid, "target", mode="steer")

    assert result["queued_consumed"] == 1
    assert sid not in server._pending_resume_queue


def test_second_explicit_steer_cannot_deliver_after_first_claim(router_env):
    sid = "concurrent-steer"
    server._pending_resume_queue[sid] = ["target"]
    entered = threading.Event()
    release = threading.Event()

    def deliver(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return {"ok": True, "via": "codex-steer"}

    router_env.resume.side_effect = deliver
    first_result = {}
    thread = threading.Thread(target=lambda: first_result.update(
        server._inject_text_into_session_router(
            sid, "target", mode="steer", preserve_queued_steer=True,
        )
    ))
    thread.start()
    assert entered.wait(2)
    second_result = {}
    second_thread = threading.Thread(target=lambda: second_result.update(
        server._inject_text_into_session_router(
            sid, "target", mode="steer", preserve_queued_steer=True,
        )
    ))
    second_thread.start()
    time.sleep(0.05)
    assert second_thread.is_alive()
    assert router_env.resume.call_count == 1
    release.set()
    thread.join(2)
    second_thread.join(2)

    assert not thread.is_alive()
    assert not second_thread.is_alive()
    assert second_result["code"] == "queued_message_missing"
    assert router_env.resume.call_count == 1


def test_finalizer_does_not_consume_a_second_copy_after_router_commit():
    sid = "already-consumed"
    server._pending_resume_queue[sid] = ["target", "last"]

    result = server._finalize_queued_steer_result(
        sid,
        "target",
        {"ok": True, "via": "codex-steer", "queued_consumed": 1},
    )

    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_finalizer_preserves_missing_replacement_conflict():
    result = server._finalize_queued_steer_result(
        "missing",
        "target",
        {
            "ok": False,
            "via": "codex-steer",
            "code": "queued_message_missing",
            "queued_consumed": 0,
            "error": "queued message no longer exists",
        },
    )

    assert result == {
        "ok": False,
        "via": "codex-steer",
        "code": "queued_message_missing",
        "queued_consumed": 0,
        "error": "queued message no longer exists",
    }
