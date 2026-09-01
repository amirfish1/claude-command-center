"""Grok ACP harness registration and spawn wiring."""
import pathlib
from unittest import mock

import server

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_grok_harness_is_registered():
    cfg = server._ACP_HARNESSES["grok"]
    assert cfg["bin_names"] == ("grok",)
    assert cfg["acp_args"] == ("agent", "--no-leader", "stdio")
    assert cfg["home_default"] == "~/.grok"
    assert "grok" in server._ACP_WORKER_HARNESSES
    assert server._acp_harness_enabled("grok") is True


def test_session_new_sends_yolo_meta(monkeypatch):
    captured = {}

    def fake_ensure(_harness):
        return {"initialized": True}

    def fake_request(_harness, method, params, timeout=25):
        captured["method"] = method
        captured["params"] = params
        return {"ok": True, "result": {"sessionId": "01a00000-0000-0000-0000-000000000001",
                                       "models": {"currentModelId": "grok-4.6"}}}

    monkeypatch.setattr(server, "_acp_ensure", fake_ensure)
    monkeypatch.setattr(server, "_acp_request", fake_request)
    monkeypatch.setattr(server, "_acp_set_config", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(server, "_acp_wire_tail_start", lambda *a, **k: None)
    monkeypatch.setattr(server, "_acp_prompt", lambda *a, **k: {"ok": True})

    result = server._acp_session_new("grok", "/tmp/repo", prompt="hi", mode="yolo")
    assert result["ok"] is True
    assert captured["method"] == "session/new"
    assert captured["params"]["_meta"]["yoloMode"] is True
    assert result["session_id"] == "01a00000-0000-0000-0000-000000000001"


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
