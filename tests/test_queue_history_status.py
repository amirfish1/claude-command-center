"""Hourly charts keep unknown history separate from measured zero counts."""
import server
from ccc_server import queue_events as history


def test_status_subsets_partition_active_tickets(monkeypatch):
    monkeypatch.setattr(history, "with_waiting_on", lambda items: items)
    items = [
        {"status": "closed", "needs_input": True},
        {"status": "open"},
        {"status": "open", "waiting_on": ["DEMO-1"]},
        {"status": "awaiting_answer", "needs_input": True, "wait": {"on": "owner"}},
        {"status": "blocked", "needs_input": True, "wait": {"on": "external"}},
        {"status": "in_progress", "claimed_by": "example-worker"},
        {"status": "in_review"},
        {"status": "awaiting_release"},
        {"status": "open", "readiness": "needs-spec"},
        {"status": "open", "claimable": False},
    ]
    counts = history._queue_history_status_counts(items)
    assert counts == dict(open=9, needs_input=1, awaiting_worker=1, waiting_tickets=1, other=6)
    assert sum(v for k, v in counts.items() if k != "open") == counts["open"]


def test_hourly_series_aligns_window_and_keeps_missing_hours_unknown():
    now = 200 * 3600 + 120
    rows = [
        {"ts": 20 * 3600, "open": 999},  # Outside the seven-day window.
        {"ts": 199 * 3600 + 10, "open": 3, "needs_input": 2},  # Legacy sample.
        {"ts": 200 * 3600 + 10, "open": 5, "needs_input": 2, "status_counts_version": 1},
        {"ts": 200 * 3600 + 60, "open": 4, "needs_input": 0,
         "awaiting_worker": 1, "waiting_tickets": 1, "other": 2, "status_counts_version": 1},
        {"ts": now + 3600, "open": 999},
    ]
    result = history._hourly_queue_history(rows, now, 7, 1)
    assert len(result) == 169
    assert result[0]["ts"] == 32 * 3600
    assert all(p["open"] is None for p in result[:-2])
    assert result[-2]["open"] == 3
    assert result[-2]["needs_input"] is None  # Old broad counts are not owner counts.
    assert result[-1] == dict(ts=200 * 3600, open=4, needs_input=0,
                              awaiting_worker=1, waiting_tickets=1, other=2)


def test_snapshot_upgrades_old_schema_without_waiting_fifteen_minutes(monkeypatch, tmp_path):
    import json
    target = tmp_path / "history.jsonl"
    target.write_text(json.dumps({"ts": history.time.time(), "open": 7}) + "\n")
    monkeypatch.setattr(history, "_queue_history_path", lambda: target)
    monkeypatch.setattr(history, "with_waiting_on", lambda items: items)
    history._record_queue_snapshot_if_due([{"status": "open"}])
    rows = [json.loads(s) for s in target.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[-1]["awaiting_worker"] == 1
    assert rows[-1]["status_counts_version"] == 1
    history._record_queue_snapshot_if_due([])
    assert len(target.read_text().splitlines()) == 2
