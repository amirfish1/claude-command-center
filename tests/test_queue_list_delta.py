"""/api/queue/list?since=<version> deltas (CCC-142).

The board list is multi-MB; boards refetch it on every queue change. A client
that sends the version it holds gets only changed tickets plus the ref order,
and worker heartbeats (workers.json rewrites) never ask boards to refetch
tickets. Synthetic rows only.
"""
import importlib
import sys


def _load_server():
    sys.modules.pop("server", None)
    return importlib.import_module("server")


def _rows(n=4):
    return [{"ref": "DEMO-%d" % i, "status": "open", "title": "t%d" % i} for i in range(n)]


def test_delta_sends_only_changed_and_new_tickets():
    server = _load_server()
    scope = (None, None, True)
    v1_rows = _rows()
    v1, keys1, d1 = server._queue_list_version(scope, v1_rows, v1_rows)
    assert keys1 == ["DEMO-0", "DEMO-1", "DEMO-2", "DEMO-3"]
    v2_rows = [dict(r) for r in v1_rows]
    v2_rows[1]["status"] = "closed"
    del v2_rows[2]
    v2_rows.append({"ref": "DEMO-9", "status": "open"})
    v2, keys2, d2 = server._queue_list_version(scope, v2_rows, v2_rows)
    assert v2 != v1
    delta = server._queue_list_delta(scope, v1, v2_rows, v2, keys2, d2)
    assert delta["delta"] is True and delta["version"] == v2
    assert delta["order"] == ["DEMO-0", "DEMO-1", "DEMO-3", "DEMO-9"]
    assert sorted(delta["changed"]) == ["DEMO-1", "DEMO-9"]
    assert delta["changed"]["DEMO-1"]["status"] == "closed"


def test_same_snapshot_is_reused_and_reports_unchanged():
    server = _load_server()
    scope = (None, None, True)
    rows = _rows()
    v1, _, _ = server._queue_list_version(scope, rows, rows)
    v1b, keys, d = server._queue_list_version(scope, rows, rows)
    assert v1b == v1
    delta = server._queue_list_delta(scope, v1, rows, v1, keys, d)
    assert delta == {"delta": True, "unchanged": True, "version": v1, "count": 4}


def test_unknown_or_other_scope_version_falls_back_to_full_list():
    server = _load_server()
    rows = _rows()
    v1, keys, d = server._queue_list_version((None, None, True), rows, rows)
    assert server._queue_list_delta((None, None, True), "nope", rows, v1, keys, d) is None
    assert server._queue_list_delta(("open", None, True), v1, rows, v1, keys, d) is None


def test_keyless_and_duplicate_rows_always_ship():
    server = _load_server()
    scope = (None, None, False)
    rows = [{"title": "no ref"}, {"id": "dup"}, {"id": "dup", "x": 1}]
    v1, keys, d = server._queue_list_version(scope, rows, rows)
    assert keys == ["#0", "dup", "#2"]
    rows2 = list(rows)
    v2, keys2, d2 = server._queue_list_version(scope, rows2, rows2)
    delta = server._queue_list_delta(scope, v1, rows2, v2, keys2, d2)
    assert sorted(delta["changed"]) == ["#0", "#2"]


def test_worker_heartbeat_does_not_invalidate_tickets(monkeypatch):
    server = _load_server()
    sent = []
    monkeypatch.setattr(server, "_invalidate_dashboard",
                        lambda resource, **kw: sent.append(resource))
    base = (("store", 1), None, ("config", 1), ("workers", 1), 0)
    beat = base[:3] + (("workers", 2),) + base[4:]
    monkeypatch.setattr(server, "_dashboard_queue_signature", lambda: beat)
    server._dashboard_queue_watch_tick(base)
    store = (("store", 2),) + beat[1:]
    monkeypatch.setattr(server, "_dashboard_queue_signature", lambda: store)
    server._dashboard_queue_watch_tick(beat)
    assert sent == ["workers", "queue"]
