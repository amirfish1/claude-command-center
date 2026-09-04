import json
import threading
import urllib.request

import pytest

import server
from ccc_server.events import DashboardEventHub


@pytest.fixture
def recording_events(monkeypatch):
    hub = DashboardEventHub(capacity=16, boot_id="publisher-test")
    monkeypatch.setattr(server, "_dashboard_events", hub)
    return hub


@pytest.fixture
def api_server():
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _post(base, path, body):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def _events(hub):
    return list(hub.snapshot_since(0, boot_id=hub.boot_id).events)


def test_publish_dashboard_patch_uses_conversation_entity(recording_events):
    event = server._publish_dashboard_patch(
        "conversation.patch", "conversation", "session-1", {"pinned": True}
    )

    assert event["topic"] == "conversation.patch"
    assert event["entity"] == {"type": "conversation", "id": "session-1"}
    assert event["patch"] == {"pinned": True}


def test_invalidate_dashboard_scopes_resource_and_optional_id(recording_events):
    event = server._invalidate_dashboard("queue", entity_id="CCC", reason="config")

    assert event["topic"] == "invalidate"
    assert event["invalidate"] == [
        {"resource": "queue", "id": "CCC", "reason": "config"}
    ]


def test_successful_rename_publishes_after_mutation(
    api_server, recording_events, monkeypatch
):
    calls = []

    def rename(session_id, name):
        calls.append((session_id, name))
        return {"ok": True}

    monkeypatch.setattr(server, "rename_session", rename)

    response = _post(
        api_server,
        "/api/conversations/session-1/rename",
        {"session_id": "session-1", "name": "New title"},
    )

    assert calls == [("session-1", "New title")]
    assert response["ok"] is True
    assert _events(recording_events)[-1]["patch"] == {"name": "New title", "title": "New title"}


def test_failed_rename_emits_no_event(api_server, recording_events, monkeypatch):
    monkeypatch.setattr(
        server, "rename_session", lambda _session_id, _name: {"ok": False, "error": "write failed"}
    )

    response = _post(
        api_server,
        "/api/conversations/session-1/rename",
        {"session_id": "session-1", "name": "Nope"},
    )

    assert response["ok"] is False
    assert _events(recording_events) == []


def test_queue_config_save_publishes_scoped_invalidation(
    tmp_path, api_server, recording_events, monkeypatch
):
    config_path = tmp_path / "queue-config.json"
    monkeypatch.setattr(
        server,
        "_queue_config_from_payload",
        lambda _payload: {"queue": "CCC", "config": {"auto_drain": False}},
    )
    monkeypatch.setattr(server, "_wt_read_config", lambda: {})
    monkeypatch.setattr(server, "_wt_config_path", lambda: config_path)
    monkeypatch.setattr(server, "_WT_CONFIG_AVAILABLE", False)
    monkeypatch.setattr(server, "_wt_log_queue_config_change", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_reconcile_once_async", lambda: None)

    response = _post(api_server, "/api/queue/config", {"queue": "CCC"})

    assert response["ok"] is True
    assert _events(recording_events)[-1]["invalidate"] == [
        {"resource": "queue", "id": "CCC", "reason": "config"}
    ]


def test_successful_spawn_publishes_session_patch_and_archive_invalidation(recording_events):
    server._publish_spawn_dashboard_event({
        "ok": True,
        "session_id": "session-new",
        "engine": "codex",
        "pid": 123,
        "spawned_via": "ui",
    })

    events = _events(recording_events)
    assert events[0]["topic"] == "session.patch"
    assert events[0]["entity"] == {"type": "session", "id": "session-new"}
    assert events[0]["patch"] == {
        "status": "starting",
        "engine": "codex",
        "pid": 123,
        "spawned_via": "ui",
    }
    assert events[1]["invalidate"] == [
        {"resource": "archive", "id": "session-new", "reason": "spawn"}
    ]


def test_failed_spawn_publishes_nothing(recording_events):
    server._publish_spawn_dashboard_event({"ok": False, "error": "no binary"})

    assert _events(recording_events) == []
