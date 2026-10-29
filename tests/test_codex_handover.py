"""Desktop handover must release the writer without interrupting work."""

from types import SimpleNamespace

import pytest
import server
from ccc_server import codex_handover as handover


@pytest.fixture
def owner(monkeypatch):
    closed = []
    transport = SimpleNamespace(kind="stdio", proc=object(), close=lambda: closed.append(True))
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", transport)
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_INITIALIZING", False)
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_INFLIGHT", 0)
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_THREAD_STATE", {})
    monkeypatch.setattr(server, "_pending_resume_queue", {})
    monkeypatch.setattr(server, "_codex_goals_snapshot", lambda: {})
    monkeypatch.setattr(server, "find_headless_codex_exec_owner", lambda session_id, session_cwd=None: None)
    monkeypatch.setattr(handover, "desktop_available", lambda: True)
    monkeypatch.setattr(handover.sys, "platform", "darwin")
    monkeypatch.setattr(handover, "_in_progress", False)
    opened = []
    monkeypatch.setattr(server, "open_session_in_codex_desktop", lambda sid, cwd=None: opened.append(sid) or {"ok": True})

    def rpc(transport, method, params, **kwargs):
        if method == "thread/loaded/list":
            return {"result": {"data": ["test-thread"]}}
        if method in ("thread/queue/list", "thread/backgroundTerminals/list"):
            return {"result": {"data": []}}
        return {"result": {"thread": {"id": params["threadId"], "status": {"type": "idle"}}}}

    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    return closed, opened, transport


def test_idle_handover_closes_owner_then_opens_same_thread(owner):
    closed, opened, _ = owner
    assert handover.handover_to_desktop("test-thread", "/tmp")["ok"]
    assert closed == [True]
    assert opened == ["test-thread"]
    assert server._CODEX_APP_SERVER_TRANSPORT is None
    assert not handover.in_progress()


@pytest.mark.parametrize("busy", ["active", "queue", "goal", "approval", "rpc", "initializing", "exec"])
def test_busy_handover_waits_without_closing_or_opening(monkeypatch, owner, busy):
    closed, opened, _ = owner
    if busy == "queue":
        server._pending_resume_queue["another-thread"] = ["next"]
    elif busy == "goal":
        monkeypatch.setattr(server, "_codex_goals_snapshot", lambda: {"test-thread": {"status": "active"}})
    elif busy == "rpc":
        monkeypatch.setattr(server, "_CODEX_APP_SERVER_INFLIGHT", 1)
    elif busy == "initializing":
        monkeypatch.setattr(server, "_CODEX_APP_SERVER_INITIALIZING", True)
    elif busy == "exec":
        monkeypatch.setattr(server, "find_headless_codex_exec_owner", lambda *a, **k: {"pid": 42})
    else:
        server._CODEX_APP_SERVER_THREAD_STATE["another-thread"] = {
            "status": "active" if busy == "active" else "idle",
            "thread_needs_approval": busy == "approval"}
    result = handover.handover_to_desktop("test-thread", "/tmp")
    assert result["pending"]
    assert not closed and not opened
    assert not handover.in_progress()


def test_authoritative_other_active_thread_prevents_handover(monkeypatch, owner):
    closed, opened, _ = owner
    def rpc(transport, method, params, **kwargs):
        if method == "thread/loaded/list":
            return {"result": {"data": ["test-thread", "another-thread"]}}
        if method in ("thread/queue/list", "thread/backgroundTerminals/list"):
            return {"result": {"data": []}}
        return {"result": {"thread": {"status": {"type": "active" if params["threadId"] == "another-thread" else "idle"}}}}
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    assert handover.handover_to_desktop("test-thread")["pending"]
    assert not closed and not opened


def test_already_unloaded_thread_does_not_close_shared_owner(monkeypatch, owner):
    closed, opened, _ = owner
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", lambda *a, **k: {"result": {"data": []}})
    assert handover.handover_to_desktop("test-thread")["ok"]
    assert not closed and opened == ["test-thread"]


def test_failed_status_read_is_fail_closed(monkeypatch, owner):
    closed, opened, _ = owner
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", lambda *a, **k: {"error": "timeout"})
    assert not handover.handover_to_desktop("test-thread")["ok"]
    assert not closed and not opened


def test_new_activity_during_read_prevents_close(monkeypatch, owner):
    closed, opened, _ = owner
    def rpc(transport, method, params, **kwargs):
        if method == "thread/loaded/list":
            return {"result": {"data": ["test-thread"]}}
        if method in ("thread/queue/list", "thread/backgroundTerminals/list"):
            return {"result": {"data": []}}
        server._CODEX_APP_SERVER_THREAD_STATE["test-thread"] = {"active_turn_id": "next-turn"}
        return {"result": {"thread": {"status": {"type": "idle"}}}}
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    assert handover.handover_to_desktop("test-thread")["pending"]
    assert not closed and not opened


def test_managed_transport_is_never_terminated(owner):
    closed, opened, transport = owner
    transport.proc = None
    result = handover.handover_to_desktop("test-thread")
    assert not result["ok"] and not result.get("pending")
    assert not closed and not opened


@pytest.mark.parametrize("busy_method", ["thread/queue/list", "thread/backgroundTerminals/list"])
def test_native_queued_or_background_work_prevents_close(monkeypatch, owner, busy_method):
    closed, opened, _ = owner
    original = server._codex_app_server_request_to_transport
    def rpc(transport, method, params, **kwargs):
        if method == busy_method:
            return {"result": {"data": [{"id": "work"}]}}
        return original(transport, method, params, **kwargs)
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    assert handover.handover_to_desktop("test-thread")["pending"]
    assert not closed and not opened


def test_pagination_includes_other_active_threads(monkeypatch, owner):
    closed, opened, _ = owner
    original = server._codex_app_server_request_to_transport
    def rpc(transport, method, params, **kwargs):
        if method == "thread/loaded/list":
            return {"result": {"data": ["another-thread"] if params.get("cursor") else ["test-thread"],
                               "nextCursor": None if params.get("cursor") else "next"}}
        if method == "thread/read" and params["threadId"] == "another-thread":
            return {"result": {"thread": {"status": {"type": "active"}}}}
        return original(transport, method, params, **kwargs)
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    assert handover.handover_to_desktop("test-thread")["pending"]
    assert not closed and not opened


def test_native_send_gate_blocks_writes_during_handover(monkeypatch, owner):
    from ccc_server import codex
    monkeypatch.setattr(handover, "_in_progress", True)
    response = codex._codex_app_server_request_to_transport(
        owner[2], "thread/resume", {"threadId": "test-thread"})
    assert response["code"] == "codex_handover_pending"
    assert not owner[0]


def test_missing_desktop_does_not_release_owner(monkeypatch, owner):
    closed, opened, _ = owner
    monkeypatch.setattr(handover, "desktop_available", lambda: False)
    result = handover.handover_to_desktop("test-thread")
    assert not result["ok"] and not closed and not opened


def test_handover_uses_recorded_workspace_not_browser_cwd(monkeypatch):
    from ccc_server import codex_client
    monkeypatch.setattr(codex_client, "_client_scope", lambda *a, **k: {})
    monkeypatch.setattr(codex_client, "_client_read_thread", lambda tid: {"id": tid, "cwd": "/tmp/recorded"})
    calls = []
    monkeypatch.setattr(handover, "handover_to_desktop", lambda tid, cwd=None: calls.append((tid, cwd)) or {"ok": True})
    result = codex_client.codex_client_dispatch("handover", {
        "context": {"thread_id": "test-thread", "repo_path": "/tmp/recorded", "cwd": "/tmp/wrong"}})
    assert result["ok"]
    assert calls == [("test-thread", "/tmp/recorded")]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_desktop_deeplink_uses_canonical_thread_and_checks_exit(monkeypatch, tmp_path, exit_code):
    from ccc_server import session_graph
    monkeypatch.setattr(server, "_is_codex_session", lambda sid: True)
    monkeypatch.setattr(server, "repo_from_session", lambda sid: {"repo_path": str(tmp_path)})
    monkeypatch.setattr(server, "repo_log_dir", lambda repo: tmp_path)
    monkeypatch.setattr(session_graph.sys, "platform", "darwin")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=exit_code)
    monkeypatch.setattr(session_graph.subprocess, "run", run)
    result = session_graph.open_session_in_codex_desktop("test-thread/one", cwd="/tmp/wrong")
    assert calls == [["open", "-b", "com.openai.codex", "codex://threads/test-thread%2Fone"]]
    assert result["ok"] is (exit_code == 0)


def test_unrelated_url_handler_is_not_desktop(monkeypatch):
    import ctypes
    monkeypatch.setattr(handover.sys, "platform", "darwin")
    def get_string(handler, buffer, size, encoding):
        buffer.value = b'com.example.other-app'
        return True
    cf = SimpleNamespace(CFStringCreateWithCString=lambda *a: 1,
                         CFStringGetCString=get_string, CFRelease=lambda pointer: None)
    ls = SimpleNamespace(LSCopyDefaultHandlerForURLScheme=lambda scheme: 2)
    monkeypatch.setattr(ctypes, "CDLL", lambda target: cf if 'CoreFoundation' in target else ls)
    assert not handover.desktop_available()


def test_temporary_conversation_is_not_destroyed(monkeypatch, owner):
    closed, opened, _ = owner
    original = server._codex_app_server_request_to_transport
    def rpc(transport, method, params, **kwargs):
        if method == "thread/read":
            return {"result": {"thread": {"ephemeral": True, "status": {"type": "idle"}}}}
        return original(transport, method, params, **kwargs)
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    result = handover.handover_to_desktop("test-thread")
    assert not result["ok"]
    assert not closed and not opened
