import json
import threading
import urllib.request

import pytest

import server
from ccc_server import today_stars
from ccc_server.events import DashboardEventHub


def test_set_star_round_trip_and_unstar(tmp_path):
    path = tmp_path / "today-stars.json"
    assert today_stars.load(path) == {}
    stars = today_stars.set_star(path, "s1", True, now=1000.0)
    assert stars == {"s1": 1000.0}
    assert today_stars.load(path, now=1001.0) == {"s1": 1000.0}
    assert today_stars.set_star(path, "s1", False, now=1002.0) == {}


def test_old_stars_are_pruned_and_bad_data_ignored(tmp_path):
    path = tmp_path / "today-stars.json"
    path.write_text(json.dumps({"old": 1.0, "new": 200000.0, "bad": "x", "": 5}))
    assert today_stars.load(path, now=200000.0 + 3600) == {"new": 200000.0}
    path.write_text("[1, 2]")
    assert today_stars.load(path) == {}


def test_missing_session_id_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        today_stars.set_star(tmp_path / "s.json", "  ", True)


@pytest.fixture
def api_server(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TODAY_STARS_FILE", tmp_path / "today-stars.json")
    hub = DashboardEventHub(capacity=16, boot_id="today-stars-test")
    monkeypatch.setattr(server, "_dashboard_events", hub)
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", hub
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _req(base, method, body=None):
    request = urllib.request.Request(
        base + "/api/today-stars",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def test_endpoint_stars_publishes_and_lists(api_server):
    base, hub = api_server
    posted = _req(base, "POST", {"session_id": "sess-a", "starred": True})
    assert posted["ok"] and set(posted["stars"]) == {"sess-a"}
    assert set(_req(base, "GET")["stars"]) == {"sess-a"}
    events = list(hub.snapshot_since(0, boot_id=hub.boot_id).events)
    assert events[-1]["topic"] == "today-stars.patch"
    assert set(events[-1]["patch"]["stars"]) == {"sess-a"}
    assert _req(base, "POST", {"session_id": "sess-a", "starred": False})["stars"] == {}
