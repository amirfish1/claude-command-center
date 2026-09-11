"""Full activity names survive storage without changing legacy records."""

import importlib
import json
import threading
import urllib.request

import pytest


@pytest.fixture
def activity_log(tmp_path, monkeypatch):
    server = importlib.import_module("server")
    log_file = tmp_path / "activity.log"
    monkeypatch.setattr(server, "ACTIVITY_LOG_FILE", log_file)
    monkeypatch.setattr(server, "_ACTIVITY_LOG_DIR_READY", False)
    return server, log_file


@pytest.mark.parametrize("category,verb,detail", [
    ("peer", "CCC-PEER-FAIL", "session=abc123 error=connection  refused"),
    ("peer", "CCC-PEER-START", "session=abc123 socket=peer.sock"),
    ("codex-app-server", "SHARED_STATE_BLOCK", 'session=abc123 reason="a  b"'),
    ("spawn", "SPAWN", "session=abc123 first\nsecond\rthird\u2028last"),
    (" category ", " verb ", "session=abc123  detail "),
    ("12345678901234", "123456789", "session=abc123 exact widths"),
])
def test_round_trip_full_fields(activity_log, category, verb, detail):
    server, log_file = activity_log
    server._log_activity(category, verb, detail)
    assert len(log_file.read_text().splitlines()) == 1
    events = server._read_activity_log(session_id="abc123")
    assert len(events) == 1
    assert events[0] == {
        "ts": events[0]["ts"], "category": category,
        "verb": verb, "detail": detail,
    }


def test_short_rows_keep_legacy_layout(activity_log):
    server, log_file = activity_log
    server._log_activity("spawn", "SPAWN", "a  b")
    line = log_file.read_text()
    assert line[23:] == "  spawn           SPAWN    a  b\n"


def test_mixed_old_new_rows_keep_order_and_historical_truncation(activity_log):
    server, log_file = activity_log
    old = f"2026-09-01 12:00:00 UTC  {'peer':<14}  {'CCC-PEER-':<9}old  detail\n"
    log_file.write_text(old)
    server._log_activity("peer", "CCC-PEER-FAIL", "new failure")
    server._log_activity("peer", "CCC-PEER-START", "new start")
    events = server._read_activity_log()
    assert [event["verb"] for event in events] == [
        "CCC-PEER-", "CCC-PEER-FAIL", "CCC-PEER-START",
    ]
    assert events[0]["detail"] == "old  detail"
    assert log_file.read_text().startswith(old)
    assert server._read_activity_log(limit=2) == events[-2:]


@pytest.mark.parametrize("payload", [
    "{broken", "null", "[]", '"text"', "{}",
    json.dumps({"category": "peer", "verb": 5, "detail": "bad"}),
    json.dumps({"category": "peer", "verb": "FAIL"}),
])
def test_malformed_versioned_records_are_skipped(activity_log, payload):
    server, log_file = activity_log
    log_file.write_text("2026-09-01 12:00:00 UTC  @ccc-activity-v2 " + payload + "\n")
    server._log_activity("peer", "CCC-PEER-START", "valid")
    events = server._read_activity_log()
    assert len(events) == 1
    assert events[0]["verb"] == "CCC-PEER-START"


@pytest.mark.parametrize("line", [
    "garbage" * 12,
    "2026-09-01 12:00:00 UTC  peer            ",
])
def test_malformed_legacy_records_are_skipped(activity_log, line):
    server, _ = activity_log
    assert server._parse_activity_log_line(line) is None


def test_endpoint_returns_full_names_with_unchanged_response_shape(activity_log):
    server, _ = activity_log
    server._log_activity("peer", "CCC-PEER-FAIL", "session=abc123 error=refused")
    server._log_activity("peer", "CCC-PEER-START", "session=other socket=peer.sock")
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_port}/api/activity-log?session_id=abc123"
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read())
        assert data["ok"] is True
        assert len(data["events"]) == 1
        assert set(data["events"][0]) == {"ts", "category", "verb", "detail"}
        assert data["events"][0]["verb"] == "CCC-PEER-FAIL"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
