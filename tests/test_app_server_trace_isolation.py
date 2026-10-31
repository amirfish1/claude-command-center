"""Tests must never append to the user's live app-server-trace.log.

The Codex app-server tests drive Mock transports with fixed clocks; their
trace records (child_pid=<Mock ...>, ts=1200.0) used to land in
~/.claude/command-center/logs/app-server-trace.log, the file used to debug
real app-server failures.
"""
import importlib
import json
from pathlib import Path

server = importlib.import_module("server")

REAL_TRACE = Path.home() / ".claude" / "command-center" / "logs" / "app-server-trace.log"


def test_trace_file_is_isolated_under_tests():
    trace = Path(server.APP_SERVER_TRACE_FILE).resolve()
    assert trace != REAL_TRACE.resolve()
    assert trace != (Path(server.COMMAND_CENTER_STATE_DIR) / "logs" / "app-server-trace.log").resolve()


def test_trace_records_land_in_the_isolated_file():
    marker = "ccc-1199-guard"
    server._app_server_trace("test-guard", marker=marker)
    lines = Path(server.APP_SERVER_TRACE_FILE).read_text().splitlines()
    assert any(json.loads(ln).get("marker") == marker for ln in lines)


def test_trace_rolls_over_at_the_size_cap(tmp_path, monkeypatch):
    """OPS-284: the trace grew unbounded (709M); past the cap it rolls to .1."""
    trace = tmp_path / "app-server-trace.log"
    monkeypatch.setattr(server, "APP_SERVER_TRACE_FILE", trace)
    monkeypatch.setattr(server, "APP_SERVER_TRACE_MAX_BYTES", 4096)
    monkeypatch.setattr(server, "_APP_SERVER_TRACE_FH", None)
    for i in range(400):
        server._app_server_trace("fill", i=i, pad="x" * 40)
    server._app_server_trace("last", marker="after-roll")
    rolled = Path(f"{trace}.1")
    assert rolled.exists()
    assert trace.stat().st_size <= 4096 + 200
    assert rolled.stat().st_size <= 4096 + 200
    assert json.loads(trace.read_text().splitlines()[-1])["marker"] == "after-roll"
    if server._APP_SERVER_TRACE_FH is not None:
        server._APP_SERVER_TRACE_FH.close()
