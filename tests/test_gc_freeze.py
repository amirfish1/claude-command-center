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


BASE = {"rss": 3 * 1024 ** 3, "fds": 100}
LEAKED = {"rss": BASE["rss"] + 2 * 1024 ** 3, "fds": 100}
SETTLED = server._GC_LEAK_BASELINE_AFTER_S


def _state(last_full=0.0, baseline=None):
    return {"last_full": last_full, "due_since": None, "baseline": baseline}


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


@pytest.fixture(autouse=True)
def sample(monkeypatch):
    """The process RSS and open files the guard sees; BASE unless a test leaks."""
    value = dict(BASE)
    monkeypatch.setattr(server, "_gc_leak_sample", lambda: dict(value))
    return value


@pytest.fixture
def requests(monkeypatch):
    """No request is in flight unless a test says so."""
    value = {"inflight": False}
    monkeypatch.setattr(server, "_gc_requests_in_flight", lambda: value["inflight"])
    return value


def test_tick_takes_the_heap_out_of_later_passes(requests):
    gc.unfreeze()
    state = _state()
    server._gc_freeze_tick(10.0, state)
    assert state["last_full"] == 0.0
    assert gc.get_freeze_count() > 0


def test_frozen_objects_are_still_freed_by_refcount(requests):
    node = Node()
    ref = weakref.ref(node)
    server._gc_freeze_tick(10.0, _state())
    del node
    assert ref() is None


def test_tick_reclaims_cycles_that_died_before_the_freeze(requests):
    """CCC-685: freezing them left them for the multi-second full pass."""
    ref = _aged_cycle()
    server._gc_freeze_tick(10.0, _state())
    assert ref() is None


def test_young_tick_does_not_walk_the_frozen_heap(requests):
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


def _leaking_state(sample):
    """A settled process whose RSS then grew by the leak bound."""
    state = _state(baseline=dict(BASE))
    sample.update(LEAKED)
    return state


def test_no_full_pass_without_leak_evidence(requests):
    """CCC-687/695: an RSS-growth trigger ran a 2.0-2.5s pass ~30 min after
    every boot to reclaim ~250 objects, then a 12h backstop stalled 5.8s to
    reclaim 5330. A steady process never runs a full pass, however long it
    lives."""
    ref = _frozen_cycle()
    state = _state()
    for now in (1800.0, SETTLED, 12 * 3600.0, 24 * 3600.0, 7 * 86400.0):
        server._gc_freeze_tick(now, state)
    assert ref() is not None
    assert state["last_full"] == 0.0
    assert not hasattr(server, "_GC_FULL_PASS_MAX_S")


def test_baseline_waits_for_the_caches_to_fill(sample, requests):
    """Growth during cache fill is not a leak (CCC-687)."""
    state = _state()
    sample.update(LEAKED)
    server._gc_freeze_tick(SETTLED - 1, state)
    assert state["baseline"] is None
    server._gc_freeze_tick(SETTLED, state)
    assert state["baseline"] == LEAKED
    assert state["last_full"] == 0.0


@pytest.mark.parametrize("key, grown", [
    ("rss", {"rss": BASE["rss"] + 2 * 1024 ** 3}),
    ("fds", {"fds": BASE["fds"] + 512}),
])
def test_leak_runs_a_tagged_full_pass(sample, requests, key, grown):
    ref = _frozen_cycle()
    state = _state(baseline=dict(BASE))
    sample.update(grown)
    seen = []
    gc.callbacks.append(lambda phase, info: seen.append(dict(server._gc_pass_tag))
                        if phase == "start" else None)
    try:
        server._gc_freeze_tick(SETTLED + 60, state)
    finally:
        gc.callbacks.pop()
    assert ref() is None
    assert seen[0] == {"pass": "full", "reason": key}
    assert state == {"last_full": SETTLED + 60, "due_since": None,
                     "baseline": dict(BASE, **grown)}
    assert gc.get_freeze_count() > 0


def test_growth_under_the_bounds_runs_no_full_pass(sample, requests):
    ref = _frozen_cycle()
    state = _state(baseline=dict(BASE))
    sample.update(rss=LEAKED["rss"] - 1, fds=BASE["fds"] + 511)
    server._gc_freeze_tick(SETTLED + 60, state)
    assert ref() is not None
    assert state["last_full"] == 0.0


def test_unreadable_sample_runs_no_full_pass(sample, requests):
    state = _state(baseline=dict(BASE))
    sample.update(rss=None, fds=None)
    server._gc_freeze_tick(SETTLED + 60, state)
    assert state["last_full"] == 0.0


def test_memory_a_pass_cannot_free_does_not_repeat_it(sample, requests):
    """The pass rebases on what it left, and full passes are at least
    _GC_FULL_PASS_MIN_S apart."""
    state = _leaking_state(sample)
    server._gc_freeze_tick(SETTLED, state)
    assert state["last_full"] == SETTLED
    ref = _frozen_cycle()
    for now in (SETTLED + 15, SETTLED + server._GC_FULL_PASS_MIN_S, 7 * 86400.0):
        server._gc_freeze_tick(now, state)
    assert ref() is not None
    assert state["last_full"] == SETTLED
    sample["rss"] += 2 * 1024 ** 3
    server._gc_freeze_tick(SETTLED + server._GC_FULL_PASS_MIN_S - 1, state)
    assert state["last_full"] == SETTLED
    server._gc_freeze_tick(SETTLED + server._GC_FULL_PASS_MIN_S, state)
    assert ref() is None


def test_leak_pass_waits_for_a_tick_with_no_request_in_flight(sample, requests):
    ref = _frozen_cycle()
    state = _leaking_state(sample)
    due = SETTLED
    requests["inflight"] = True
    server._gc_freeze_tick(due, state)
    server._gc_freeze_tick(due + 15, state)
    assert ref() is not None
    assert state["due_since"] == due
    requests["inflight"] = False
    server._gc_freeze_tick(due + 30, state)
    assert ref() is None
    assert state["last_full"] == due + 30


def test_busy_server_cannot_postpone_a_leak_pass_past_the_quiet_wait(sample, requests):
    ref = _frozen_cycle()
    state = _leaking_state(sample)
    due = SETTLED
    end = due + server._GC_FULL_PASS_QUIET_WAIT_S
    requests["inflight"] = True
    server._gc_freeze_tick(due, state)
    server._gc_freeze_tick(end - 1, state)
    assert ref() is not None
    server._gc_freeze_tick(end, state)
    assert ref() is None


def test_in_flight_check_reads_the_request_registry(monkeypatch):
    monkeypatch.setattr(server, "_INFLIGHT_REQUESTS", {})
    assert server._gc_requests_in_flight() is False
    monkeypatch.setattr(server, "_INFLIGHT_REQUESTS", {1: "/api/sessions"})
    assert server._gc_requests_in_flight() is True


def test_pending_restart_keeps_sweep_due_until_it_is_cancelled(monkeypatch, sample, requests):
    due = True
    monkeypatch.setattr(server, "_gc_dashboard_restart_due", lambda: due)
    ref = _frozen_cycle()
    state = _leaking_state(sample)
    deadline = SETTLED

    server._gc_freeze_tick(deadline, state)
    assert ref() is not None
    assert state["due_since"] == deadline

    due = False
    server._gc_freeze_tick(deadline + 15, state)
    assert ref() is None
    assert state["last_full"] == deadline + 15


def test_restart_cannot_postpone_cycle_reclamation_past_grace(monkeypatch, sample, requests):
    monkeypatch.setattr(server, "_gc_dashboard_restart_due", lambda: True)
    ref = _frozen_cycle()
    state = _leaking_state(sample)
    deadline = SETTLED
    end = deadline + server._GC_RESTART_GRACE_S

    server._gc_freeze_tick(deadline, state)
    server._gc_freeze_tick(end - 1, state)
    assert ref() is not None
    server._gc_freeze_tick(end, state)
    assert ref() is None
    assert state["last_full"] == end


def test_real_sample_reads_this_process():
    got = server._process_rss_bytes(), server._process_open_fds()
    assert got[0] and got[0] > 1024 ** 2
    assert got[1] and got[1] > 0


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
