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


def _cycle():
    node = Node()
    node.me = node
    ref = weakref.ref(node)
    del node
    return ref


def test_tick_takes_the_heap_out_of_later_passes():
    gc.unfreeze()
    assert server._gc_freeze_tick(10.0, 100.0) == 100.0
    assert gc.get_freeze_count() > 0


def test_frozen_objects_are_still_freed_by_refcount():
    node = Node()
    ref = weakref.ref(node)
    server._gc_freeze_tick(10.0, 100.0)
    del node
    assert ref() is None


def test_full_pass_reclaims_frozen_cycles():
    ref = _cycle()
    server._gc_freeze_tick(10.0, 100.0)
    gc.collect()
    assert ref() is not None, "a frozen cycle waits for the full pass"
    next_pass = server._gc_freeze_tick(100.0, 100.0)
    assert ref() is None
    assert next_pass == 100.0 + server._GC_FULL_PASS_EVERY_S
    assert gc.get_freeze_count() > 0


def test_pending_restart_keeps_sweep_due_until_it_is_cancelled(monkeypatch):
    due = True
    monkeypatch.setattr(server, "_gc_dashboard_restart_due", lambda: due)
    ref = _cycle()
    server._gc_freeze_tick(10.0, 100.0)

    assert server._gc_freeze_tick(100.0, 100.0) == 100.0
    assert ref() is not None

    due = False
    assert server._gc_freeze_tick(115.0, 100.0) == 115.0 + server._GC_FULL_PASS_EVERY_S
    assert ref() is None


def test_restart_cannot_postpone_cycle_reclamation_past_grace(monkeypatch):
    monkeypatch.setattr(server, "_gc_dashboard_restart_due", lambda: True)
    ref = _cycle()
    server._gc_freeze_tick(10.0, 100.0)
    end = 100.0 + server._GC_RESTART_GRACE_S

    assert server._gc_freeze_tick(end - 1, 100.0) == 100.0
    assert ref() is not None
    assert server._gc_freeze_tick(end, 100.0) == end + server._GC_FULL_PASS_EVERY_S
    assert ref() is None


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
