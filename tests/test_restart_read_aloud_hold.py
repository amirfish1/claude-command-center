"""CCC-144: a routine restart waits while a read-aloud is playing, boundedly."""
import importlib.machinery
import importlib.util
import pathlib
import time

import pytest

from ccc_server import free_runtime as fr

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean():
    fr._TTS_SESSIONS.clear()
    fr._LAST_FG_OPEN = 0.0
    fr._HOLD_SINCE = None
    yield
    fr._TTS_SESSIONS.clear()
    fr._LAST_FG_OPEN = 0.0
    fr._HOLD_SINCE = None


def _session(now, seconds=30.0, done=True, touched_ago=0.0, cancelled=False):
    s = fr._TtsSession(["x"], "")
    s.created = now
    s.touched = now - touched_ago
    s.size = int(seconds * fr._TTS_SESSION_BYTES_PER_S)
    s.done = done
    s.cancelled = cancelled
    fr._TTS_SESSIONS["sid"] = s
    return s


def test_idle_does_not_hold():
    assert fr.restart_hold(1000.0) is None


def test_active_read_holds_then_completion_releases():
    now = 1000.0
    _session(now, seconds=30)
    hold = fr.restart_hold(now + 5)
    assert hold and hold["reason"] == "read-aloud playing"
    # past the expected end plus grace the read no longer counts
    assert fr.restart_hold(now + 30 + fr._READ_END_GRACE_S + 1) is None


def test_cancelled_read_does_not_hold():
    _session(1000.0, cancelled=True)
    assert fr.restart_hold(1001.0) is None


def test_abandoned_generation_expires():
    now = 1000.0
    _session(now, seconds=5, done=False, touched_ago=fr._FG_RECENT_S + 5)
    # not generating recently, so only the (short) expected end counts
    assert fr.restart_hold(now + 60) is None


def test_recent_foreground_clip_holds_then_expires():
    fr._LAST_FG_OPEN = 1000.0
    assert fr.restart_hold(1010.0)
    assert fr.restart_hold(1000.0 + fr._FG_RECENT_S + 1) is None


def test_hold_is_finite():
    now = 1000.0
    _session(now, seconds=fr._READ_MAX_S)   # trusted to play a long time
    assert fr.restart_hold(now + 1)
    assert fr.restart_hold(now + fr.RESTART_HOLD_MAX_S - 1)
    assert fr.restart_hold(now + fr.RESTART_HOLD_MAX_S + 1) is None
    # under what `ccc restart` retries (10 x 60 s), so the restart still lands
    assert fr.RESTART_HOLD_MAX_S < 10 * 60


def test_ccc_sends_routine_flag_only_when_not_forced(monkeypatch):
    loader = importlib.machinery.SourceFileLoader("ccc_cli_mod", str(ROOT / "ccc"))
    spec = importlib.util.spec_from_loader("ccc_cli_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    sent = []
    monkeypatch.setattr(mod, "_post_json", lambda base, path, payload, timeout=60: sent.append(payload) or (200, {"ok": True}))
    mod._post_restart("http://x", "dashboard", routine=True)
    mod._post_restart("http://x", "dashboard")
    assert sent == [{"routine": True}, {}]


def test_handler_holds_only_routine_restarts_before_the_drain_precheck():
    src = (ROOT / "server.py").read_text()
    block = src[src.index('if path in ("/api/restart", "/api/restart/all"):'):]
    hold = block.index("_free_runtime.restart_hold()")
    precheck = block.index("_safe_worker_restart_precheck(")
    assert hold < precheck            # never set the worker drain, then refuse
    gate = block[:hold]
    assert '_rbody.get("routine")' in gate
