"""Pi engine adapter (W3-3): bin resolution + a clean "not installed" stub.

No `pi` CLI exists to test a real spawn against (see ccc_server/pi.py's
module docstring) -- these tests only cover the honest-stub contract: clean
unavailability when the CLI is missing, and no subprocess launched even when
a binary is present, since spawn always reports "not wired" today.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _fresh_server():
    for name in ("server", "morning", "morning_store"):
        sys.modules.pop(name, None)
    return importlib.import_module("server")


def test_pi_spawn_reports_unavailable_cleanly_when_cli_missing(tmp_path, monkeypatch):
    server = _fresh_server()
    monkeypatch.delenv("CCC_PI_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # no `pi` anywhere on PATH

    result = server.spawn_session_pi("task")
    assert result["ok"] is False
    assert result["code"] == "pi_unavailable"
    assert "not found" in result["error"].lower()


def test_pi_spawn_reports_invalid_env_bin_cleanly(tmp_path, monkeypatch):
    server = _fresh_server()
    monkeypatch.setenv("CCC_PI_BIN", str(tmp_path / "does-not-exist"))

    result = server.spawn_session_pi("task")
    assert result["ok"] is False
    assert result["code"] == "pi_unavailable"


def test_pi_spawn_never_launches_a_subprocess_even_when_bin_present(tmp_path, monkeypatch):
    server = _fresh_server()
    marker = tmp_path / "ran.txt"
    fake = tmp_path / "pi"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("CCC_PI_BIN", str(fake))

    result = server.spawn_session_pi("task")
    assert result["ok"] is False
    assert result["code"] == "pi_unavailable"
    assert "not yet wired" in result["error"].lower()
    assert not marker.exists()
