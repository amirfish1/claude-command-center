import threading
import time

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
