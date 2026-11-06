"""Full GC passes stopped every dashboard request for ~1.2s (CCC-142)."""

import gc
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


def test_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("CCC_GC_FREEZE", "0")
    started = []
    monkeypatch.setattr(server.threading, "Thread", lambda *a, **k: started.append(1))
    server._start_gc_freeze_thread()
    assert started == []
