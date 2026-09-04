"""Factory Droid engine adapter: real spawn path (W3-3).

Droid's spawn used to be a permanent stub (`droid_unavailable` even when the
CLI resolved). These tests cover the real `droid exec` subprocess path via a
fake CLI script, mirroring tests/test_aider_engine.py's pattern.
"""

from __future__ import annotations

import importlib
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
    raise AssertionError(f"Droid child {pid} did not exit")


_FAKE_DROID_SCRIPT = (
    "#!/bin/sh\n"
    "printf 'ARGV:%s\\n' \"$*\"\n"
    "printf 'ENV_ANTHROPIC_API_KEY:%s\\n' \"$ANTHROPIC_API_KEY\"\n"
)


def test_droid_spawn_launches_exec_with_model_and_effort(tmp_path, monkeypatch):
    server = _fresh_server()
    repo = tmp_path / "repo"
    repo.mkdir()
    fake = tmp_path / "droid"
    fake.write_text(_FAKE_DROID_SCRIPT, encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("CCC_DROID_BIN", str(fake))

    result = server.spawn_session_droid(
        "do the thing", cwd=str(repo), repo_path=str(repo),
        model="claude-sonnet-5", reasoning_effort="high",
    )

    assert result["ok"] is True
    assert result["via"] == "droid-spawn"
    _wait_for_exit(server, result["pid"])

    log_text = Path(result["log"]).read_text(encoding="utf-8")
    assert "exec" in log_text
    assert "--auto high" in log_text
    assert "--model claude-sonnet-5" in log_text
    assert "--reasoning-effort high" in log_text
    assert "do the thing" in log_text


def test_droid_spawn_drops_effort_not_in_models_ladder(tmp_path, monkeypatch):
    server = _fresh_server()
    repo = tmp_path / "repo"
    repo.mkdir()
    fake = tmp_path / "droid"
    fake.write_text(_FAKE_DROID_SCRIPT, encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("CCC_DROID_BIN", str(fake))

    # minimax-m3's only reasoning effort is "high" -- "xhigh" must be dropped,
    # not blindly forwarded to a CLI flag the model doesn't support.
    result = server.spawn_session_droid(
        "task", cwd=str(repo), repo_path=str(repo),
        model="minimax-m3", reasoning_effort="xhigh",
    )
    assert result["ok"] is True
    _wait_for_exit(server, result["pid"])
    log_text = Path(result["log"]).read_text(encoding="utf-8")
    assert "--reasoning-effort" not in log_text


def test_droid_spawn_merges_byok_env(tmp_path, monkeypatch):
    server = _fresh_server()
    repo = tmp_path / "repo"
    repo.mkdir()
    fake = tmp_path / "droid"
    fake.write_text(_FAKE_DROID_SCRIPT, encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("CCC_DROID_BIN", str(fake))

    result = server.spawn_session_droid(
        "task", cwd=str(repo), repo_path=str(repo),
        model="claude-sonnet-5", env={"ANTHROPIC_API_KEY": "sk-ant-test-XXXX"},
    )
    assert result["ok"] is True
    _wait_for_exit(server, result["pid"])
    log_text = Path(result["log"]).read_text(encoding="utf-8")
    assert "ENV_ANTHROPIC_API_KEY:sk-ant-test-XXXX" in log_text


def test_droid_spawn_reports_unavailable_cleanly_when_cli_missing(tmp_path, monkeypatch):
    server = _fresh_server()
    monkeypatch.delenv("CCC_DROID_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # no `droid` anywhere on PATH

    result = server.spawn_session_droid("task", cwd=str(tmp_path), repo_path=str(tmp_path))
    assert result["ok"] is False
    assert result["code"] == "droid_unavailable"
