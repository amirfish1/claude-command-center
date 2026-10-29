"""Session -> queue link for "Create queue for this session" (CCC-1225).

The link used to live in one browser's localStorage. It now lives on the
queue's WatchTower config entry (session_id), so agents, a task panel and other
browsers can find a session's queue; offer_workers drives a one-time
"start N workers?" prompt once the first tickets land.
"""
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import server
from ccc_server import session_queue

ROOT = Path(__file__).resolve().parent.parent
SID = "849d9611-c646-40e7-b696-a6064141938e"


def test_worker_offer_updates():
    assert session_queue.worker_offer_updates(0) == {"offer_workers": None}
    assert session_queue.worker_offer_updates("2") == {
        "offer_workers": None, "auto_drain": True, "desired_workers": 2}
    for bad in (-1, 17, "x"):
        with pytest.raises(ValueError):
            session_queue.worker_offer_updates(bad)


def test_valid_session_id_rejects_junk():
    assert session_queue.valid_session_id(" " + SID + " ") == SID
    for bad in ("", "../etc", "a b", "x" * 200):
        with pytest.raises(ValueError):
            session_queue.valid_session_id(bad)


@pytest.fixture
def api(tmp_path, monkeypatch):
    cfg = tmp_path / "queue-config.json"
    monkeypatch.setenv("WATCHTOWER_CONFIG_FILE", str(cfg))
    monkeypatch.setattr(server._wt_config, "CONFIG_FILE", cfg)
    monkeypatch.setattr(server, "_reconcile_once_async", lambda: None)
    monkeypatch.setattr(server, "_wt_log_queue_config_change", lambda *a, **k: None)
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, payload=None):
        req = urllib.request.Request(
            base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def stored(queue):
        return (json.loads(cfg.read_text()) if cfg.exists() else {}).get(queue) or {}

    yield call, stored
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_create_stores_link_and_lookup_finds_it(api):
    call, stored = api
    status, _ = call("/api/queue/config", {
        "queue": "LOGIN-REDIRECT", "auto_drain": True, "session_id": SID, "offer_workers": True})
    assert status == 200
    conf = stored("LOGIN-REDIRECT")
    assert conf["session_id"] == SID
    assert conf["offer_workers"] is True
    assert conf["auto_drain"] is False   # brand-new queue: auto-drain forced off
    status, data = call("/api/queue/for-session?session_id=" + SID)
    assert status == 200 and data["queue"] == "LOGIN-REDIRECT"
    status, data = call("/api/queue/for-session?session_id=other-session")
    assert status == 200 and data["queue"] is None
    assert call("/api/queue/for-session?session_id=../x")[0] == 400


def test_resave_without_the_keys_keeps_the_link(api):
    call, stored = api
    assert call("/api/queue/config", {"queue": "LQ", "session_id": SID, "offer_workers": True})[0] == 200
    # The gear dialog re-posts the full config without knowing these keys.
    assert call("/api/queue/config", {"queue": "LQ", "auto_drain": True})[0] == 200
    assert stored("LQ")["session_id"] == SID
    assert stored("LQ")["offer_workers"] is True
    # An explicit false clears the offer.
    assert call("/api/queue/config", {"queue": "LQ", "offer_workers": False})[0] == 200
    assert "offer_workers" not in stored("LQ")


def test_bad_session_id_writes_nothing(api):
    call, stored = api
    assert call("/api/queue/config", {"queue": "BAD", "session_id": "a b"})[0] == 400
    assert stored("BAD") == {}


def test_offer_decline_and_start(api):
    call, stored = api
    assert call("/api/queue/config", {"queue": "OQ", "session_id": SID, "offer_workers": True})[0] == 200
    status, data = call("/api/queue/offer-workers", {"queue": "OQ", "workers": 0})
    assert status == 200 and data["auto_drain"] is False
    assert "offer_workers" not in stored("OQ")
    assert stored("OQ")["session_id"] == SID
    assert call("/api/queue/config", {"queue": "OQ", "offer_workers": True})[0] == 200
    status, data = call("/api/queue/offer-workers", {"queue": "oq", "workers": 3})
    assert status == 200 and data["auto_drain"] is True and data["desired_workers"] == 3
    conf = stored("OQ")
    assert conf["auto_drain"] is True and conf["desired_workers"] == 3
    assert "offer_workers" not in conf
    assert call("/api/queue/offer-workers", {"queue": "NOPE", "workers": 1})[0] == 404
    assert call("/api/queue/offer-workers", {"queue": "OQ", "workers": 99})[0] == 400


def test_ui_uses_server_link_and_skill_instructions():
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "String(q.config.session_id || '') === sessionId" in app
    assert "promptModal('Name the queue for this session', suggested)" in app
    assert "--accept" in app and "--after <REF>" in app
    assert "/api/queue/offer-workers" in app
    assert "_uxqMaybeOfferWorkers(queues)" in app
    # The link is no longer written to localStorage.
    assert "_uxqRememberSessionCreatedQueue" not in app
