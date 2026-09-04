import threading
import time
import json
import urllib.request
import inspect

import pytest


def _hub(*, capacity=4, boot_id="boot-test"):
    from ccc_server.events import DashboardEventHub

    return DashboardEventHub(capacity=capacity, boot_id=boot_id)


def test_publish_returns_versioned_event():
    hub = _hub()

    event = hub.publish(
        "session.patch",
        entity={"type": "session", "id": "session-1"},
        patch={"status": "idle"},
    )

    assert event["schema"] == 1
    assert event["boot_id"] == "boot-test"
    assert event["seq"] == 1
    assert event["topic"] == "session.patch"
    assert event["entity_version"] == 1
    assert event["entity"] == {"type": "session", "id": "session-1"}
    assert event["patch"] == {"status": "idle"}
    assert event["invalidate"] == []
    assert event["ts"].endswith("Z")


def test_entity_versions_advance_independently():
    hub = _hub()

    first = hub.publish(
        "conversation.patch",
        entity={"type": "conversation", "id": "one"},
        patch={"title": "First"},
    )
    other = hub.publish(
        "conversation.patch",
        entity={"type": "conversation", "id": "two"},
        patch={"title": "Other"},
    )
    second = hub.publish(
        "conversation.patch",
        entity={"type": "conversation", "id": "one"},
        patch={"title": "Second"},
    )

    assert [first["entity_version"], other["entity_version"], second["entity_version"]] == [1, 1, 2]
    assert [first["seq"], other["seq"], second["seq"]] == [1, 2, 3]


def test_snapshot_returns_copies_not_mutable_ring_records():
    hub = _hub()
    published = hub.publish(
        "queue.patch",
        entity={"type": "ticket", "id": "CCC-1"},
        patch={"state": "open"},
    )

    published["patch"]["state"] = "corrupted"
    snapshot = hub.snapshot_since(0, boot_id="boot-test")
    snapshot.events[0]["patch"]["state"] = "also-corrupted"

    fresh = hub.snapshot_since(0, boot_id="boot-test")
    assert fresh.events[0]["patch"] == {"state": "open"}


def test_snapshot_with_different_boot_requires_resync():
    hub = _hub()
    hub.publish("invalidate", invalidate=[{"resource": "archive"}])

    snapshot = hub.snapshot_since(1, boot_id="old-boot")

    assert snapshot.resync_required is True
    assert snapshot.boot_id == "boot-test"
    assert snapshot.latest_seq == 1
    assert snapshot.events == ()


def test_expired_cursor_requires_resync():
    hub = _hub(capacity=2)
    for value in range(3):
        hub.publish("invalidate", invalidate=[{"resource": "archive", "id": str(value)}])

    snapshot = hub.snapshot_since(0, boot_id="boot-test")

    assert snapshot.resync_required is True
    assert snapshot.latest_seq == 3
    assert snapshot.events == ()


def test_cursor_at_ring_boundary_can_replay():
    hub = _hub(capacity=2)
    for value in range(3):
        hub.publish("invalidate", invalidate=[{"resource": "archive", "id": str(value)}])

    snapshot = hub.snapshot_since(1, boot_id="boot-test")

    assert snapshot.resync_required is False
    assert [event["seq"] for event in snapshot.events] == [2, 3]


def test_wait_since_wakes_when_event_is_published():
    hub = _hub()
    result = {}

    def wait_for_event():
        result["snapshot"] = hub.wait_since(0, boot_id="boot-test", timeout=1.0)

    thread = threading.Thread(target=wait_for_event)
    thread.start()
    time.sleep(0.02)
    hub.publish("invalidate", invalidate=[{"resource": "queue"}])
    thread.join(timeout=1.0)

    assert thread.is_alive() is False
    assert [event["topic"] for event in result["snapshot"].events] == ["invalidate"]


def test_wait_since_times_out_without_events():
    hub = _hub()

    started = time.monotonic()
    snapshot = hub.wait_since(0, boot_id="boot-test", timeout=0.02)

    assert time.monotonic() - started >= 0.015
    assert snapshot.events == ()
    assert snapshot.resync_required is False


@pytest.mark.parametrize(
    ("topic", "kwargs"),
    [
        ("", {}),
        ("session.patch", {"entity": {"type": "session"}}),
        ("session.patch", {"entity": {"id": "one"}}),
        ("session.patch", {"entity": "one"}),
    ],
)
def test_publish_rejects_invalid_envelopes(topic, kwargs):
    hub = _hub()

    with pytest.raises((TypeError, ValueError)):
        hub.publish(topic, **kwargs)


def _read_sse_event(response):
    event_id = None
    payload = None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and payload is None:
        line = response.readline().decode("utf-8")
        if line.startswith("id: "):
            event_id = int(line[len("id: "):].strip())
        elif line.startswith("data: "):
            payload = json.loads(line[len("data: "):])
    return event_id, payload


@pytest.fixture
def dashboard_server(monkeypatch):
    import server
    from ccc_server.events import DashboardEventHub

    monkeypatch.setattr(
        server,
        "_dashboard_events",
        DashboardEventHub(capacity=8, boot_id="http-boot"),
        raising=False,
    )
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_dashboard_events_endpoint_replays_envelope(dashboard_server):
    server, base = dashboard_server
    published = server._dashboard_events.publish(
        "session.patch",
        entity={"type": "session", "id": "session-1"},
        patch={"status": "idle"},
    )

    with urllib.request.urlopen(base + "/api/events?since=0", timeout=2) as response:
        event_id, payload = _read_sse_event(response)

    assert response.headers.get("Content-Type") == "text/event-stream"
    assert event_id == published["seq"]
    assert payload == published


def test_dashboard_events_endpoint_honors_last_event_id(dashboard_server):
    server, base = dashboard_server
    server._dashboard_events.publish("invalidate", invalidate=[{"resource": "queue"}])
    second = server._dashboard_events.publish(
        "invalidate", invalidate=[{"resource": "archive"}]
    )
    request = urllib.request.Request(
        base + "/api/events", headers={"Last-Event-ID": "1"}
    )

    with urllib.request.urlopen(request, timeout=2) as response:
        event_id, payload = _read_sse_event(response)

    assert event_id == 2
    assert payload == second


def test_dashboard_events_endpoint_emits_resync_for_expired_cursor(dashboard_server):
    server, base = dashboard_server
    server._dashboard_events = _hub(capacity=1, boot_id="http-boot")
    server._dashboard_events.publish("invalidate", invalidate=[{"resource": "one"}])
    server._dashboard_events.publish("invalidate", invalidate=[{"resource": "two"}])

    with urllib.request.urlopen(base + "/api/events?since=0", timeout=2) as response:
        event_id, payload = _read_sse_event(response)

    assert event_id == 2
    assert payload["topic"] == "resync.required"
    assert payload["boot_id"] == "http-boot"
    assert payload["seq"] == 2


def test_external_queue_signature_change_publishes_unified_invalidation(monkeypatch):
    import server
    from ccc_server.events import DashboardEventHub

    hub = DashboardEventHub(capacity=8, boot_id="watch-test")
    monkeypatch.setattr(server, "_dashboard_events", hub)
    monkeypatch.setattr(server, "_dashboard_queue_signature", lambda: (2, 1, 0))

    current = server._dashboard_queue_watch_tick((1, 1, 0))

    assert current == (2, 1, 0)
    events = hub.snapshot_since(0, boot_id="watch-test").events
    assert events[-1]["invalidate"] == [
        {"resource": "queue", "reason": "external-change"}
    ]


def test_unified_stream_owns_shared_external_watch_lifetime():
    import server

    source = inspect.getsource(server.CommandCenterHandler._stream_dashboard_events)
    assert "_dashboard_event_watch_enter()" in source
    assert "_dashboard_event_watch_exit()" in source
