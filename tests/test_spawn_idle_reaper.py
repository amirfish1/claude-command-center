"""Unit tests for the spawn idle-TTL reaper (`_reap_idle_spawned_headless`).

CCC-spawned persistent headless workers read stdin from a FIFO opened O_RDWR,
so the child is a writer of its own stdin and never sees EOF — finished
workers idle forever unless the TTL sweep retires them. These tests pin the
policy: who gets retired, every guard that blocks a retire, and the
perf-gate invariant that the sweep does exactly one batched process probe.
"""
import importlib
import json
import os
import time
from pathlib import Path

import pytest

server = importlib.import_module("server")

CLAUDE_CMD = "/Users/someone/.local/bin/claude -p --verbose --input-format stream-json"
NOW = 1_800_000_000.0
TTL_S = server._SPAWN_IDLE_TTL_HOURS_DEFAULT * 3600


def _write_registry(tmp_path, entries):
    reg = tmp_path / "spawned-pids.json"
    reg.write_text(json.dumps(entries))
    return reg


def _entry(pid, tmp_path, *, sid="sid-1234", idle_s=TTL_S * 2, engine="claude", **extra):
    """Registry entry whose spawn log went quiet `idle_s` seconds before NOW."""
    log = tmp_path / f"spawn-{pid}.log"
    log.write_text("x")
    os.utime(log, (NOW - idle_s, NOW - idle_s))
    e = {
        "pid": pid,
        "session_id": sid,
        "name": f"worker-{pid}",
        "log": str(log),
        "fifo": None,
        "spawned_at": "20260101T000000",
        "engine": engine,
    }
    e.update(extra)
    return e


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Isolate the reaper: fake registry file, ps table, guards, kills."""
    state = {
        "table": {},
        "killed": [],
        "ledger": [],
        "table_calls": 0,
        "wt": (set(), set()),
        "jsonl": None,
    }

    def fake_table():
        state["table_calls"] += 1
        return state["table"]

    monkeypatch.setattr(server, "SPAWNED_PIDS_FILE", tmp_path / "spawned-pids.json")
    monkeypatch.setattr(server, "_spawn_reaper_process_table", fake_table)
    monkeypatch.setattr(server, "_wt_live_worker_guard", lambda: state["wt"])
    monkeypatch.setattr(server, "_find_session_jsonl", lambda sid: state["jsonl"])
    monkeypatch.setattr(
        server, "_resume_ledger_append",
        lambda event, **kw: state["ledger"].append({"event": event, **kw}),
    )
    monkeypatch.setattr(
        server.os, "killpg",
        lambda pid, sig: state["killed"].append((pid, sig)),
    )
    monkeypatch.setattr(server, "_spawned_sessions", [])
    monkeypatch.delenv("CCC_SPAWN_IDLE_TTL_HOURS", raising=False)
    return state


def _claude_row(ppid=1, pgid=None, stat="S", command=CLAUDE_CMD):
    return {"ppid": ppid, "pgid": pgid, "stat": stat, "command": command}


def test_retires_idle_claude_headless(tmp_path, harness):
    entry = _entry(101, tmp_path)
    _write_registry(tmp_path, [entry])
    harness["table"] = {101: _claude_row(pgid=101)}

    reaped = server._reap_idle_spawned_headless(now=NOW)

    assert [r["pid"] for r in reaped] == [101]
    assert harness["killed"] == [(101, server.signal.SIGTERM)]
    saved = json.loads((tmp_path / "spawned-pids.json").read_text())
    assert saved[0]["retired"] is True
    assert saved[0]["retire_reason"] == "idle-ttl"
    # Resumability: the retired entry keeps its session_id.
    assert saved[0]["session_id"] == "sid-1234"
    assert harness["ledger"] and harness["ledger"][0]["source"] == "spawn_idle_ttl"


def test_recent_log_activity_blocks_reap(tmp_path, harness):
    _write_registry(tmp_path, [_entry(102, tmp_path, idle_s=600)])
    harness["table"] = {102: _claude_row(pgid=102)}
    assert server._reap_idle_spawned_headless(now=NOW) == []
    assert harness["killed"] == []


def test_active_tool_child_blocks_reap(tmp_path, harness):
    _write_registry(tmp_path, [_entry(103, tmp_path)])
    harness["table"] = {
        103: _claude_row(pgid=103),
        # A direct child in its OWN process group = running tool = mid-turn.
        2222: {"ppid": 103, "pgid": 2222, "stat": "S", "command": "/bin/bash -c sleep"},
    }
    assert server._reap_idle_spawned_headless(now=NOW) == []
    assert harness["killed"] == []


def test_hook_child_does_not_block_reap(tmp_path, harness):
    _write_registry(tmp_path, [_entry(104, tmp_path)])
    hook_cmd = "python3 hooks/pre-tool-use.py"
    assert server._is_ccc_hook_command(hook_cmd)
    harness["table"] = {
        104: _claude_row(pgid=104),
        2223: {"ppid": 104, "pgid": 2223, "stat": "S", "command": hook_cmd},
    }
    assert [r["pid"] for r in server._reap_idle_spawned_headless(now=NOW)] == [104]


def test_wt_live_worker_guard_by_pid_and_sid(tmp_path, harness):
    _write_registry(tmp_path, [
        _entry(105, tmp_path, sid="sid-a"),
        _entry(106, tmp_path, sid="sid-b"),
    ])
    harness["table"] = {105: _claude_row(pgid=105), 106: _claude_row(pgid=106)}
    harness["wt"] = ({105}, {"sid-b"})
    assert server._reap_idle_spawned_headless(now=NOW) == []
    assert harness["killed"] == []


def test_skips_non_claude_engines_and_already_retired(tmp_path, harness):
    _write_registry(tmp_path, [
        _entry(107, tmp_path, engine="codex"),
        _entry(108, tmp_path, engine="remote-claude"),
        _entry(109, tmp_path, retired=True),
    ])
    harness["table"] = {
        107: _claude_row(pgid=107), 108: _claude_row(pgid=108),
        109: _claude_row(pgid=109),
    }
    assert server._reap_idle_spawned_headless(now=NOW) == []
    assert harness["killed"] == []


def test_pid_reuse_guard_argv0_must_be_claude(tmp_path, harness):
    _write_registry(tmp_path, [_entry(110, tmp_path)])
    harness["table"] = {110: _claude_row(pgid=110, command="/usr/bin/vim server.py")}
    assert server._reap_idle_spawned_headless(now=NOW) == []
    assert harness["killed"] == []


def test_dead_or_zombie_pid_skipped(tmp_path, harness):
    _write_registry(tmp_path, [
        _entry(111, tmp_path),                     # not in table at all
        _entry(112, tmp_path),                     # zombie
    ])
    harness["table"] = {112: _claude_row(pgid=112, stat="Z")}
    assert server._reap_idle_spawned_headless(now=NOW) == []
    assert harness["killed"] == []


def test_recent_transcript_blocks_reap(tmp_path, harness):
    _write_registry(tmp_path, [_entry(113, tmp_path)])
    harness["table"] = {113: _claude_row(pgid=113)}
    jsonl = tmp_path / "transcript.jsonl"
    jsonl.write_text("{}")
    os.utime(jsonl, (NOW - 60, NOW - 60))  # a takeover terminal just wrote
    harness["jsonl"] = jsonl
    assert server._reap_idle_spawned_headless(now=NOW) == []
    assert harness["killed"] == []


def test_ttl_env_knob(tmp_path, harness, monkeypatch):
    _write_registry(tmp_path, [_entry(114, tmp_path, idle_s=2 * 3600)])
    harness["table"] = {114: _claude_row(pgid=114)}
    # 2h idle < default 3h TTL -> survives.
    assert server._reap_idle_spawned_headless(now=NOW) == []
    # Tighten to 1h -> retired.
    monkeypatch.setenv("CCC_SPAWN_IDLE_TTL_HOURS", "1")
    assert [r["pid"] for r in server._reap_idle_spawned_headless(now=NOW)] == [114]


def test_ttl_zero_disables_sweep(tmp_path, harness, monkeypatch):
    _write_registry(tmp_path, [_entry(115, tmp_path)])
    harness["table"] = {115: _claude_row(pgid=115)}
    monkeypatch.setenv("CCC_SPAWN_IDLE_TTL_HOURS", "0")
    assert server._reap_idle_spawned_headless(now=NOW) == []
    assert harness["table_calls"] == 0  # fully short-circuited


def test_one_batched_probe_no_per_row_subprocess(tmp_path, harness, monkeypatch):
    """Perf gate: N registry rows -> exactly one process-table probe and
    zero ad-hoc subprocess forks anywhere in the sweep."""
    entries = [_entry(200 + i, tmp_path, sid=f"sid-{i}") for i in range(25)]
    _write_registry(tmp_path, entries)
    harness["table"] = {e["pid"]: _claude_row(pgid=e["pid"]) for e in entries}

    def no_fork(*a, **kw):
        raise AssertionError("per-row subprocess in spawn idle sweep")

    monkeypatch.setattr(server.subprocess, "run", no_fork)
    reaped = server._reap_idle_spawned_headless(now=NOW)
    assert len(reaped) == 25
    assert harness["table_calls"] == 1


def test_cleans_in_memory_entry_fds(tmp_path, harness, monkeypatch):
    entry = _entry(116, tmp_path)
    fifo = tmp_path / "spawn-116.log.stdin"
    fifo.write_text("")  # stand-in node; reaper only unlinks it
    entry["fifo"] = str(fifo)
    _write_registry(tmp_path, [entry])
    harness["table"] = {116: _claude_row(pgid=116)}
    r, w = os.pipe()
    os.close(r)
    mem = {"pid": 116, "stdin_fd": w, "fifo": str(fifo), "log_fh": None}
    monkeypatch.setattr(server, "_spawned_sessions", [mem])

    assert [x["pid"] for x in server._reap_idle_spawned_headless(now=NOW)] == [116]
    assert mem["stdin_fd"] is None          # fd closed via _cleanup_finished_entry
    assert not fifo.exists()                # FIFO node removed


def test_spawn_table_parsing_shapes():
    assert server._spawn_table_row_is_claude({"command": "claude -p"})
    assert server._spawn_table_row_is_claude(
        {"command": str(Path.home() / ".local/share/claude/versions/2.1.211") + " -p"}
    )
    assert not server._spawn_table_row_is_claude({"command": "python3 fake_claude.py"})
    assert not server._spawn_table_row_is_claude({"command": ""})
    assert server._spawn_entry_spawned_at_epoch("20260101T000000")
    assert server._spawn_entry_spawned_at_epoch("garbage") is None
