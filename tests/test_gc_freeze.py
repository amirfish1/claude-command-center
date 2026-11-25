"""Full GC passes stopped every dashboard request for ~1.2s (CCC-142)."""

import gc
import json
import weakref

import pytest

import server


class Node:
    pass


@pytest.fixture(autouse=True)
def unfrozen():
    yield
    gc.unfreeze()


GROWN = server._GC_FULL_PASS_GROWTH_BYTES


def _state(last_full=0.0, baseline=1000):
    return {"last_full": last_full, "baseline_rss": baseline, "due_since": None}


def _aged_cycle():
    """A cycle that outlived the young passes before it died, as long-lived
    request and thread objects do. Only a gen-2 pass can find it."""
    node = Node()
    node.me = node
    ref = weakref.ref(node)
    gc.collect(1)
    gc.collect(1)
    del node
    gc.collect(1)
    assert ref() is not None
    return ref


@pytest.fixture
def rss(monkeypatch):
    value = {"now": 1000}
    monkeypatch.setattr(server, "_process_rss_bytes", lambda: value["now"])
    return value


def test_tick_takes_the_heap_out_of_later_passes(rss):
    gc.unfreeze()
    state = _state()
    server._gc_freeze_tick(10.0, state)
    assert state["last_full"] == 0.0
    assert gc.get_freeze_count() > 0


def test_frozen_objects_are_still_freed_by_refcount(rss):
    node = Node()
    ref = weakref.ref(node)
    server._gc_freeze_tick(10.0, _state())
    del node
    assert ref() is None


def test_tick_reclaims_cycles_that_died_before_the_freeze(rss):
    """CCC-685: freezing them left them for the multi-second full pass."""
    ref = _aged_cycle()
    server._gc_freeze_tick(10.0, _state())
    assert ref() is None


def test_young_tick_does_not_walk_the_frozen_heap(rss):
    passes = []
    gc.callbacks.append(lambda phase, info: passes.append(server._gc_pass_tag["pass"])
                        if phase == "start" else None)
    try:
        server._gc_freeze_tick(10.0, _state())
    finally:
        gc.callbacks.pop()
    assert passes == ["young"]
    assert server._gc_pass_tag["pass"] is None


def _frozen_cycle():
    node = Node()
    node.me = node
    ref = weakref.ref(node)
    server._gc_freeze_tick(10.0, _state())
    del node
    gc.collect()
    assert ref() is not None, "a cycle that dies frozen waits for a full pass"
    return ref


def test_no_full_pass_on_a_timer_while_memory_is_flat(rss):
    ref = _frozen_cycle()
    state = _state()
    rss["now"] = 1000 + GROWN - 1
    server._gc_freeze_tick(server._GC_FULL_PASS_MAX_S - 1, state)
    assert ref() is not None
    assert state["last_full"] == 0.0


def test_growth_triggers_a_full_pass_after_the_minimum_interval(rss):
    ref = _frozen_cycle()
    state = _state()
    rss["now"] = 1000 + GROWN
    server._gc_freeze_tick(server._GC_FULL_PASS_MIN_S - 1, state)
    assert ref() is not None, "never more often than the minimum interval"
    now = server._GC_FULL_PASS_MIN_S
    server._gc_freeze_tick(now, state)
    assert ref() is None
    assert state == {"last_full": now, "baseline_rss": 1000 + GROWN, "due_since": None}
    assert gc.get_freeze_count() > 0


def test_backstop_runs_a_full_pass_without_growth(rss):
    ref = _frozen_cycle()
    state = _state()
    server._gc_freeze_tick(server._GC_FULL_PASS_MAX_S, state)
    assert ref() is None
    assert state["last_full"] == server._GC_FULL_PASS_MAX_S


def test_unmeasurable_memory_keeps_the_old_cadence(rss):
    ref = _frozen_cycle()
    rss["now"] = None
    state = _state(baseline=None)
    server._gc_freeze_tick(server._GC_FULL_PASS_MIN_S, state)
    assert ref() is None


def test_full_pass_is_tagged_with_its_reason(rss):
    seen = []
    gc.callbacks.append(lambda phase, info: seen.append(dict(server._gc_pass_tag))
                        if phase == "start" else None)
    try:
        server._gc_freeze_tick(server._GC_FULL_PASS_MAX_S, _state())
    finally:
        gc.callbacks.pop()
    assert seen == [{"pass": "full", "reason": "backstop"}]


def test_pending_restart_keeps_sweep_due_until_it_is_cancelled(monkeypatch, rss):
    due = True
    monkeypatch.setattr(server, "_gc_dashboard_restart_due", lambda: due)
    ref = _frozen_cycle()
    state = _state()
    deadline = server._GC_FULL_PASS_MAX_S

    server._gc_freeze_tick(deadline, state)
    assert ref() is not None
    assert state["due_since"] == deadline

    due = False
    server._gc_freeze_tick(deadline + 15, state)
    assert ref() is None
    assert state["last_full"] == deadline + 15


def test_restart_cannot_postpone_cycle_reclamation_past_grace(monkeypatch, rss):
    monkeypatch.setattr(server, "_gc_dashboard_restart_due", lambda: True)
    ref = _frozen_cycle()
    state = _state()
    deadline = server._GC_FULL_PASS_MAX_S
    end = deadline + server._GC_RESTART_GRACE_S

    server._gc_freeze_tick(deadline, state)
    server._gc_freeze_tick(end - 1, state)
    assert ref() is not None
    server._gc_freeze_tick(end, state)
    assert ref() is None
    assert state["last_full"] == end


def test_process_rss_is_readable_here():
    assert server._process_rss_bytes() > 0


def test_restart_hint_is_for_an_imminent_replacement_of_this_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(server.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(server, "_SERVER_START_TS", 1700.5)
    monkeypatch.setattr(server.time, "time", lambda: 3500.0)
    state = tmp_path / ".claude" / "command-center" / "restart-debounce.json"
    state.parent.mkdir(parents=True)

    assert server._gc_dashboard_restart_due() is False
    state.write_text("[]")
    assert server._gc_dashboard_restart_due() is False

    pending = {"target": "all", "dashboard_started_at": 1700.5, "due": 3510.0}
    for change in ({"target": "worker"}, {"dashboard_started_at": 1700.0},
                   {"due": 3560.0}, {"due": 3400.0}, {"due": "3510.0"}):
        state.write_text(json.dumps({"pending": dict(pending, **change)}))
        assert server._gc_dashboard_restart_due() is False

    state.write_text(json.dumps({"pending": pending}))
    assert server._gc_dashboard_restart_due() is True

    state.write_text(json.dumps({
        "last_target": "dashboard",
        "reservation": {"dashboard_started_at": 1700.5, "at": 3499.0},
    }))
    assert server._gc_dashboard_restart_due() is True


def test_start_raises_the_youngest_threshold(monkeypatch):
    before = gc.get_threshold()
    monkeypatch.setattr(server.threading, "Thread", lambda *a, **k: type(
        "T", (), {"start": lambda self: None})())
    try:
        server._start_gc_freeze_thread()
        assert gc.get_threshold() == (server._GC_GEN0_THRESHOLD, *before[1:])
    finally:
        gc.set_threshold(*before)


def test_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("CCC_GC_FREEZE", "0")
    started = []
    monkeypatch.setattr(server.threading, "Thread", lambda *a, **k: started.append(1))
    server._start_gc_freeze_thread()
    assert started == []
