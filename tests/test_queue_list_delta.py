"""/api/queue/list?since=<version> deltas (CCC-142).

The board list is multi-MB; boards refetch it on every queue change. A client
that sends the version it holds gets only changed tickets plus the ref order,
and worker heartbeats (workers.json rewrites) never ask boards to refetch
tickets. Synthetic rows only.
"""
from contextlib import closing
import gzip
import http.client
import importlib
import json
import socket
import sys
import threading

import pytest


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


def test_stalled_queue_response_releases_admission(monkeypatch):
    server = _load_server()
    rows = _rows()
    rows[0]["note"] = "a" * (1 << 20)
    monkeypatch.setattr(server, "_ux_fixes_list_items_cached", lambda *_a, **_kw: rows)
    monkeypatch.setattr(server, "_ux_fixes_list_synced_at", lambda *_: 1)
    monkeypatch.setattr(server, "_bg_route_is_cheap", lambda _: False)
    monkeypatch.setattr(server, "_save_bg_route_cpu", lambda: None)
    monkeypatch.setattr(server, "_QUEUE_LIST_WRITE_TIMEOUT", 0.4)

    class Handler(server.CommandCenterHandler):
        def setup(self):
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
            super().setup()

    with server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as listener:
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.socket() as stalled:
                stalled.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
                stalled.settimeout(3)
                stalled.connect(listener.server_address)
                stalled.sendall(b"GET /api/queue/list HTTP/1.0\r\n\r\n")
                with closing(http.client.HTTPResponse(stalled)) as incomplete:
                    incomplete.begin()
                    assert incomplete.status == 200
                    assert incomplete.length > 1 << 20
                    # Leave the first body unread while a second board polls.
                    with closing(http.client.HTTPConnection(*listener.server_address, timeout=3)) as reader:
                        reader.request("GET", "/api/queue/list", headers={"Accept-Encoding": "gzip"})
                        response = reader.getresponse()
                        assert response.status == 200
                        assert response.getheader("Content-Encoding") == "gzip"
                        assert json.loads(gzip.decompress(response.read()))["items"] == rows
                    with pytest.raises(http.client.IncompleteRead):
                        incomplete.read()
        finally:
            listener.shutdown()
            thread.join(timeout=3)


def test_worker_heartbeat_invalidates_only_changed_worker_data(monkeypatch):
    server = _load_server()
    sent = []
    monkeypatch.setattr(server, "_invalidate_dashboard",
                        lambda resource, *, reason: sent.append((resource, reason)))
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lambda: (set(), set()))
    monkeypatch.setattr(server, "_wt_read_config", lambda: {})
    record = {"alive": True, "pid": 123, "session_id": "session-a", "started_at": 1234,
              "queue": "TEST", "engine": "claude", "idle_seconds": 2}
    monkeypatch.setattr(server, "_wt_read_workers", lambda *, include_activity: [dict(record)])
    signature = (("store", 1), None, ("config", 1), ("workers", 1), 0)
    monkeypatch.setattr(server, "_dashboard_queue_signature", lambda: signature)
    state = server._dashboard_queue_watch_tick(None, None)

    record.update(heartbeat_at=1235, idle_seconds=10)
    signature = signature[:3] + (("workers", 2),) + signature[4:]
    state = server._dashboard_queue_watch_tick(*state)

    record["idle_seconds"] = 65
    signature = signature[:3] + (("workers", 3),) + signature[4:]
    state = server._dashboard_queue_watch_tick(*state)

    record["released_at"] = 1236
    signature = signature[:3] + (("workers", 4),) + signature[4:]
    state = server._dashboard_queue_watch_tick(*state)

    signature = (("store", 2),) + signature[1:]
    server._dashboard_queue_watch_tick(*state)
    assert sent == [
        ("workers", "worker-health-only"), ("workers", "worker-heartbeat"),
        ("workers", "worker-heartbeat"), ("queue", "external-change"),
    ]
