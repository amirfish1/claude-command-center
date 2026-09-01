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
    with server._CODEX_APP_SERVER_LOCK:
        server._CODEX_APP_SERVER_THREAD_STATE.clear()
        server._CODEX_APP_SERVER_TURN_THREAD.clear()
    suppressions = getattr(
        server, "_CODEX_QUEUED_STEER_ACK_SUPPRESSIONS", None,
    )
    if suppressions is not None:
        suppressions.clear()
    yield
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()
    with server._CODEX_APP_SERVER_LOCK:
        server._CODEX_APP_SERVER_THREAD_STATE.clear()
        server._CODEX_APP_SERVER_TURN_THREAD.clear()
    if suppressions is not None:
        suppressions.clear()


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
    monkeypatch.setattr(
        server, "_save_codex_app_server_state_unlocked", mock.Mock(),
    )
    return SimpleNamespace(resume=resume)


def _notify_user_message(session_id, text, turn_id="turn-active"):
    server._codex_app_server_handle_message({
        "jsonrpc": "2.0",
        "method": "item/completed",
        "params": {
            "threadId": session_id,
            "turnId": turn_id,
            "item": {
                "id": f"user-{turn_id}",
                "type": "userMessage",
                "text": text,
            },
        },
    })


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


@pytest.mark.parametrize("notification_timing", ["before_response", "after_response"])
def test_successful_claim_suppresses_its_real_delivery_callback(
    router_env, notification_timing,
):
    sid = f"steer-callback-{notification_timing}"
    server._pending_resume_queue[sid] = ["target", "target", "last"]

    def deliver(*args, **kwargs):
        if notification_timing == "before_response":
            _notify_user_message(sid, "target")
        return {"ok": True, "via": "codex-steer"}

    router_env.resume.side_effect = deliver
    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )
    if notification_timing == "after_response":
        _notify_user_message(sid, "target")

    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_claim_suppresses_only_the_matching_native_steer_turn(
    router_env, monkeypatch,
):
    sid = "steer-callback-turn-match"
    server._pending_resume_queue[sid] = ["target", "target", "last"]

    def request(method, params=None, timeout=20):
        if method == "thread/resume":
            return {"result": {"thread": {
                "id": sid,
                "status": {"type": "active"},
                "turns": [{"id": "claimed-turn", "status": "inProgress"}],
            }}}
        if method == "turn/steer":
            _notify_user_message(sid, "target", turn_id="other-turn")
            return {"result": {"turnId": "claimed-turn"}}
        raise AssertionError(f"unexpected app-server method: {method}")

    monkeypatch.setattr(server, "_codex_app_server_request", request)
    router_env.resume.side_effect = lambda session_id, text, **kwargs: (
        server._codex_steer_via_app_server(session_id, text, cwd="/tmp")
    )

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["last"]
    _notify_user_message(sid, "target", turn_id="claimed-turn")
    assert server._pending_resume_queue[sid] == ["last"]


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


def test_exception_restores_claim_and_does_not_suppress_a_later_ack(router_env):
    sid = "steer-exception"
    server._pending_resume_queue[sid] = ["first", "target", "last"]
    router_env.resume.side_effect = RuntimeError("steer exploded")

    with pytest.raises(RuntimeError, match="steer exploded"):
        server._inject_text_into_session_router(
            sid, "target", mode="steer", preserve_queued_steer=True,
        )

    assert server._pending_resume_queue[sid] == ["first", "target", "last"]
    _notify_user_message(sid, "target", turn_id="later-turn")
    assert server._pending_resume_queue[sid] == ["first", "last"]


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
    thread_errors = []

    def steer_into(result):
        try:
            result.update(server._inject_text_into_session_router(
                sid, "target", mode="steer", preserve_queued_steer=True,
            ))
        except BaseException as exc:
            thread_errors.append(exc)

    thread = threading.Thread(target=steer_into, args=(first_result,))
    thread.start()
    assert entered.wait(2)
    second_result = {}
    second_thread = threading.Thread(target=steer_into, args=(second_result,))
    second_thread.start()
    time.sleep(0.05)
    assert second_thread.is_alive()
    assert router_env.resume.call_count == 1
    release.set()
    thread.join(2)
    second_thread.join(2)

    assert not thread.is_alive()
    assert not second_thread.is_alive()
    assert thread_errors == []
    assert first_result["ok"]
    assert first_result["queued_consumed"] == 1
    assert second_result["code"] == "queued_message_missing"
    assert router_env.resume.call_count == 1


def test_direct_steer_excludes_codex_resume_queue_pump(router_env):
    sid = "steer-vs-pump"
    server._pending_resume_queue[sid] = ["target", "next"]
    entered = threading.Event()
    release = threading.Event()
    thread_errors = []
    steer_result = {}

    def deliver(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return {"ok": True, "via": "codex-steer"}

    def steer():
        try:
            steer_result.update(server._inject_text_into_session_router(
                sid, "target", mode="steer", preserve_queued_steer=True,
            ))
        except BaseException as exc:
            thread_errors.append(exc)

    router_env.resume.side_effect = deliver
    thread = threading.Thread(target=steer)
    thread.start()
    assert entered.wait(2)

    pump_result = server._pump_codex_resume_queue(sid)

    assert pump_result == {"ok": True, "waiting": "already-pumping"}
    assert router_env.resume.call_count == 1
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert thread_errors == []
    assert steer_result["ok"]
    assert steer_result["queued_consumed"] == 1


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
