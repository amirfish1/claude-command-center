"""Grok ACP harness registration and spawn wiring."""
import pathlib
from types import SimpleNamespace
from unittest import mock

import server
import pytest
from ccc_server import acp

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def permission_timeout_probe(monkeypatch):
    timers, responses, events = [], [], []

    class Transport:
        def alive(self):
            return True

        def send_json(self, payload):
            responses.append((("grok", payload["id"]), {"result": payload["result"]}))

    class Timer:
        def __init__(self, interval, function, args=()):
            self.interval, self.function, self.args = interval, function, args
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

        def fire(self):
            self.function(*self.args)

    monkeypatch.setattr(acp.threading, "Timer", Timer)
    monkeypatch.setattr(server, "_ACP_SESSION_STATE", {})
    monkeypatch.setattr(server, "_ACP_CONNS", {"grok": {"transport": Transport()}})
    monkeypatch.setattr(server, "_acp_emit_event_unlocked", lambda harness, sid, event, **kw: events.append(event))
    monkeypatch.setattr(server, "_acp_respond", lambda *args, **kw: responses.append((args, kw)) or True)
    monkeypatch.setattr(acp, "_acp_save_state_unlocked", lambda *args: None)
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *args, **kw: None)
    monkeypatch.delenv("CCC_GROK_PERMISSION_TIMEOUT_SECONDS", raising=False)
    return timers, responses, events


def _request_permission(harness="grok"):
    acp._acp_handle_agent_request(harness, 7, "session/request_permission", {
        "sessionId": "permission-timeout-session",
        "toolCall": {"toolCallId": "tool-7", "title": "Run command"},
        "options": [{"optionId": "allow_once", "kind": "allow_once"}],
    })


def test_grok_unanswered_permission_expires_without_approval(permission_timeout_probe):
    timers, responses, events = permission_timeout_probe
    _request_permission()
    assert len(timers) == 1
    assert timers[0].interval == 300
    timers[0].fire()
    assert responses == [(("grok", 7), {"result": {"outcome": {"outcome": "cancelled"}}})]
    assert not server._acp_session("grok", "permission-timeout-session")["pending_permissions"]
    assert events[-1]["blocks"][0]["tool_status"] == "failed"
    assert "expired" in events[-1]["blocks"][0]["output_preview"]


def test_manual_grok_approval_wins_before_timeout(permission_timeout_probe):
    timers, responses, events = permission_timeout_probe
    _request_permission()
    assert server._acp_resolve_approval("grok", "permission-timeout-session", 7, "allow_once")["ok"]
    assert timers[0].cancelled
    timers[0].fire()
    assert len(responses) == 1
    assert responses[0][1]["result"]["outcome"]["optionId"] == "allow_once"
    assert len(events) == 1


def test_old_timeout_does_not_cancel_reused_request_id(permission_timeout_probe):
    timers, responses, events = permission_timeout_probe
    _request_permission()
    _request_permission()
    timers[0].fire()
    assert responses == []
    assert "7" in server._acp_session("grok", "permission-timeout-session")["pending_permissions"]
    timers[1].fire()
    assert len(responses) == 1


@pytest.mark.parametrize("configured, expected", [("12", 12), ("0", 1), ("-5", 1), ("7200", 3600), ("invalid", 300)])
def test_grok_permission_timeout_configuration_is_bounded(monkeypatch, permission_timeout_probe, configured, expected):
    monkeypatch.setenv("CCC_GROK_PERMISSION_TIMEOUT_SECONDS", configured)
    _request_permission()
    assert permission_timeout_probe[0][0].interval == expected


def test_other_acp_harness_keeps_manual_approval(permission_timeout_probe):
    _request_permission("kimi")
    assert permission_timeout_probe[0] == []
    assert permission_timeout_probe[1] == []


def test_permission_timeout_never_replies_to_replacement_connection(permission_timeout_probe):
    timers, responses, events = permission_timeout_probe
    _request_permission()
    server._ACP_CONNS["grok"] = {"transport": object()}
    timers[0].fire()
    assert responses == []
    assert not server._acp_session("grok", "permission-timeout-session")["pending_permissions"]


def test_timeout_reply_uses_original_transport_if_connection_changes_after_check(monkeypatch, permission_timeout_probe):
    timers, responses, events = permission_timeout_probe
    _request_permission()

    def replace_connection(*args, **kwargs):
        server._ACP_CONNS["grok"] = {"transport": object()}

    monkeypatch.setattr(server, "_acp_emit_event_unlocked", replace_connection)
    timers[0].fire()
    assert responses == [(("grok", 7), {"result": {"outcome": {"outcome": "cancelled"}}})]


def test_connection_exit_cancels_its_permission_timers(monkeypatch, permission_timeout_probe):
    timers, responses, events = permission_timeout_probe
    _request_permission()
    conn = server._ACP_CONNS["grok"]
    conn["transport"].proc = SimpleNamespace(stdout=[])
    monkeypatch.setattr(server, "_ACP_TERMINALS", {})
    monkeypatch.setattr(server, "_ACP_PENDING", {})
    acp._acp_reader("grok", conn)
    assert timers[0].cancelled
    assert not server._acp_session("grok", "permission-timeout-session")["pending_permissions"]
    timers[0].fire()
    assert responses == []


def test_old_reader_permission_is_not_associated_with_new_connection(permission_timeout_probe):
    timers, responses, events = permission_timeout_probe
    old_conn = server._ACP_CONNS["grok"]
    server._ACP_CONNS["grok"] = {"transport": object()}
    acp._acp_handle_message("grok", {
        "id": 7, "method": "session/request_permission",
        "params": {"sessionId": "permission-timeout-session"},
    }, source_conn=old_conn)
    assert timers == []
    assert events == []
    assert responses == []


def test_grok_harness_is_registered():
    cfg = server._ACP_HARNESSES["grok"]
    assert cfg["bin_names"] == ("grok",)
    assert cfg["acp_args"] == ("agent", "--no-leader", "stdio")
    assert cfg["home_default"] == "~/.grok"
    assert "grok" in server._ACP_WORKER_HARNESSES
    assert server._acp_harness_enabled("grok") is True


def test_session_new_sends_yolo_meta(monkeypatch):
    captured = {}
    set_configs = []

    def fake_ensure(_harness):
        return {"initialized": True}

    def fake_request(_harness, method, params, timeout=25):
        captured["method"] = method
        captured["params"] = params
        return {"ok": True, "result": {"sessionId": "01a00000-0000-0000-0000-000000000001",
                                       "models": {"currentModelId": "grok-4.6"}}}

    monkeypatch.setattr(server, "_acp_ensure", fake_ensure)
    monkeypatch.setattr(server, "_acp_request", fake_request)
    monkeypatch.setattr(
        server,
        "_acp_set_config",
        lambda *args, **kwargs: set_configs.append((args, kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(server, "_acp_wire_tail_start", lambda *a, **k: None)
    monkeypatch.setattr(server, "_acp_prompt", lambda *a, **k: {"ok": True})

    result = server._acp_session_new("grok", "/tmp/repo", prompt="hi", mode="yolo")
    assert result["ok"] is True
    assert captured["method"] == "session/new"
    assert captured["params"]["_meta"]["yoloMode"] is True
    assert result["session_id"] == "01a00000-0000-0000-0000-000000000001"
    assert set_configs == []


def test_grok_approval_mode_is_not_sent_as_a_live_config_change(monkeypatch):
    requests = []

    monkeypatch.setattr(
        server,
        "_control_plane_engine_call",
        lambda *args, **kwargs: requests.append((args, kwargs)),
    )
    monkeypatch.setattr(server, "_acp_request", lambda *args, **kwargs: requests.append((args, kwargs)))

    result = server._acp_set_config("grok", "existing-grok-session", "mode", "yolo")

    assert result == {
        "ok": False,
        "code": "grok_approval_mode_fixed_at_creation",
        "error": (
            "Grok approval mode is fixed when the session is created; "
            "spawn a new session with yolo mode enabled."
        ),
    }
    assert requests == []


def test_spawn_session_grok_uses_acp(monkeypatch, tmp_path):
    monkeypatch.setattr(
        server, "_control_plane_engine_call", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        server, "_resolve_grok_bin",
        lambda: {"available": True, "bin": "/usr/bin/grok"},
    )
    monkeypatch.setattr(
        server, "_spawn_repo_context",
        lambda cwd=None, repo_path=None: {
            "cwd": str(tmp_path), "repo_path": str(tmp_path),
        },
    )
    monkeypatch.setattr(server, "_spawn_model_for_engine", lambda *a, **k: "grok-4.6")
    monkeypatch.setattr(
        server, "_acp_session_new",
        lambda *a, **k: {"ok": True, "session_id": "01a00000-aaaa-bbbb-cccc-000000000002"},
    )
    monkeypatch.setattr(server, "_record_spawn_to_registry", lambda **k: None)
    original = list(server._spawned_sessions)
    try:
        server._spawned_sessions.clear()
        result = server.spawn_session_grok("Reply PONG", cwd=str(tmp_path))
    finally:
        server._spawned_sessions[:] = original
    assert result["ok"] is True
    assert result["via"] == "grok-acp"
    assert result["session_id"] == "01a00000-aaaa-bbbb-cccc-000000000002"


def test_grok_snapshot_exposes_pending_permission_without_consuming_it(monkeypatch):
    sid = "grok-pending-permission"
    request_id = "permission-7"
    monkeypatch.setattr(
        server, "_control_plane_engine_call", lambda *a, **k: None,
    )
    server._ACP_SESSION_STATE.pop("grok", None)
    try:
        state = server._acp_session("grok", sid, create=True, cwd="/tmp/repo")
        state["pending_permissions"][request_id] = {
            "req_id": 7,
            "session_id": sid,
            "tool_call": {
                "toolCallId": "tool-1",
                "title": "Run command",
            },
            "options": [
                {"optionId": "allow_once", "name": "Allow once"},
                {"optionId": "reject_once", "name": "Reject"},
            ],
            "requested_at": 123.0,
        }

        snapshot = server._acp_session_snapshot("grok", sid)

        assert snapshot["pending_permission"] == {
            "request_id": request_id,
            "harness": "grok",
            "tool_call": {
                "toolCallId": "tool-1",
                "title": "Run command",
            },
            "options": [
                {"optionId": "allow_once", "name": "Allow once"},
                {"optionId": "reject_once", "name": "Reject"},
            ],
        }
        assert request_id in state["pending_permissions"]
    finally:
        server._ACP_SESSION_STATE.pop("grok", None)


def test_grok_live_status_projects_pending_permission(monkeypatch):
    permission = {
        "request_id": "permission-8",
        "harness": "grok",
        "tool_call": {"toolCallId": "tool-2", "title": "Run command"},
        "options": [{"optionId": "allow_once", "name": "Allow once"}],
    }
    monkeypatch.setattr(server, "_is_kimi_session", lambda _sid: False)
    monkeypatch.setattr(server, "_is_grok_session", lambda _sid: True)
    monkeypatch.setattr(
        server, "_acp_resolve_bin", lambda _harness: {"available": True},
    )
    monkeypatch.setattr(
        server,
        "_acp_session_snapshot",
        lambda _harness, _sid: {
            "status": "active",
            "cwd": "/tmp/repo",
            "model": "grok-4.6",
            "pending_permissions": 1,
            "pending_permission": permission,
        },
    )

    status = server.session_live_status("grok-live-permission", "/tmp/repo")

    assert status["needs_approval"] is True
    assert status["acp_pending_permission"] == permission


def test_ui_and_api_pins():
    server_py = (ROOT / "server.py").read_text(encoding="utf-8")
    engines_py = (ROOT / "ccc_server" / "engines.py").read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    index_html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert '"/api/sessions/spawn-grok"' in server_py
    assert "def spawn_session_grok(" in engines_py
    assert "if (engine === 'grok') return '/api/sessions/spawn-grok';" in app_js
    assert 'option value="grok"' in index_html
