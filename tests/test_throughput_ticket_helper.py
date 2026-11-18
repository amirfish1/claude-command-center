from types import SimpleNamespace

import pytest

import server
from ccc_server import bg_helper, usage_stats


@pytest.mark.parametrize("mode", ["disabled", "child", "unavailable"])
def test_ticket_counts_fall_back_without_changing_day_boundaries(monkeypatch, mode):
    monkeypatch.setattr(bg_helper, "enabled", lambda: mode != "disabled")
    monkeypatch.setattr(bg_helper, "is_child", lambda: mode == "child")
    calls = []
    def call(*args, **kwargs):
        calls.append(args[0])
        return None
    monkeypatch.setattr(bg_helper, "call", call)
    items = [
        {"created_at": "2026-01-01T00:00:00Z", "closed_at": "2026-01-02T00:00:00Z"},
        {"created_at": "2026-01-02T00:00:00Z"},
        {"created_at": "2026-01-01T12:00:00Z", "closed_at": "2026-01-01T23:59:59Z"},
        {"created_at": "2025-12-31T12:00:00Z"},
        {"created_at": "invalid"},
    ]
    monkeypatch.setattr(server, "_q", SimpleNamespace(list_items=lambda: items))
    assert usage_stats._throughput_daily_ticket_counts(1767225600, 1767312000) == {
        "available": True, "opened": 2, "closed": 1, "still_open": 2,
    }
    assert calls == (["throughput_ticket_counts"] if mode == "unavailable" else [])


def test_ticket_scan_failure_remains_unavailable(monkeypatch):
    def fail():
        raise OSError("test queue unavailable")
    monkeypatch.setattr(server, "_q", SimpleNamespace(list_items=fail))
    assert usage_stats._throughput_daily_ticket_counts_local(100, 200) == {
        "available": False, "opened": 0, "closed": 0, "still_open": 0,
    }
