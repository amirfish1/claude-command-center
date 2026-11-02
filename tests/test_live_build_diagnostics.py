"""Attribute slow live builds without scanning or publishing session data."""

import server


def test_live_build_reports_phase_waits_and_keeps_cached_polls_cheap(monkeypatch):
    clocks = {"wall": 0.0, "cpu": 0.0}
    calls = []

    def work(name, seconds, result):
        calls.append(name)
        clocks["wall"] += seconds
        clocks["cpu"] += seconds / 10
        return result

    monkeypatch.setattr(server.time, "perf_counter", lambda: clocks["wall"])
    monkeypatch.setattr(server.time, "thread_time", lambda: clocks["cpu"])
    monkeypatch.setattr(server, "_live_activity_snapshot", {"ts": 0, "data": None})
    monkeypatch.setattr(server, "_discover_live_session_ids", lambda: work(
        "discovery", 0.1, {"live-a", "ended"}))
    monkeypatch.setattr(server, "_codex_pool_candidate_sids", lambda: work(
        "pool", 0.2, {"live-a", "live-b"}))
    monkeypatch.setattr(server, "_archive_session_is_live", lambda sid: work(
        "liveness", 0.3, sid != "ended"))
    monkeypatch.setattr(server, "_live_activity_entry_for_session", lambda sid: work(
        "activity", 0.4, {"is_live": True, "pending_tool": "Shell"}))
    monkeypatch.setattr(server, "_record_live_build_ms", lambda ms: None)
    events = []
    monkeypatch.setattr(server, "record_event", lambda kind, ms, detail: events.append(
        (kind, round(ms), detail)))

    result = server.build_live_sessions_activity()
    assert set(result) == {"live-a", "live-b"}
    assert result["live-a"]["pending_tool"] == "Shell"
    kind, wall, detail = events[0]
    assert (kind, wall, detail["cpu_ms"]) == ("live_build", 2000, 200)
    assert (detail["candidates"], detail["rows"]) == (3, 2)
    assert detail["phases"] == {
        "discovery": {"wall_ms": 100, "cpu_ms": 10, "max_ms": 100, "count": 1},
        "pool_candidates": {"wall_ms": 200, "cpu_ms": 20, "max_ms": 200, "count": 1},
        "liveness": {"wall_ms": 900, "cpu_ms": 90, "max_ms": 300, "count": 3},
        "activity": {"wall_ms": 800, "cpu_ms": 80, "max_ms": 400, "count": 2},
    }
    assert "live-a" not in repr(detail)
    calls_before = list(calls)
    assert server.build_live_sessions_activity() is result
    assert calls == calls_before
    assert len(events) == 1
