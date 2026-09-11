"""Durable CCC-owned Aider session adapter coverage."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _fresh_server():
    for name in ("server", "morning", "morning_store"):
        sys.modules.pop(name, None)
    return importlib.import_module("server")


def _wait_for_exit(server, pid, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for entry in server._spawned_sessions:
            if entry.get("pid") == pid and server._poll_spawn_entry(entry) is not None:
                return
        time.sleep(0.03)
    raise AssertionError(f"Aider child {pid} did not exit")


def test_aider_sessions_have_ccc_owned_durable_ids_and_transcripts(tmp_path, monkeypatch):
    server = _fresh_server()
    repo = tmp_path / "repo"
    repo.mkdir()
    sessions = tmp_path / "aider-sessions"
    fake = tmp_path / "aider"
    fake.write_text("#!/bin/sh\nprintf 'Aider reply for: %s\\n' \"$*\"\n", encoding="utf-8")
    fake.chmod(0o755)

    monkeypatch.setattr(server, "AIDER_SESSIONS_DIR", sessions)
    monkeypatch.setenv("CCC_AIDER_BIN", str(fake))

    started = server.spawn_session_aider("first task", cwd=str(repo), repo_path=str(repo))

    assert started["ok"] is True
    assert started["engine"] == "aider"
    sid = started["session_id"]
    assert sid
    _wait_for_exit(server, started["pid"])

    rows = server.find_aider_conversations(repo_path=str(repo), repo_only=True)
    assert [row["session_id"] for row in rows] == [sid]
    assert rows[0]["source"] == "aider"

    initial = server.parse_conversation(sid, use_cache=False)["events"]
    assert [event["type"] for event in initial] == ["user_text", "assistant", "result"]
    assert initial[0]["text"] == "first task"
    assert "Aider reply" in initial[1]["blocks"][0]["text"]

    resumed = server.resume_session_aider(sid, "second task")
    assert resumed["ok"] is True
    assert resumed["session_id"] == sid
    _wait_for_exit(server, resumed["pid"])

    events = server.parse_conversation(sid, use_cache=False)["events"]
    assert [event["type"] for event in events] == [
        "user_text", "assistant", "result", "user_text", "assistant", "result",
    ]
    assert events[3]["text"] == "second task"
    assert server._detect_session_engine(sid) == "aider"


def test_aider_does_not_ingest_project_level_history(tmp_path, monkeypatch):
    server = _fresh_server()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".aider.chat.history.md").write_text("# unrelated project history\n", encoding="utf-8")
    monkeypatch.setattr(server, "AIDER_SESSIONS_DIR", tmp_path / "aider-sessions")

    assert server.find_aider_conversations(repo_path=str(repo), repo_only=True) == []


def test_aider_is_in_orchestration_spawn_engines_dispatch_chain():
    """Aider's spawn function existed but was never reachable via
    /api/sessions/spawn (W2-2's own scope note) -- this pins the payload
    normalization + engine allowlist side of that wiring (W3-3)."""
    server = _fresh_server()
    assert "aider" in server._ORCHESTRATION_SPAWN_ENGINES
    engine, model = server._spawn_request_engine_and_model({"engine": "aider"})
    assert engine == "aider"
