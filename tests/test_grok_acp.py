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


def test_ui_and_api_pins():
    server_py = (ROOT / "server.py").read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    index_html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert '"/api/sessions/spawn-grok"' in server_py
    assert "def spawn_session_grok(" in server_py
    assert "if (engine === 'grok') return '/api/sessions/spawn-grok';" in app_js
    assert 'option value="grok"' in index_html
