"""A headless Claude turn that is between tools must still read as working."""

from pathlib import Path

SERVER_PY = (Path(__file__).resolve().parents[1] / "server.py").read_text(
    encoding="utf-8",
)


def test_session_status_marks_open_headless_turn_active():
    handler = SERVER_PY.split('elif path == "/api/session-status":', 1)[1].split(
        "# Authoritative AskUserQuestion signal", 1,
    )[0]
    branch = handler.split("_headless_turn_in_progress(spawn)", 1)[1]
    assert 'status["sidecar_status"] = "active"' in branch.split("# Authoritative", 1)[0]
