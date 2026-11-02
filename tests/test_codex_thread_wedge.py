"""A Codex thread the app-server daemon stopped answering is detected and healed.

Incident: the managed daemon answered every thread except one; CCC retried
that thread every ~90s for hours, piling hung requests into the daemon while
the session showed no warning. The detector counts per-thread timeouts, holds
delivery, and restarts the daemon only when no other Codex work is live.
"""
import json
import os
import time

import pytest

from ccc_server import codex_thread_wedge as w

SID = "01a19a3e-1ebe-75d3-952d-aa500fef6dfc"
TIMEOUT = {"error": "Codex app-server request timed out: thread/resume"}
OK = {"result": {"thread": {"id": SID}}}


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path))
    w.reset_for_tests()
    yield tmp_path
    w.reset_for_tests()


def _timeouts(n, start=1000.0, step=40.0, sid=SID):
    for i in range(n):
        w.note_result("thread/resume", {"threadId": sid}, TIMEOUT, now=start + i * step)


def _recover(now=2000.0, turn_active=lambda: False, sessions_dir=None, calls=None):
    calls = [] if calls is None else calls
    return w.maybe_recover(
        SID, turn_active=turn_active, codex_bin="codex", now=now,
        sessions_dir=sessions_dir or "/nonexistent",
        restart=lambda b: calls.append(b) or {"ok": True},
    ), calls


def test_healthy_thread_is_not_wedged():
    _timeouts(2)
    assert w.wedged(SID) is None
    assert _recover()[0] == {"wedged": False}


def test_quick_burst_is_not_wedged():
    _timeouts(5, step=1.0)  # 4s span: a blip, not a hang
    assert w.wedged(SID) is None


def test_success_resets_the_count():
    _timeouts(3)
    w.note_result("thread/read", {"threadId": SID}, OK, now=1100.0)
    assert w.wedged(SID) is None


def test_other_threads_and_unthreaded_calls_are_ignored():
    _timeouts(3, sid="other-thread")
    w.note_result("thread/loaded/list", {}, TIMEOUT)
    assert w.wedged(SID) is None
    assert w.wedged("other-thread") is not None


def test_wedged_idle_daemon_is_restarted_once():
    _timeouts(3)
    decision, calls = _recover()
    assert decision["action"] == "restarted"
    assert calls == ["codex"]
    assert w.wedged(SID) is None  # counters reset after restart
    _timeouts(3, start=2100.0)
    again, calls = _recover(now=2300.0, calls=calls)
    assert again["action"] == "wait"
    assert "recently" in again["reason"]
    assert calls == ["codex"]  # cooldown: no second restart


def test_restart_waits_for_a_live_turn():
    _timeouts(3)
    decision, calls = _recover(turn_active=lambda: True)
    assert decision["action"] == "wait"
    assert "turn is running" in decision["reason"]
    assert calls == []


def test_restart_waits_for_other_codex_writers(tmp_path):
    _timeouts(3)
    now = time.time()
    day = tmp_path / time.strftime("%Y/%m/%d", time.localtime(now))
    day.mkdir(parents=True)
    (day / "rollout-x-other-thread.jsonl").write_text("{}\n")
    (day / f"rollout-x-{SID}.jsonl").write_text("{}\n")  # its own file never blocks
    decision, calls = _recover(now=now, sessions_dir=tmp_path)
    assert decision["action"] == "wait"
    assert "another Codex session is writing" in decision["reason"]
    assert calls == []
    old = now - 600
    os.utime(day / "rollout-x-other-thread.jsonl", (old, old))
    decision, calls = _recover(now=now, sessions_dir=tmp_path)
    assert decision["action"] == "restarted"


def test_state_is_published_for_the_dashboard(state):
    _timeouts(3)
    _recover(turn_active=lambda: True)
    data = json.loads((state / "codex-thread-wedge.json").read_text())
    assert data["threads"][SID]["timeouts"] == 3
    published = w.read_published(SID)
    assert published and "turn is running" in published["waiting"]
    w.note_result("thread/resume", {"threadId": SID}, OK)
    assert w.read_published(SID) is None


def test_delivery_gate_holds_without_touching_the_queue(monkeypatch):
    import importlib
    importlib.import_module("server")
    from ccc_server import core, queue_events

    _timeouts(3)
    monkeypatch.setattr(core, "_codex_app_server_active_turn_fresh", lambda now: True)
    claimed = []
    monkeypatch.setattr(core, "_apply_pending_input_operations",
                        lambda *a, **k: claimed.append(a) or {"ok": True})
    result = queue_events._codex_queued_delivery_transaction(SID)
    assert result["code"] == "codex_thread_wedged"
    assert result["ok"] is False
    assert claimed == []  # message stays at the head of the durable queue


def test_delivery_gate_passes_healthy_threads():
    from ccc_server import queue_events
    assert queue_events._codex_wedge_gate(SID) is None
