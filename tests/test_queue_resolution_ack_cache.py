"""Resolution acknowledgments must survive immediate cached list polls."""
import copy
import time

import server  # registers the decomposed modules before importing them
from ccc_server import queue_events


def test_ack_updates_every_cached_view_and_slim_snapshot_without_reloading(monkeypatch):
    old = {
        "ref": "DEMO-1", "status": "closed", "updated_at": "old",
        "resolution": {"summary": "done", "unresolved": ["pending"]},
        "history": [{"event": "close"}], "waiting_on": [],
        "stages": {"current": "done"},
    }
    unrelated = {"ref": "DEMO-2", "status": "open"}
    original = copy.deepcopy(old)
    snapshot = [old, unrelated]
    synced_at = time.time()
    cache = {
        ("", ""): {"ts": synced_at, "items": snapshot},
        ("closed", ""): {"ts": synced_at, "items": [old]},
        ("open", ""): {"ts": synced_at, "items": [unrelated]},
    }
    monkeypatch.setattr(queue_events, "_ux_fixes_list_cache", cache)
    monkeypatch.setattr(queue_events, "_list_items_with_waiting_on",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not reload")))
    slim_before = queue_events._ux_fixes_slim_items(snapshot)
    updated = copy.deepcopy(old)
    updated["resolution"]["unresolved_ack"] = {"0": {"by": "dashboard", "at": "now"}}
    updated["history"].append({"event": "ack"})
    updated["updated_at"] = "now"
    updated.pop("waiting_on")
    updated.pop("stages")
    queue_events._ux_fixes_cache_ack(updated)

    for key in [("", ""), ("closed", "")]:
        row = queue_events._ux_fixes_list_items_cached(*key)[0]
        assert row["resolution"] == updated["resolution"]
        assert row["history"] == updated["history"]
        assert row["updated_at"] == "now"
        assert row["waiting_on"] == []
        assert row["stages"] == {"current": "done"}
        assert cache[key]["ts"] == synced_at
    assert old == original, "existing snapshots, including close history, stay untouched"
    assert cache[("", "")]["items"][1] is unrelated
    slim_after = queue_events._ux_fixes_slim_items(cache[("", "")]["items"])
    assert slim_after is not slim_before
    assert slim_after[0]["resolution"]["unresolved_ack"] == updated["resolution"]["unresolved_ack"]

    updated["resolution"].pop("unresolved_ack")
    queue_events._ux_fixes_cache_ack(updated)
    assert "unresolved_ack" not in queue_events._ux_fixes_list_items_cached()[0]["resolution"]


def test_ack_endpoint_updates_cached_lists_on_success_and_preserves_errors(monkeypatch):
    import json
    import threading
    import urllib.error
    import urllib.request

    row = {"ref": "DEMO-1", "status": "closed",
           "resolution": {"unresolved": ["pending"]}}
    cache = {("", ""): {"ts": time.time(), "items": [copy.deepcopy(row)]}}
    monkeypatch.setattr(queue_events, "_ux_fixes_list_cache", cache)
    monkeypatch.setattr(server, "_uxq_item_payload", lambda item: item)
    calls = []

    def acknowledge(ref, **kwargs):
        calls.append((ref, kwargs))
        if ref != row["ref"]:
            return None
        if kwargs["undo"]:
            row["resolution"].pop("unresolved_ack", None)
        else:
            row["resolution"]["unresolved_ack"] = {"0": {"by": "dashboard", "at": "now"}}
        return copy.deepcopy(row)

    monkeypatch.setattr(server._q, "ack_resolution", acknowledge)
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        def post(ref="DEMO-1", undo=False):
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_address[1]}/api/ux-fixes/ack",
                data=json.dumps({"ref": ref, "field": "unresolved", "index": 0, "undo": undo}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)

        status, payload = post()
        assert status == 200 and payload["ok"]
        assert queue_events._ux_fixes_list_items_cached()[0]["resolution"] == payload["item"]["resolution"]
        assert calls[-1] == ("DEMO-1", {"targets": [("unresolved", 0)], "all_items": False,
                                         "by": "dashboard", "undo": False})
        assert post(undo=True)[0] == 200
        assert "unresolved_ack" not in queue_events._ux_fixes_list_items_cached()[0]["resolution"]
        snapshot = copy.deepcopy(cache)
        assert post(ref="DEMO-999")[0] == 404
        assert cache == snapshot
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
