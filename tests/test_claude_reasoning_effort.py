"""Regression coverage for Claude's model-picker reasoning effort."""

from pathlib import Path

import server


def test_claude_accepts_max_reasoning_effort(monkeypatch):
    monkeypatch.setattr(server, "_detect_session_engine", lambda _sid: "claude")
    monkeypatch.setattr(server, "_set_session_override", lambda *args: None)
    monkeypatch.setattr(server, "session_live_status", lambda *_args: {})
    monkeypatch.setattr(server, "find_session_cwd", lambda _sid: "")

    result = server._set_session_model("sid", "opus-5", False, "max", effort_only=True)

    assert result["ok"] is True
    assert result["reasoning_effort"] == "max"


def test_claude_picker_uses_effort_only_requests_and_resume_flag():
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    server_py = Path("server.py").read_text(encoding="utf-8")

    assert "CLAUDE_REASONING_LEVELS" in app_js
    assert "effort_only = true" in app_js
    assert 'f"/effort {reasoning_effort}"' in server_py
    assert 'cmd.extend(["--effort", effort])' in server_py
