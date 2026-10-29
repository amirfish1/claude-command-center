"""Spawn dedupe contract: task_key / prompt_hash / task_summary on the spawn
registry, finished-spawn history, and the /api/sessions/spawn dedupe lookup.

Covers the five-part change: meta persisted through _record_spawn_to_registry,
inherited across resume, stamped by the post-dispatch tag helper, moved to
spawned-history.json on removal (not deleted), and matched by
_spawn_dedupe_lookup with repo scoping + engine-process liveness.
"""

import json

import pytest

import server
from ccc_server import usage_limit as ul


@pytest.fixture(autouse=True)
def isolated_spawn_state(monkeypatch, tmp_path):
    """Point the live registry and the history file at tmp_path — the module
    paths are resolved at import from the REAL state dir, and
    _record_spawn_to_registry writes unconditionally."""
    pids = tmp_path / "spawned-pids.json"
    pids.write_text("[]")
    monkeypatch.setattr(server, "SPAWNED_PIDS_FILE", pids)
    monkeypatch.setattr(
        ul, "_SPAWNED_HISTORY_FILE", tmp_path / "spawned-history.json"
    )
    yield
    server._clear_spawn_request_meta()


def _write_registry(entries):
    server.SPAWNED_PIDS_FILE.write_text(json.dumps(entries))


def _read_registry():
    return json.loads(server.SPAWNED_PIDS_FILE.read_text())


def _record(pid=None, session_id=None, **kw):
    args = dict(
        pid=pid,
        name=kw.pop("name", "lane"),
        log_path=kw.pop("log_path", "/tmp/x.log"),
        cwd=kw.pop("cwd", "/tmp/repo"),
        spawned_at=kw.pop("spawned_at", "20261003T120000"),
        command_summary=kw.pop("command_summary", "do the thing"),
        engine=kw.pop("engine", "claude"),
        session_id=session_id,
        repo_path=kw.pop("repo_path", "/tmp/repo"),
    )
    args.update(kw)
    server._record_spawn_to_registry(**args)


def test_record_spawn_persists_request_meta():
    server._set_spawn_request_meta(
        task_key="WT-42", prompt_hash="abc123", task_summary="fix the flaky test"
    )
    _record(pid=98765, session_id="sid-1")

    entries = _read_registry()
    assert len(entries) == 1
    assert entries[0]["task_key"] == "WT-42"
    assert entries[0]["prompt_hash"] == "abc123"
    assert entries[0]["task_summary"] == "fix the flaky test"


def test_record_spawn_without_meta_leaves_fields_absent():
    _record(pid=98766)
    entry = _read_registry()[0]
    for field in ("task_key", "prompt_hash", "task_summary"):
        assert field not in entry


def test_meta_inherited_across_resume_same_session():
    """A resumed lane gets a new pid + record; the dedupe identity must
    follow the session or a re-dispatch misses and twins the task."""
    server._set_spawn_request_meta(
        task_key="WT-7", prompt_hash="h1", task_summary="write tests"
    )
    _record(pid=111, session_id="sid-resume")
    server._clear_spawn_request_meta()
    _record(pid=222, session_id="sid-resume")  # resume: new pid, same sid

    entries = _read_registry()
    resumed = [e for e in entries if e.get("pid") == 222]
    assert len(resumed) == 1
    assert resumed[0]["task_key"] == "WT-7"
    assert resumed[0]["prompt_hash"] == "h1"
    assert resumed[0]["task_summary"] == "write tests"


def test_tag_helper_stamps_meta_by_pid():
    _record(pid=333, session_id="sid-3")
    server._tag_spawn_task_meta_in_registry(
        pid=333,
        meta={"task_key": "k-9", "prompt_hash": "ph", "task_summary": "s"},
    )
    entry = _read_registry()[0]
    assert entry["task_key"] == "k-9"
    assert entry["prompt_hash"] == "ph"
    assert entry["task_summary"] == "s"


def test_remove_spawn_archives_to_history_not_delete():
    _write_registry([{
        "pid": 444, "session_id": "sid-4", "name": "lane",
        "command_summary": "done work", "engine": "claude",
        "cwd": "/tmp/repo", "task_key": "WT-9",
    }])

    server._remove_spawn_from_registry(444, exit_code=0)

    assert _read_registry() == []
    history = ul._load_spawn_history()
    assert len(history) == 1
    assert history[0]["pid"] == 444
    assert history[0]["task_key"] == "WT-9"
    assert history[0]["exit_code"] == 0
    assert history[0]["ended_at_epoch"]


def test_dedupe_lookup_matches_live_task_key():
    _write_registry([{
        "pid": None, "session_id": "sid-5", "name": "lane",
        "engine": "kimi", "cwd": "/tmp/repo",
        "task_key": "verify-d08302b3",
    }])
    live, finished = server._spawn_dedupe_lookup(
        task_key="verify-d08302b3", scope="/tmp/repo"
    )
    assert live and live["task_key"] == "verify-d08302b3"
    assert finished is None


def test_dedupe_lookup_scope_mismatch_does_not_match():
    _write_registry([{
        "pid": None, "session_id": "sid-6", "name": "lane",
        "engine": "kimi", "cwd": "/tmp/other-repo",
        "task_key": "shared-key",
    }])
    live, finished = server._spawn_dedupe_lookup(
        task_key="shared-key", scope="/tmp/repo"
    )
    assert live is None
    assert finished is None


def test_dedupe_lookup_finds_finished_task_in_history():
    (ul._SPAWNED_HISTORY_FILE).write_text(json.dumps([{
        "pid": 555, "session_id": "sid-7", "name": "lane",
        "engine": "claude", "cwd": "/tmp/repo",
        "task_key": "old-task", "exit_code": 0,
        "ended_at_epoch": 1700000000,
    }]))

    live, finished = server._spawn_dedupe_lookup(
        task_key="old-task", scope="/tmp/repo"
    )
    assert live is None
    assert finished and finished["task_key"] == "old-task"


def test_dedupe_lookup_prompt_hash_only_when_opted_in():
    """prompt_hash dedupe fires only for dedupe:true callers — a task_key
    spawn must never collide on prompt text alone."""
    _write_registry([{
        "pid": None, "session_id": "sid-8", "name": "lane",
        "engine": "kimi", "cwd": "/tmp/repo",
        "task_key": "other", "prompt_hash": "deadbeef",
    }])
    live, finished = server._spawn_dedupe_lookup(
        task_key="different-key", scope="/tmp/repo"
    )
    assert live is None
    live, finished = server._spawn_dedupe_lookup(
        prompt_hash="deadbeef", scope="/tmp/repo"
    )
    assert live is not None


def test_dedupe_existing_response_is_honest_about_report_back():
    resp = server._dedupe_existing_response(
        {"pid": 777, "session_id": "sid-9", "task_key": "k"},
        finished=False,
    )
    assert resp["ok"] is True
    assert resp["existing"] is True
    assert resp["finished"] is False
    assert resp["report_back"] == "original_dispatcher"
    assert resp["session_id"] == "sid-9"


def test_dedupe_lookup_no_match_spawns_no_subprocess(monkeypatch):
    """Perf gate: rows rejected by the key/scope string compare must never
    reach the pid liveness probe — cost stays O(registry size) with zero
    subprocesses, per the no-subprocess-per-row rule."""
    import subprocess
    _write_registry([
        {"pid": 12345, "session_id": f"sid-{i}", "engine": "claude",
         "cwd": "/tmp/repo", "task_key": f"key-{i}"}
        for i in range(50)
    ])
    calls = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    live, finished = server._spawn_dedupe_lookup(
        task_key="no-such-key", scope="/tmp/repo"
    )
    assert live is None
    assert calls == [], f"dedupe scan spawned subprocesses: {calls}"


def test_list_spawned_sessions_rows_carry_task_fields(monkeypatch):
    monkeypatch.setattr(server, "_control_plane_routes_engines", lambda: False)
    monkeypatch.setattr(server, "_poll_spawn_entry", lambda entry: None)
    monkeypatch.setattr(
        server, "_spawn_session_id_from_entry", lambda entry: "sid-10"
    )
    monkeypatch.setattr(server, "_load_spawn_registry", lambda: [])
    server._spawned_sessions[:] = [{
        "pid": 999, "spawn_id": "999", "session_id": "sid-10",
        "name": "lane", "log": "", "prompt": "do it",
        "started": "20261003T120000", "engine": "claude",
        "cwd": "/tmp/repo", "repo_path": "/tmp/repo", "model": "",
        "task_key": "WT-1", "task_summary": "do it", "prompt_hash": "h",
    }]
    try:
        rows = server.list_spawned_sessions()
    finally:
        server._spawned_sessions.clear()
    assert rows[0]["task_key"] == "WT-1"
    assert rows[0]["task_summary"] == "do it"
    assert rows[0]["prompt_hash"] == "h"
