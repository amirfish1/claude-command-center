"""History must not wait on remote queues or publish missing data as zero."""
import threading
from types import SimpleNamespace

import server  # Register shared core names before loading extracted modules.
from ccc_server import queue_events as history


def setup_history(monkeypatch, *, items, fresh):
    now = history.time.time()
    snapshots = []
    core = SimpleNamespace(
        _wt_list_items_for_requests=lambda: items,
        _wt_items_memo_lock=threading.Lock(),
        _wt_items_memo={"items": items if fresh else None,
                        "at": now if fresh else 0},
        _WT_ITEMS_SNAPSHOT_TTL_S=30,
    )
    monkeypatch.setattr(history, "_core", core)
    monkeypatch.setattr(history, "_queue_history_cache",
                        {"ts": 0, "key": None, "result": None})
    monkeypatch.setattr(history, "_record_queue_snapshot_if_due", snapshots.append)
    monkeypatch.setattr(history, "_read_queue_history_rows", lambda: [])
    monkeypatch.setattr(history, "_backfill_queue_history",
                        lambda *args: [{"ts": now, "open": len(args[2])}])
    return core, snapshots


def test_fresh_shared_snapshot_is_cached_and_recorded(monkeypatch):
    core, snapshots = setup_history(monkeypatch, items=[{"status": "open"}], fresh=True)
    first = history.compute_queue_history()
    assert first["points"][0]["open"] == 1
    assert first["refreshing"] is False
    core._wt_list_items_for_requests = lambda: (_ for _ in ()).throw(AssertionError("cache miss"))
    assert history.compute_queue_history() is first
    assert snapshots == [[{"status": "open"}]]


def test_missing_snapshot_never_becomes_a_zero_sample_and_retries(monkeypatch):
    core, snapshots = setup_history(monkeypatch, items=[], fresh=False)
    pending = history.compute_queue_history()
    assert pending["points"] == []
    assert pending["refreshing"] is True
    assert snapshots == []
    core._wt_items_memo.update(items=[], at=history.time.time())
    core._wt_list_items_for_requests = lambda: core._wt_items_memo["items"]
    result = history.compute_queue_history()
    assert result["refreshing"] is False
    assert snapshots == [[]]  # A genuinely empty queue is a valid sample.


def test_stale_data_can_draw_history_but_not_fresh_snapshots(monkeypatch):
    _, snapshots = setup_history(monkeypatch, items=[{"status": "open"}], fresh=False)
    result = history.compute_queue_history()
    assert result["points"][0]["open"] == 1
    assert result["refreshing"] is True
    assert snapshots == []
