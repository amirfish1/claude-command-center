"""CCC-48: Devin's shared `devin acp` conn lives in the worker, like kimi/grok.

The ACP server talks over stdio pipes to the process that spawned it. Owned
by the dashboard, every dashboard restart (each CCC auto-pull) killed its
running turns. These tests pin the routing: the dashboard asks the worker,
and the worker's engine host runs the conn-owner logic.
"""
import importlib
from unittest import mock

import pytest

from worker_engines import ASYNC_OPERATIONS, EngineHost


def _server():
    return importlib.import_module("server")


def _host(legacy):
    host = EngineHost(mock.Mock())
    host._module = legacy
    return host


def test_devin_is_worker_routed():
    assert "devin" in _server()._ACP_WORKER_HARNESSES


def test_devin_turns_are_tracked_as_running_work():
    """A running Devin turn keeps the worker busy, so an automatic worker
    restart waits for it."""
    assert ("devin", "prompt") in ASYNC_OPERATIONS
    assert ("devin", "acp_spawn") in ASYNC_OPERATIONS


def test_spawn_runs_in_the_worker_when_routed():
    server = _server()
    routed = {"ok": True, "session_id": "raw-9", "work_id": "w1"}
    with mock.patch.object(server, "_control_plane_routes_engines", return_value=True), \
         mock.patch.object(server, "_control_plane_engine_call", return_value=routed) as call, \
         mock.patch.object(server, "_acp_new_session") as local_new:
        out = server._devin_acp_spawn_new_session(
            "hello", "/tmp/x", model="swe-2", permission_mode="dangerous",
        )
    assert out == routed
    local_new.assert_not_called()
    engine, operation, args = call.call_args[0]
    assert (engine, operation) == ("devin", "acp_spawn")
    assert args["prompt"] == server._devin_session_state_prompt("hello")
    assert args["cwd"] == "/tmp/x"
    assert args["model"] == "swe-2"


def test_ambiguous_spawn_never_falls_back_to_a_second_session():
    """A worker timeout after send may already have created the session; the
    caller's `devin -p` fallback would start a duplicate."""
    server = _server()
    with mock.patch.object(server, "_control_plane_routes_engines", return_value=True), \
         mock.patch.object(server, "_control_plane_engine_call",
                           return_value={"ok": False, "ambiguous": True}):
        with pytest.raises(RuntimeError):
            server._devin_acp_spawn_new_session("hello", "/tmp/x")


def test_routed_prompt_carries_the_session_cwd():
    """The worker may hold no state for a dormant session; cwd lets it
    session/load in the right directory."""
    server = _server()
    with mock.patch.object(server, "_control_plane_engine_call",
                           return_value={"ok": True}) as call:
        server._acp_prompt("devin", "raw-1", "hi", cwd="/repo")
    assert call.call_args[0][2]["cwd"] == "/repo"


def test_worker_host_runs_devin_spawn_and_prompt():
    legacy = mock.Mock()
    legacy._devin_acp_spawn_new_session.return_value = {"ok": True, "session_id": "r"}
    legacy._acp_prompt.return_value = {"ok": True}
    host = _host(legacy)
    host._call("devin", "acp_spawn", {"prompt": "p", "cwd": "/c", "model": "m"})
    legacy._devin_acp_spawn_new_session.assert_called_once_with(
        "p", "/c", model="m", permission_mode=None, reasoning_effort=None,
    )
    host._call("devin", "prompt", {"session_id": "r", "text": "t", "cwd": "/c"})
    assert legacy._acp_prompt.call_args.kwargs["cwd"] == "/c"


def test_worker_host_never_routes_devin_spawn_to_grok():
    legacy = mock.Mock()
    with pytest.raises(ValueError):
        _host(legacy)._call("devin", "spawn", {"prompt": "p"})
    legacy.spawn_session_grok.assert_not_called()


def test_worker_host_reports_devin_availability():
    legacy = mock.Mock()
    legacy._acp_resolve_bin.return_value = {"available": True, "bin": "/b/devin"}
    legacy._acp_conn.return_value = None
    out = _host(legacy)._call("devin", "availability", {})
    legacy._acp_resolve_bin.assert_called_once_with("devin")
    assert out["availability"]["available"] is True


def test_close_idle_conn_refuses_while_a_session_is_mid_turn(monkeypatch):
    server = _server()
    monkeypatch.setenv("CCC_CONTROL_PLANE_ENGINES", "0")
    transport = mock.Mock()
    monkeypatch.setitem(server._ACP_CONNS, "devin", {"transport": transport})
    monkeypatch.setitem(server._ACP_SESSION_STATE, "devin", {"a": {"status": "active"}})
    assert server._acp_close_idle_conn("devin") == {"ok": True, "busy": 1, "closed": False}
    transport.close.assert_not_called()
    monkeypatch.setitem(server._ACP_SESSION_STATE, "devin", {"a": {"status": "idle"}})
    assert server._acp_close_idle_conn("devin")["closed"] is True
    transport.close.assert_called_once()


def test_snapshot_reports_whether_the_live_conn_has_the_session(monkeypatch):
    server = _server()
    monkeypatch.setenv("CCC_CONTROL_PLANE_ENGINES", "0")
    transport = mock.Mock()
    transport.alive.return_value = True
    conn = {"transport": transport}
    monkeypatch.setitem(server._ACP_CONNS, "devin", conn)
    monkeypatch.setitem(server._ACP_SESSION_STATE, "devin", {
        "on": {"status": "idle", "loaded_conn": id(conn)},
        "off": {"status": "idle", "loaded_conn": None},
    })
    assert server._acp_session_snapshot("devin", "on")["loaded"] is True
    assert server._acp_session_snapshot("devin", "off")["loaded"] is False
    assert server._devin_acp_session_loaded("on") is True
