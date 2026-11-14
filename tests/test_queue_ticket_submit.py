"""Lost-reply recovery uses the real isolated WatchTower store."""
import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import server
from ccc_server import queue_submit


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "queue-config.json").write_text("{}")
    monkeypatch.setenv("WATCHTOWER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("WATCHTOWER_ACTIVITY_LOG", str(tmp_path / "activity.log"))
    monkeypatch.setenv("WATCHTOWER_HOME", str(tmp_path / "wt"))
    monkeypatch.setenv("WATCHTOWER_STORE", str(tmp_path / "queue.json"))
    monkeypatch.setattr(server._q, "_resolve_store_path", lambda: tmp_path / "queue.json")
    monkeypatch.setattr(server._q, "_resolve_activity_log_path", lambda: tmp_path / "activity.log")
    return server._q, tmp_path / "receipts.sqlite3"


def test_saved_ticket_survives_crash_before_receipt_and_process_reload(isolated):
    queue, receipts = isolated
    payload = dict(note="Fix the test button", project="DEMO", idempotency_key="test-submission-key-1")
    # Terminate a separate process after the queue write commits, before the
    # receipt is written. A finally/exception handler cannot rescue this case.
    child = subprocess.run([sys.executable, "-c", """
import json, os, sys
from pathlib import Path
from watchtower import queue
from ccc_server import queue_submit
real_enqueue = queue.enqueue
def die_after_save(**kwargs):
    real_enqueue(**kwargs)
    os._exit(17)
queue.enqueue = die_after_save
queue_submit.enqueue(queue, json.loads(sys.argv[1]), Path(sys.argv[2]))
""", json.dumps(payload), str(receipts)], capture_output=True, timeout=15)
    assert child.returncode == 17, child.stderr.decode()
    recovered = queue_submit.enqueue(queue, payload, receipts)
    assert recovered["ref"] == "DEMO-1"
    assert len(queue.list_items(project="DEMO")) == 1
    assert queue_submit.enqueue(queue, payload, receipts)["ref"] == "DEMO-1"
    with pytest.raises(ValueError, match="different ticket"):
        queue_submit.enqueue(queue, dict(payload, note="Different"), receipts)


def test_concurrent_retries_create_one_ticket(isolated):
    queue, receipts = isolated
    payload = dict(note="One ticket", project="DEMO", idempotency_key="test-submission-key-2")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: queue_submit.enqueue(queue, payload, receipts), range(4)))
    assert {r["ref"] for r in results} == {"DEMO-1"}
    assert len(queue.list_items(project="DEMO")) == 1


def test_existing_unkeyed_clients_and_invalid_keys(isolated):
    queue, receipts = isolated
    for invalid in ("", "short", 12, "bad key with spaces"):
        with pytest.raises(ValueError, match="idempotency_key"):
            queue_submit.enqueue(queue, dict(note="One ticket", project="DEMO",
                                            idempotency_key=invalid), receipts)
    assert queue.list_items(project="DEMO") == []
    assert queue_submit.enqueue(queue, dict(note="One ticket", project="DEMO"), receipts)["ref"] == "DEMO-1"


def test_remote_pending_receipt_never_repeats_unknown_write(isolated, monkeypatch):
    queue, receipts = isolated
    # Exercise the guard using a durable pending receipt; no remote service calls.
    payload = dict(note="One ticket", project="DEMO", idempotency_key="test-submission-key-3")
    queue_submit.enqueue(queue, payload, receipts)
    with sqlite3.connect(receipts) as db:
        db.execute("UPDATE submissions SET item = NULL")
    monkeypatch.setattr(queue, "_github_backend_for_project", lambda _: object())
    with pytest.raises(queue_submit.PendingSubmission):
        queue_submit.enqueue(queue, payload, receipts)


def test_http_confirmation_does_not_wait_for_dispatch(isolated, monkeypatch):
    import urllib.error
    import urllib.request
    queue, receipts = isolated
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", receipts.parent)
    began, release = threading.Event(), threading.Event()

    class Workers:
        def dispatch_after_enqueue(self, *_):
            began.set()
            release.wait(5)

    monkeypatch.setattr(server, "_WT_WORKERS_AVAILABLE", True)
    monkeypatch.setattr(server, "_wt_workers", Workers())
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        payload = dict(note="One ticket", project="DEMO", idempotency_key="test-submission-key-4")
        statuses = []
        def post(data):
            req = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_address[1]}/api/ux-fixes/enqueue",
                data=json.dumps(data).encode(), headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=3) as response:
                statuses.append(response.status)
                return json.load(response)
        assert post(payload)["item"]["ref"] == "DEMO-1"
        assert began.wait(1) and not release.is_set()
        assert post(payload)["item"]["ref"] == "DEMO-1"
        assert len(queue.list_items(project="DEMO")) == 1
        with pytest.raises(urllib.error.HTTPError) as mismatch:
            post(dict(payload, note="Changed ticket"))
        assert mismatch.value.code == 400
        assert "different ticket" in json.load(mismatch.value)["error"]
        with sqlite3.connect(receipts.parent / "ticket-submissions.sqlite3") as db:
            db.execute("UPDATE submissions SET item = NULL")
        monkeypatch.setattr(queue, "_github_backend_for_project", lambda _: object())
        assert post(payload)["pending"] is True
        assert statuses == [200, 200, 202]
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
