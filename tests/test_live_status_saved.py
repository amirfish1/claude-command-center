"""The sidebar's live-status read shows the last saved status at once (CCC-319)."""

import json
import threading
import time

import pytest

import server


@pytest.fixture
def live(monkeypatch, tmp_path):
    """A saved status old enough to need a refresh, and a controllable build."""
    state = {"calls": 0, "release": threading.Event(), "result": {"new": {"is_live": True}}}

    def build(detail=None):
        state["calls"] += 1
        assert state["release"].wait(5)
        if isinstance(state["result"], Exception):
            raise state["result"]
        return state["result"]

    monkeypatch.setattr(server, "_live_activity_snapshot", {
        "ts": time.time() - 60, "data": {"old": {"is_live": True}}})
    monkeypatch.setattr(server, "_live_activity_refresh", {"done": None})
    monkeypatch.setattr(server, "_live_activity_saved", {"enabled": False, "at": 0.0})
    monkeypatch.setattr(server, "_LIVE_ACTIVITY_VIEW_WAIT_S", 0.05)
    monkeypatch.setattr(server, "_build_live_sessions_activity_uncached", build)
    monkeypatch.setattr(server, "_record_live_build_ms", lambda ms: None)
    monkeypatch.setattr(server, "record_event", lambda *a, **k: None)
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    yield state
    state["release"].set()
    done = server._live_activity_refresh["done"]
    if done is not None:
        assert done.wait(5)


def test_stalled_build_does_not_hold_up_the_read(live):
    started = time.perf_counter()
    data, status = server.live_sessions_activity_for_view()
    assert time.perf_counter() - started < 1.0
    assert set(data) == {"old"}
    assert status["source"] == "stale"
    assert status["refreshing"] is True
    assert status["age_s"] >= 59

    live["release"].set()
    assert server._live_activity_refresh["done"].wait(5)
    data, status = server.live_sessions_activity_for_view()
    assert set(data) == {"new"}
    assert status == {"source": "fresh", "age_s": status["age_s"], "refreshing": False}
    assert live["calls"] == 1


def test_normal_build_finishes_inside_the_read(live, monkeypatch):
    """A usual ~30ms build must not cost the poll a cycle of staleness."""
    monkeypatch.setattr(server, "_LIVE_ACTIVITY_VIEW_WAIT_S", 5)
    live["release"].set()
    data, status = server.live_sessions_activity_for_view()
    assert set(data) == {"new"}
    assert (status["source"], status["refreshing"]) == ("fresh", False)


def test_failed_refresh_keeps_the_last_status_and_retries(live):
    live["result"] = RuntimeError("boom")
    live["release"].set()
    server.live_sessions_activity_for_view()
    assert server._live_activity_refresh["done"].wait(5)
    data, status = server.live_sessions_activity_for_view()
    assert set(data) == {"old"}
    assert status["source"] == "stale"
    assert server._live_activity_refresh["done"].wait(5)
    assert live["calls"] == 2


def test_blocking_callers_still_get_a_fresh_build(live):
    live["release"].set()
    assert set(server.build_live_sessions_activity()) == {"new"}


def test_first_read_with_nothing_saved_waits_for_the_build(live):
    server._live_activity_snapshot.update(ts=0.0, data=None)
    live["release"].set()
    data, status = server.live_sessions_activity_for_view()
    assert set(data) == {"new"}
    assert (status["source"], status["refreshing"]) == ("fresh", False)


def _reset_to_boot():
    server._live_activity_snapshot.clear()
    server._live_activity_snapshot.update(ts=0.0, data=None)


def test_saved_copy_survives_a_restart(live):
    server._load_saved_live_activity()  # enables saving, as the dashboard does
    live["release"].set()
    server.build_live_sessions_activity()
    _reset_to_boot()
    live["release"].clear()

    assert server._load_saved_live_activity() is True
    data, status = server.live_sessions_activity_for_view()
    assert set(data) == {"new"}
    assert status["source"] == "saved"
    assert status["refreshing"] is True


def test_saves_are_throttled(live):
    server._load_saved_live_activity()
    live["release"].set()
    server.build_live_sessions_activity()
    path = server._live_activity_saved_path()
    first = path.read_text()
    live["result"] = {"newer": {}}
    server._live_activity_snapshot["ts"] = 0.0
    server.build_live_sessions_activity()
    assert path.read_text() == first


def test_other_processes_do_not_write_the_saved_copy(live):
    """The worker imports server but never runs the dashboard boot."""
    live["release"].set()
    server.build_live_sessions_activity()
    assert not server._live_activity_saved_path().exists()


@pytest.mark.parametrize("body", [
    "not json",
    json.dumps({"version": 1}),
    json.dumps({"saved_at": "x", "sessions": {}}),
    json.dumps({"saved_at": 1.0, "sessions": {"too-old": {}}}),
    json.dumps({"saved_at": time.time() + 9999, "sessions": {"future": {}}}),
    json.dumps({"saved_at": time.time(), "sessions": ["wrong-shape"]}),
])
def test_unusable_saved_copy_is_ignored(live, body):
    _reset_to_boot()
    server._live_activity_saved_path().write_text(body)
    assert server._load_saved_live_activity() is False
    assert server._live_activity_snapshot["data"] is None
