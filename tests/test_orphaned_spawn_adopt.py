"""A restart must not cost a live CCC-spawned Claude child its delivery channel.

Failure being guarded: after a dashboard/worker restart, the next message to a
session whose `claude -p` child was spawned before the restart was held as
`orphaned_spawn` and then auto-recovery killed the healthy child and resumed
it. The child was still reading its stdin FIFO the whole time; only the
in-memory spawn handle was gone. These tests pin re-adoption ahead of recovery
and the env leak that made the dashboard run engines in-process.
"""
import importlib
import re
from pathlib import Path

import pytest

importlib.import_module("server")  # wires ccc_server._core before submodules load
from ccc_server import pending_inputs  # noqa: E402

SID = "35fa97b3-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _fresh_throttle(monkeypatch):
    monkeypatch.setattr(pending_inputs, "_orphan_adopt_last", {})
    logged = []
    monkeypatch.setattr(
        pending_inputs._core, "_log_activity",
        lambda *a, **k: logged.append(a),
    )
    return logged


def test_routed_dashboard_asks_worker_to_adopt(monkeypatch, _fresh_throttle):
    calls = []
    monkeypatch.setattr(pending_inputs._core, "_control_plane_routes_engines", lambda: True)

    def request(method, params=None, **kw):
        calls.append(method)
        return {"ok": True, "adopted": 1}

    monkeypatch.setattr(pending_inputs._core, "_control_plane_request", request)
    monkeypatch.setattr(
        pending_inputs, "_worker_owned_claude_input_state",
        lambda sid: {"owned": True, "pid": 4242},
    )
    assert pending_inputs._adopt_orphaned_spawn(SID, now=1000.0) is True
    assert calls == ["engine.adopt"]
    assert any(a[1] == "ADOPT" for a in _fresh_throttle)
    # Throttled: a second tick inside the window does not re-scan.
    assert pending_inputs._adopt_orphaned_spawn(SID, now=1001.0) is False
    assert calls == ["engine.adopt"]


def test_channel_truly_gone_falls_through_to_hold(monkeypatch, _fresh_throttle):
    monkeypatch.setattr(pending_inputs._core, "_control_plane_routes_engines", lambda: True)
    monkeypatch.setattr(
        pending_inputs._core, "_control_plane_request",
        lambda method, params=None, **kw: {"ok": True, "adopted": 0},
    )
    monkeypatch.setattr(
        pending_inputs, "_worker_owned_claude_input_state",
        lambda sid: {"owned": False},
    )
    assert pending_inputs._adopt_orphaned_spawn(SID, now=1000.0) is False
    assert not _fresh_throttle
    # Worker unreachable: also no adoption, recovery keeps its fallback role.
    monkeypatch.setattr(
        pending_inputs._core, "_control_plane_request",
        lambda method, params=None, **kw: {"ok": False, "available": False},
    )
    assert pending_inputs._adopt_orphaned_spawn(SID, now=2000.0) is False


def test_in_process_engine_owner_reattaches_locally(monkeypatch):
    monkeypatch.setattr(pending_inputs._core, "_control_plane_routes_engines", lambda: False)
    reattached = []
    entry = {"pid": 4242, "engine": "claude", "resumed_sid": SID}
    state = {"entry": None}

    def reattach(**kw):
        reattached.append(kw)
        state["entry"] = entry

    monkeypatch.setattr(pending_inputs._core, "_reattach_spawned_orphans", reattach)
    monkeypatch.setattr(
        pending_inputs._core, "_find_live_spawn_entry_for_session",
        lambda sid: state["entry"],
    )
    assert pending_inputs._adopt_orphaned_spawn(SID, now=1000.0) is True
    assert reattached and "claude" in reattached[0]["only_engines"]


def test_spawned_children_do_not_inherit_worker_marker(monkeypatch):
    server = importlib.import_module("server")
    monkeypatch.setenv("CCC_WORKER_PROCESS", "1")
    env = server._question_relay_env()
    assert "CCC_WORKER_PROCESS" not in env
    assert env[server.QUESTION_RELAY_ENV] == "1"
    import os
    assert os.environ.get("CCC_WORKER_PROCESS") == "1"  # worker's own marker kept


def test_dashboard_entry_point_clears_worker_marker():
    src = (Path(__file__).resolve().parent.parent / "server.py").read_text()
    main_block = src[src.index("\ndef _script_main():"):]
    head = main_block[:main_block.index("--archive-refresh-worker")]
    assert re.search(r'os\.environ\.pop\("CCC_WORKER_PROCESS", None\)', head)


def test_boot_reattach_skip_requires_real_routing():
    src = (Path(__file__).resolve().parent.parent / "server.py").read_text()
    block = src[src.index("    worker_owns_engines = ("):]
    block = block[:block.index("\n    )\n")]
    assert "_control_plane_routes_engines()" in block


def test_spawned_children_do_not_inherit_claude_session_identity(monkeypatch):
    server = importlib.import_module("server")
    for key in server._INHERITED_CLAUDE_SESSION_ENV:
        monkeypatch.setenv(key, "leaked")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")  # user config survives
    env = server._question_relay_env()
    for key in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
                "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_CHILD_SESSION"):
        assert key not in env
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"


def test_dashboard_entry_point_clears_claude_session_identity():
    src = (Path(__file__).resolve().parent.parent / "server.py").read_text()
    main_block = src[src.index("\ndef _script_main():"):]
    head = main_block[:main_block.index("--archive-refresh-worker")]
    assert "_INHERITED_CLAUDE_SESSION_ENV" in head
