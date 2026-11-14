"""Speculative synthesis yields under contention without delaying a listener."""
import ast
import os
from pathlib import Path
import threading
import time
from unittest import mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "local_tts_server.py"


def lock_class():
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_PriorityLock")
    ns = {"threading": threading, "time": time}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns["_PriorityLock"]


def charged_lock(cpu=2.0):
    lock = lock_class()()
    with mock.patch.object(lock, "_background_budget", return_value=0.5), \
            mock.patch.object(time, "monotonic", side_effect=[100.0, 100.0, 101.0]), \
            mock.patch.object(time, "process_time", side_effect=[0.0, cpu]):
        lock.acquire(False)
        lock.release()
    return lock


def test_background_cpu_cost_creates_a_pause():
    # Two CPU seconds at half a core need four wall seconds; one elapsed.
    lock = charged_lock()
    assert lock._bg_ready_at == 104.0
    with mock.patch.object(lock, "_background_budget", return_value=0.5), \
            mock.patch.object(time, "monotonic", return_value=101.0), \
            mock.patch.object(lock._cond, "wait", side_effect=RuntimeError("paused")) as wait:
        with pytest.raises(RuntimeError, match="paused"):
            lock.acquire(False)
        wait.assert_called_once_with(1.0)


def test_foreground_bypasses_outstanding_background_pause():
    lock = charged_lock()
    with mock.patch.object(time, "monotonic", return_value=101.0), \
            mock.patch.object(lock._cond, "wait", side_effect=AssertionError("foreground waited")):
        lock.acquire(True)
        lock.release()
    assert lock._bg_ready_at == 104.0


def test_idle_host_does_not_wait_on_old_background_debt():
    lock = charged_lock()
    with mock.patch.object(lock, "_background_budget", return_value=0.0), \
            mock.patch.object(time, "monotonic", return_value=101.0), \
            mock.patch.object(lock._cond, "wait", side_effect=AssertionError("idle host waited")):
        lock.acquire(False)
        lock.release()


def test_pause_is_bounded_to_protect_streaming_timeouts():
    lock = charged_lock(cpu=100.0)
    assert lock._bg_ready_at == 101.0 + lock.BG_MAX_PAUSE_S


def test_budget_only_applies_on_busy_hosts(monkeypatch):
    cls = lock_class()
    monkeypatch.delenv("CCC_TTS_BACKGROUND_CPU", raising=False)
    with mock.patch.object(os, "cpu_count", return_value=4), \
            mock.patch.object(os, "getloadavg", return_value=(4.0, 3.0, 2.0)):
        assert cls._background_budget() == 0.5
        monkeypatch.setenv("CCC_TTS_BACKGROUND_CPU", "0")
        assert cls._background_budget() == 0.0
        monkeypatch.setenv("CCC_TTS_BACKGROUND_CPU", "nan")
        assert cls._background_budget() == 0.5
    with mock.patch.object(os, "getloadavg", return_value=(0.5, 0.5, 0.5)), \
            mock.patch.object(os, "cpu_count", return_value=4):
        assert cls._background_budget() == 0.0
    with mock.patch.object(os, "getloadavg", side_effect=OSError):
        assert cls._background_budget() == 0.0


def test_priority_lock_tracks_waiting_background_requests():
    lock = lock_class()()
    lock._busy = True
    observed = []

    def stop_waiting(_timeout=None):
        observed.append((lock._fg_waiting, lock._bg_waiting))
        raise RuntimeError("stop")

    with mock.patch.object(lock, "_background_budget", return_value=0.0), \
            mock.patch.object(lock._cond, "wait", side_effect=stop_waiting):
        with pytest.raises(RuntimeError, match="stop"):
            lock.acquire(False)
    assert observed == [(0, 1)]
    assert lock._bg_waiting == 0
