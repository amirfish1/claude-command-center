"""Passive observation must not acquire a Codex conversation's writer lock."""

import pytest

import server
from ccc_server import codex


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(server, "_codex_app_server_is_live", lambda: True)
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_THREAD_STATE", {})
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TURN_THREAD", {})
    monkeypatch.setattr(server, "_pending_resume_queue", {})
    monkeypatch.setattr(server, "_save_codex_app_server_state_unlocked", lambda: None)
    monkeypatch.setattr(server, "_schedule_codex_queue_pump", lambda sid: None)
    monkeypatch.setattr(server, "_codex_goals_snapshot", lambda: {})
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    monkeypatch.setattr(server, "_schedule_codex_idle_unsubscribe", lambda sid: None)
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", object())
    monkeypatch.setattr(codex, "_CODEX_IDLE_UNSUBSCRIBE_UNSUPPORTED_TRANSPORT", None)


@pytest.mark.parametrize("status,active", [("active", True), ("idle", False), ("notLoaded", False)])
def test_passive_check_reads_without_resuming(monkeypatch, isolated, status, active):
    calls = []

    def rpc(method, params, **kwargs):
        calls.append((method, params))
        return {"result": {"thread": {"id": "test-thread", "status": {"type": status},
                                      "turns": []}}}

    monkeypatch.setattr(server, "_codex_app_server_request", rpc)
    assert codex._codex_app_server_thread_is_active("test-thread") is active
    assert calls == [("thread/read", {"threadId": "test-thread", "includeTurns": True})]


def test_read_failure_preserves_known_active_turn(monkeypatch, isolated):
    server._CODEX_APP_SERVER_THREAD_STATE["test-thread"] = {
        "status": "active", "active_turn_id": "test-turn", "active_writer": "ccc"}
    monkeypatch.setattr(server, "_codex_app_server_request", lambda *a, **k: {"error": "busy"})
    assert codex._codex_app_server_thread_is_active("test-thread")
    assert server._CODEX_APP_SERVER_THREAD_STATE["test-thread"]["active_turn_id"] == "test-turn"


def test_unavailable_passive_check_does_not_start_server(monkeypatch, isolated):
    monkeypatch.setattr(server, "_codex_app_server_is_live", lambda: False)
    monkeypatch.setattr(server, "_ensure_codex_app_server", lambda: pytest.fail("unexpected start"))
    assert not codex._codex_app_server_thread_is_active("test-thread")


@pytest.mark.parametrize("busy", ["turn", "queue", "goal", "approval", "starting"])
def test_idle_release_keeps_work_subscribed(monkeypatch, isolated, busy):
    state = {"status": "idle"}
    if busy == "turn":
        state.update(status="active", active_turn_id="test-turn")
    elif busy == "queue":
        server._pending_resume_queue["test-thread"] = ["next message"]
    elif busy == "goal":
        monkeypatch.setattr(server, "_codex_goals_snapshot", lambda: {
            "test-thread": {"status": "active", "objective": "finish"}})
    elif busy == "approval":
        state["thread_needs_approval"] = True
    elif busy == "starting":
        state["ccc_turn_start_pending"] = True
    server._CODEX_APP_SERVER_THREAD_STATE["test-thread"] = state
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", lambda *a, **k: pytest.fail("unexpected RPC"))
    assert not codex._codex_unsubscribe_idle_thread("test-thread")


def test_idle_release_reads_before_unsubscribing(monkeypatch, isolated):
    server._CODEX_APP_SERVER_THREAD_STATE["test-thread"] = {"status": "idle"}
    calls = []

    def rpc(transport, method, params, **kwargs):
        calls.append(method)
        if method == "thread/read":
            return {"result": {"thread": {"id": "test-thread", "status": {"type": "idle"}}}}
        return {"result": {"status": "unsubscribed"}}

    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    assert codex._codex_unsubscribe_idle_thread("test-thread")
    assert calls == ["thread/read", "thread/unsubscribe"]


@pytest.mark.parametrize("response", [
    {"error": "busy"},
    {"result": {"thread": {"status": {"type": "active"}}}},
    {"result": {"thread": {"status": {"type": "systemError"}}}},
])
def test_idle_release_requires_authoritative_idle(monkeypatch, isolated, response):
    server._CODEX_APP_SERVER_THREAD_STATE["test-thread"] = {"status": "idle"}
    calls = []

    def rpc(transport, method, *a, **k):
        calls.append(method)
        return response

    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    assert not codex._codex_unsubscribe_idle_thread("test-thread")
    assert calls == ["thread/read"]


def test_completion_schedules_release_outside_reader(monkeypatch, isolated):
    scheduled = []
    monkeypatch.setattr(server, "_schedule_codex_idle_unsubscribe", scheduled.append)
    server._CODEX_APP_SERVER_THREAD_STATE["test-thread"] = {
        "status": "active", "active_turn_id": "test-turn", "active_writer": "ccc"}
    codex._codex_app_server_handle_message({"method": "turn/completed", "params": {
        "threadId": "test-thread", "turn": {"id": "test-turn", "status": "completed"}}})
    assert scheduled == ["test-thread"]
    assert server._CODEX_APP_SERVER_THREAD_STATE["test-thread"]["status"] == "idle"


def test_release_does_not_race_a_send(monkeypatch, isolated):
    lock = codex._codex_thread_turn_lock("test-thread")
    lock.acquire()
    try:
        monkeypatch.setattr(server, "_codex_app_server_request_to_transport", lambda *a, **k: pytest.fail("unexpected RPC"))
        assert not codex._codex_unsubscribe_idle_thread("test-thread")
    finally:
        lock.release()


@pytest.mark.parametrize("status", ["notLoaded", "systemError", ""])
def test_unknown_read_status_preserves_owner(monkeypatch, isolated, status):
    server._CODEX_APP_SERVER_THREAD_STATE["test-thread"] = {
        "status": "active", "active_turn_id": "test-turn", "active_writer": "unknown"}
    monkeypatch.setattr(server, "_codex_app_server_request", lambda *a, **k: {
        "result": {"thread": {"id": "test-thread", "status": {"type": status}}}})
    assert codex._codex_app_server_thread_is_active("test-thread")
    assert server._CODEX_APP_SERVER_THREAD_STATE["test-thread"]["active_turn_id"] == "test-turn"


def test_cleanup_never_starts_transport(monkeypatch, isolated):
    server._CODEX_APP_SERVER_THREAD_STATE["test-thread"] = {"status": "idle"}
    monkeypatch.setattr(server, "_ensure_codex_app_server", lambda: pytest.fail("unexpected start"))
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", None)
    assert not codex._codex_unsubscribe_idle_thread("test-thread")


@pytest.mark.parametrize("field,value", [("active_turn_id", "next-turn"),
                                        ("ccc_turn_start_pending", True),
                                        ("thread_needs_approval", True)])
def test_cleanup_rechecks_notifications_after_read(monkeypatch, isolated, field, value):
    server._CODEX_APP_SERVER_THREAD_STATE["test-thread"] = {"status": "idle"}
    calls = []

    def rpc(transport, method, *a, **k):
        calls.append(method)
        server._CODEX_APP_SERVER_THREAD_STATE["test-thread"][field] = value
        return {"result": {"thread": {"status": {"type": "idle"}}}}

    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    assert not codex._codex_unsubscribe_idle_thread("test-thread")
    assert calls == ["thread/read"]


def test_older_transport_is_only_probed_once(monkeypatch, isolated):
    server._CODEX_APP_SERVER_THREAD_STATE["test-thread"] = {"status": "idle"}
    calls = []

    def rpc(transport, method, *a, **k):
        calls.append(method)
        if method == "thread/read":
            return {"result": {"thread": {"status": {"type": "idle"}}}}
        return {"error": {"code": -32601, "message": "Method not found"}}

    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    assert not codex._codex_unsubscribe_idle_thread("test-thread")
    assert not codex._codex_unsubscribe_idle_thread("test-thread")
    assert calls == ["thread/read", "thread/unsubscribe"]
