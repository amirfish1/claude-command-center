"""Isolated exact-session/no-queue transport contract tests.

The fake RPC is the already-connected managed transport, never an agent CLI.
HTTP cases run only an ephemeral local test server.
"""
import json
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import server
from ccc_server import codex, codex_handover, continuation, runaway_hold

SID = "exact-thread"
FLAGS = {"same_session": True, "no_queue": True, "skip_wt": True, "wt_origin": True}


@pytest.fixture
def connected(monkeypatch):
    transport = SimpleNamespace(kind="managed-unix")
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", transport)
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_LOCK", threading.Condition(threading.RLock()))
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_THREAD_STATE", {})
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TURN_THREAD", {})
    monkeypatch.setattr(server, "_is_codex_session", lambda sid: True)
    monkeypatch.setattr(server, "_get_session_override", lambda sid: None)
    monkeypatch.setattr(server, "_codex_app_server_is_live", lambda: True)
    monkeypatch.setattr(server, "_codex_app_server_thread_state", lambda sid: dict(server._CODEX_APP_SERVER_THREAD_STATE.get(sid, {})))
    monkeypatch.setattr(server, "_codex_thread_writer_snapshot", lambda sid: {"writer": None, "external_active": False})
    locks = {}
    monkeypatch.setattr(server, "_codex_thread_turn_lock", lambda sid: locks.setdefault(sid, threading.Lock()))
    calls = []

    def rpc(current, method, params, **kwargs):
        assert current is transport
        calls.append((method, params))
        if method == "thread/read":
            return {"result": {"thread": {"id": params["threadId"], "status": {"type": "idle"}, "turns": []}}}
        assert method == "turn/start"
        return {"result": {"turn": {"id": "accepted-turn"}}}

    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    # A regression must fail loudly before any queue/forwarder/WT callback or
    # competing writer can receive input, even if its usual backend is on.
    forbidden = []
    for name in (
        "_federation_resolve_target", "_claude_subagent_parent_session_id",
        "_canonical_kimi_session_id", "_control_plane_engine_call",
        "_ensure_codex_app_server", "_codex_app_server_request",
        "_codex_turn_params", "_spawn_model_for_engine",
        "_queue_terminal_input", "resume_session_headless",
        "_try_wt_send_for_headless_delivery", "_try_uds_peer_delivery",
        "_inject_dedupe_acquire", "_inject_dedupe_record",
        "_note_pending_queued", "_save_pending_inputs", "resume_desktop_conversation",
    ):
        if hasattr(server, name):
            blocked = Mock(side_effect=AssertionError(name))
            monkeypatch.setattr(server, name, blocked)
            forbidden.append(blocked)
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "wt")
    yield calls, transport
    for blocked in forbidden:
        blocked.assert_not_called()


def inject(**kwargs):
    return server._inject_text_into_session(SID, "takeover prompt", **(FLAGS | kwargs))


def test_exact_idle_thread_direct_start(connected):
    calls, _ = connected
    result = inject(mode="steer")
    assert result["ok"] and result["same_session"] is True
    assert result["queued"] is False
    assert result["session_id"] == SID
    assert result["turn_id"] == "accepted-turn"
    assert [method for method, _ in calls] == ["thread/read", "turn/start"]
    assert calls[1][1] == {"threadId": SID, "input": [{"type": "text", "text": "takeover prompt"}]}
    assert server._CODEX_APP_SERVER_THREAD_STATE[SID]["active_turn_id"] == "accepted-turn"


def test_router_also_enforces_scope_before_parent_and_control_plane(connected):
    result = server._inject_text_into_session_router(SID, "hello", **FLAGS)
    assert result["same_session"] is True


@pytest.mark.parametrize("override,expected", [
    (None, {}), ({}, {}),
    ({"model": "chosen-test-model"}, {"model": "chosen-test-model"}),
    ({"reasoning_effort": "medium"}, {"effort": "medium"}),
    ({"model": "chosen-test-model", "reasoning_effort": "high", "context_1m": True,
      "cwd": "/other-workspace", "approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}},
     {"model": "chosen-test-model", "effort": "high"}),
    ({"model": "", "reasoning_effort": ""}, {}),
])
def test_native_settings_preserved_except_explicit_session_model_effort(connected, monkeypatch, override, expected):
    monkeypatch.setattr(server, "_get_session_override", lambda sid: override)
    monkeypatch.setenv("CCC_CODEX_MODEL", "unrelated-default-model")
    original = server._codex_app_server_request_to_transport
    def rpc(transport, method, params, **kwargs):
        response = original(transport, method, params, **kwargs)
        if method == "thread/read":
            response["result"]["thread"].update({
                "model": "native-thread-model", "effort": "low", "cwd": "/native-workspace",
                "approvalPolicy": "on-request", "sandboxPolicy": {"type": "readOnly"},
            })
        return response
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    assert inject()["same_session"] is True
    params = connected[0][1][1]
    # An exact allowlist proves no cwd, workspace, approval or sandbox
    # expansion; absence of model/effort lets Codex retain native settings.
    assert params == {"threadId": SID, "input": [{"type": "text", "text": "takeover prompt"}], **expected}


def test_real_managed_rpc_wait_releases_recursive_condition_for_reader(connected, monkeypatch):
    calls, transport = connected
    readers = []
    replies = []
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_RESPONSES", {})
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_INFLIGHT_LOCK", threading.Lock())
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_INFLIGHT", 0)
    monkeypatch.setattr(server, "_app_server_trace", lambda *a, **kw: None)
    monkeypatch.setattr(codex_handover, "in_progress", lambda: False)
    monkeypatch.setattr(codex, "_CODEX_APP_SERVER_NEXT_ID", 1)
    transport.proc = SimpleNamespace(pid=None)
    transport.reader_exited = False

    def send_json(request):
        calls.append((request["method"], request["params"]))
        def receive():
            # The real RPC primitive acquires this condition once; scoped
            # turn/start holds it recursively. Both levels must be released
            # by Condition.wait before this separate reader can respond.
            with server._CODEX_APP_SERVER_LOCK:
                if request["method"] == "thread/read":
                    result = {"thread": {"id": SID, "status": {"type": "idle"}, "turns": []}}
                else:
                    result = {"turn": {"id": "accepted-turn"}}
                    assert server._CODEX_APP_SERVER_THREAD_STATE[SID]["ccc_turn_start_pending"]
                server._CODEX_APP_SERVER_RESPONSES[request["id"]] = {"result": result}
                replies.append(request["method"])
                server._CODEX_APP_SERVER_LOCK.notify_all()
        reader = threading.Thread(target=receive, daemon=True)
        readers.append(reader)
        reader.start()

    transport.send_json = send_json
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", codex._codex_app_server_request_to_transport)
    result = inject()
    for reader in readers:
        reader.join(timeout=1)
        assert not reader.is_alive()
    assert result["same_session"] is True and result["queued"] is False
    assert replies == ["thread/read", "turn/start"]
    assert server._CODEX_APP_SERVER_INFLIGHT == 0
    assert server._CODEX_APP_SERVER_RESPONSES == {}


@pytest.mark.parametrize("options", [
    {"same_session": False}, {"no_queue": False}, {"skip_wt": False}, {"wt_origin": False},
    {"force_queue": True}, {"force_terminal": True}, {"force_headless": True},
    {"mode": "send_queue"}, {"mode": "abort"}, {"preserve_queued_steer": True},
])
def test_invalid_scope_never_calls_transport(connected, options):
    result = inject(**options)
    assert not result["ok"] and not result["same_session"] and not result["queued"]
    assert connected[0] == []


@pytest.mark.parametrize("state", [
    {"active_turn_id": "busy"}, {"ccc_turn_start_pending": True},
    {"status": "active"}, {"thread_needs_approval": True},
    {"active_flags": ["waitingOnApproval"]}, {"pending_approval_request": "approval"},
    {"active_items": {"tool": {}}},
])
def test_busy_local_state_never_starts(connected, state):
    server._CODEX_APP_SERVER_THREAD_STATE[SID] = state
    assert inject()["code"] == "session_busy"
    assert connected[0] == []


def test_contended_lock_does_not_wait_or_break_lock(connected):
    lock = server._codex_thread_turn_lock(SID)
    with lock:
        assert inject()["code"] == "session_busy"
    assert connected[0] == []


@pytest.mark.parametrize("thread", [
    {"id": "replacement", "status": {"type": "idle"}},
    {"id": SID, "status": {"type": "active"}},
    {"id": SID, "status": {"type": "notLoaded"}},
    {"id": SID},
    {"id": SID, "status": {"type": "idle"}, "turns": [{"id": "busy", "status": "inProgress"}]},
])
def test_wire_unknown_busy_or_wrong_thread_does_not_start(connected, monkeypatch, thread):
    rpc = Mock(return_value={"result": {"thread": thread}})
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    assert inject()["code"] == "session_not_idle"
    assert rpc.call_count == 1
    assert rpc.call_args.args[1] == "thread/read"


@pytest.mark.parametrize("snapshot", [{"external_active": True}, {"writer": "unknown"}, {"writer": "ccc"}])
def test_writer_gate_never_falls_back(connected, monkeypatch, snapshot):
    monkeypatch.setattr(server, "_codex_thread_writer_snapshot", lambda sid: snapshot)
    assert inject()["code"] == "session_busy"
    assert len(connected[0]) == 1


@pytest.mark.parametrize("kind", [None, "stdio"])
def test_no_current_managed_transport_never_routes_to_worker(connected, monkeypatch, kind):
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", None if kind is None else SimpleNamespace(kind=kind))
    assert inject()["code"] == "scoped_transport_unavailable"
    assert connected[0] == []


def test_native_non_codex_uses_engine_neutral_broker(connected, monkeypatch):
    from ccc_server import takeover_sessions
    monkeypatch.setattr(server, "_is_codex_session", lambda sid: False)
    calls = []
    monkeypatch.setattr(takeover_sessions, "deliver", lambda sid, text, **kw:
                        calls.append((sid, text, kw)) or {"ok": False, "queued": False, "code": "session_unknown"})
    assert inject()["code"] == "session_unknown"
    assert calls and calls[0][0] == SID
    assert connected[0] == []


def test_connection_changes_after_read_never_starts(connected, monkeypatch):
    def snapshot(sid):
        monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", object())
        return {}
    monkeypatch.setattr(server, "_codex_thread_writer_snapshot", snapshot)
    assert not inject()["ok"]
    assert len(connected[0]) == 1


def test_thread_becomes_busy_after_read_never_starts(connected, monkeypatch):
    def snapshot(sid):
        server._CODEX_APP_SERVER_THREAD_STATE[sid] = {"active_turn_id": "other-turn"}
        return {}
    monkeypatch.setattr(server, "_codex_thread_writer_snapshot", snapshot)
    assert inject()["code"] == "session_busy"
    assert len(connected[0]) == 1


def test_unreachable_read_never_starts_or_falls_back(connected, monkeypatch):
    rpc = Mock(side_effect=OSError("unreachable"))
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    result = inject()
    assert not result["ok"] and not result["queued"] and not result["same_session"]
    assert rpc.call_count == 1


@pytest.mark.parametrize("reply", [
    {"ok": False, "error": "timeout", "fallback": "queue"}, RuntimeError("lost reply"),
    None, {"result": {"turn": {}}}, {"result": {"turn": {"id": []}}},
])
def test_ambiguous_start_has_no_queue_and_blocks_competing_retry(connected, monkeypatch, reply):
    original = server._codex_app_server_request_to_transport
    def rpc(transport, method, params, **kwargs):
        if method == "turn/start":
            if isinstance(reply, Exception):
                raise reply
            return reply
        return original(transport, method, params, **kwargs)
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    result = inject()
    assert not result["ok"] and result["uncertain"] and not result["same_session"]
    assert result["queued"] is False and "fallback" not in result
    assert inject()["code"] == "session_busy"


def test_definitive_rejection_has_no_fallback(connected, monkeypatch):
    original = server._codex_app_server_request_to_transport
    def rpc(transport, method, params, **kwargs):
        if method == "turn/start":
            return {"error": {"code": -32000, "message": "thread not loaded"}}
        return original(transport, method, params, **kwargs)
    monkeypatch.setattr(server, "_codex_app_server_request_to_transport", rpc)
    result = inject()
    assert result["code"] == "delivery_rejected" and not result["same_session"]
    assert not server._CODEX_APP_SERVER_THREAD_STATE[SID].get("ccc_turn_start_pending")


@pytest.fixture
def endpoint(connected, monkeypatch):
    monkeypatch.setattr(server, "_handoff_lease_guard", lambda sid: None)
    monkeypatch.setattr(runaway_hold, "is_held", lambda sid: False)
    monkeypatch.setattr(continuation, "forward_target", Mock(side_effect=AssertionError("lineage forwarding")))
    monkeypatch.setattr(server, "_resolve_bridge_session_alias", Mock(side_effect=AssertionError("alias rewriting")))
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    def get_capabilities():
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/takeover/capabilities", timeout=5) as response:
            return response.code, json.load(response)
    def post(**changes):
        payload = {"session_id": SID, "text": "takeover prompt", "same_session": True,
                   "no_queue": True, "skip_wt": True, "origin": "wt"} | changes
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/inject-input",
                                         data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.code, json.load(response)
    def get_observation(**params):
        url = f"http://127.0.0.1:{httpd.server_port}/api/takeover/observe?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.code, json.load(response)
    post.observation = get_observation
    post.capabilities = get_capabilities
    yield post
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_endpoint_delivers_without_continuation_or_alias_rewrite(endpoint, connected):
    status, result = endpoint()
    assert status == 200 and result["same_session"] is True
    assert connected[0][1][1]["threadId"] == SID


def test_capability_handshake_does_not_start_or_probe_agents(endpoint, connected):
    status, result = endpoint.capabilities()
    assert status == 200
    assert result["schema_version"] == 1
    assert result["same_session"] is True and result["no_queue"] is True
    assert result["observation"] is True and "devin" in result["engines"]
    assert connected[0] == []


@pytest.mark.parametrize("changes", [
    {"same_session": "true"}, {"no_queue": 1}, {"no_queue": False},
    {"same_session": False}, {"skip_wt": False}, {"origin": "ccc"},
    {"force_queue": True}, {"mode": "queue"}, {"mode": "abort"},
    {"force_terminal": True}, {"replace_queued": True}, {"stop": True},
    {"presentation_bootstrap": True}, {"text": []}, {"session_id": 12},
])
def test_endpoint_rejects_broader_operations_before_routing(endpoint, connected, changes):
    status, result = endpoint(**changes)
    assert status == 400 and not result["ok"] and not result["same_session"]
    assert connected[0] == []


def test_endpoint_respects_hold_without_delivery(endpoint, connected, monkeypatch):
    monkeypatch.setattr(runaway_hold, "is_held", lambda sid: True)
    status, result = endpoint()
    assert status == 409 and not result["same_session"]
    assert connected[0] == []


def test_observation_get_forwards_exact_engine_sid_and_nonce(endpoint, monkeypatch):
    from ccc_server import takeover_sessions
    calls = []
    monkeypatch.setattr(takeover_sessions, "observe", lambda sid, engine, nonce:
                        calls.append((sid, engine, nonce)) or {"schema_version": 1, "session_id": sid, "engine": engine})
    status, result = endpoint.observation(session_id="devincli-native-slug", engine="devin", nonce="[nonce exact]")
    assert status == 200 and result["engine"] == "devin"
    assert calls == [("devincli-native-slug", "devin", "[nonce exact]")]


def test_scoped_endpoint_forwards_engine_to_broker(endpoint, connected, monkeypatch):
    from ccc_server import takeover_sessions
    monkeypatch.setattr(server, "_is_codex_session", lambda sid: False)
    calls = []
    monkeypatch.setattr(takeover_sessions, "deliver", lambda sid, text, **kw:
                        calls.append((sid, text, kw)) or {"ok": True, "same_session": True, "queued": False})
    status, result = endpoint(engine="custom-engine")
    assert status == 200 and result["same_session"] is True
    assert calls == [(SID, "takeover prompt", {"engine": "custom-engine"})]
