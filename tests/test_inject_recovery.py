"""CCC-27: bounded auto-recovery for live sessions that hold queued input.

The failure being guarded: a live `claude -p` child sits idle while a user's
message is parked in the terminal queue. Recovery kills + resumes the child,
which can loop forever if unbounded -- these tests pin the loop guards.
"""
import importlib

import pytest

from ccc_server import inject_recovery as ir

NOW = 1_000_000.0
STUCK = dict(held_s=ir.STUCK_AFTER_S + 1, log_silent_s=ir.LOG_SILENT_S + 1,
             spawn_age_s=ir.MIN_SPAWN_AGE_S + 1, tool_child=False)


def d(entry=None, msg="m1", now=NOW, **over):
    kw = dict(STUCK, **over)
    return ir.decide(entry, msg_hash=ir.text_hash(msg), now=now, **kw)


def test_stuck_child_is_recovered():
    assert d() == (ir.RECOVER, "stuck")


@pytest.mark.parametrize("over,reason", [
    (dict(held_s=1), "young_hold"),
    (dict(log_silent_s=5), "log_active"),
    (dict(log_silent_s=None), "log_active"),
    (dict(spawn_age_s=5), "young_spawn"),
    (dict(tool_child=True), "tool_child"),
])
def test_healthy_or_young_child_is_never_touched(over, reason):
    assert d(**over) == (ir.WAIT, reason)


def test_kill_switch(monkeypatch):
    monkeypatch.setenv(ir.KILL_SWITCH_ENV, "0")
    assert d() == (ir.SKIP, "disabled")


def test_one_recovery_per_message():
    entry = {"hashes": [ir.text_hash("m1")], "attempts": []}
    assert d(entry) == (ir.GIVE_UP, "already_recovered")
    assert d(entry, msg="a different message")[0] == ir.RECOVER


def test_budget_then_give_up_and_backoff():
    one = {"attempts": [NOW - 10]}
    assert d(one) == (ir.WAIT, "backoff")
    later = NOW + ir.BACKOFF_BASE_S + 1
    assert d(one, now=later)[0] == ir.RECOVER
    full = {"attempts": [NOW - 500, NOW - 100][:ir.BUDGET_MAX]}
    assert d(full) == (ir.GIVE_UP, "budget")


def test_old_attempts_age_out_of_budget():
    old = {"attempts": [NOW - ir.BUDGET_WINDOW_S - 1] * 5}
    assert d(old)[0] == ir.RECOVER


def test_stuck_session_is_skipped_until_a_human_clears_it(tmp_path):
    p = str(tmp_path / "s.json")
    ir.record_attempt("s1", ir.text_hash("m1"), now=NOW, path=p)
    ir.mark_stuck("s1", "budget", now=NOW, path=p)
    assert d(ir.get("s1", p)) == (ir.SKIP, "inject_stuck")
    ir.clear_stuck("s1", path=p)
    assert ir.get("s1", p) == {}


def test_attempt_is_persisted_and_isolated_per_session(tmp_path):
    p = str(tmp_path / "s.json")
    ir.record_attempt("a", "h1", now=NOW, path=p)
    assert ir.get("a", p)["attempts"] == [NOW]
    assert ir.get("b", p) == {}


def test_wiring_is_exported():
    server = importlib.import_module("server")
    assert callable(server._force_restart_session)
    assert callable(server._maybe_recover_stuck_hold)


def test_recovery_finds_worker_spawned_child_via_disk_registry(monkeypatch):
    """A child the worker spawned after the dashboard booted is absent from the
    dashboard's in-memory spawn list; force-restart/recovery must still see it."""
    from ccc_server import pending_inputs, spawn_registry
    entry = {"pid": 4242, "engine": "claude", "session_id": "sid-1"}
    monkeypatch.setattr(pending_inputs._core, "_find_live_spawn_entry_for_session", lambda sid: None)
    monkeypatch.setattr(spawn_registry, "_disk_spawn_entry_for_session", lambda sid: entry)
    assert pending_inputs._live_claude_spawn_for_recovery("sid-1") is entry
    entry_codex = dict(entry, engine="codex")
    monkeypatch.setattr(spawn_registry, "_disk_spawn_entry_for_session", lambda sid: entry_codex)
    assert pending_inputs._live_claude_spawn_for_recovery("sid-1") is None
    monkeypatch.setattr(spawn_registry, "_disk_spawn_entry_for_session", lambda sid: None)
    assert pending_inputs._live_claude_spawn_for_recovery("sid-1") is None
