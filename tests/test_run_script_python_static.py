"""Regression coverage for the Python interpreter selected by run.sh."""

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_run_script_prefers_the_repo_virtualenv_python():
    script = (PROJECT_ROOT / "run.sh").read_text(encoding="utf-8")

    assert 'PYTHON="$HERE/.venv/bin/python3"' in script
    assert 'exec "$PYTHON" "$HERE/ccc_server/boot.py" "$HERE/server.py"' in script


def test_run_script_accepts_python39():
    script = (PROJECT_ROOT / "run.sh").read_text(encoding="utf-8")

    assert "sys.version_info >= (3, 9)" in script
    assert "requires Python 3.9+" in script


@pytest.mark.parametrize("health, expected", [
    ("", "0 0 0"),
    (json.dumps({"ok": False}), "0 0 0"),
    (json.dumps({"ok": True, "active": 0, "queued": 0, "uncertain": 0}), "0 0 0"),
    *[
        (json.dumps({
            "ok": True, "active": 0, "queued": 0, "uncertain": 0,
            "codex_live_turns": 0, busy_field: 1,
            "worker": {"pid": 123, "capabilities": ["engine-execution-v1"]},
        }), "123 0 1")
        for busy_field in ("active", "queued", "uncertain", "codex_live_turns")
    ],
    (json.dumps({
        "ok": True, "active": 0, "queued": 0, "uncertain": 0,
        "codex_live_turns": 0, "worker": {"pid": 123},
    }), "123 1 0"),
    (json.dumps({
        "ok": True, "active": 0, "queued": 0, "uncertain": 0,
        "codex_live_turns": 0,
        "worker": {"pid": 123, "capabilities": ["engine-execution-v1"]},
    }), "123 1 1"),
])
def test_run_script_expands_worker_health_heredoc(health, expected):
    """Execute the shell expansion: bash -n cannot catch errors inside it."""
    script = (PROJECT_ROOT / "run.sh").read_text(encoding="utf-8")
    start = script.index("    read -r existing_worker_pid existing_worker_idle worker_compatible <<EOF\n")
    end = script.index("\nEOF\n", start) + len("\nEOF\n")
    proc = subprocess.run(
        ["bash", "-eu", "-c", script[start:end] +
         'printf "%s %s %s\\n" "$existing_worker_pid" "$existing_worker_idle" "$worker_compatible"'],
        env={**os.environ, "PYTHON": sys.executable, "worker_health": health},
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected
