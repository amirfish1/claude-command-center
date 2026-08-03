"""Privacy and transport contracts for reviewed queue diagnostics."""
import importlib
import json
import pathlib
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TOP_KEYS = {"schema_version", "generated_at", "request_id", "ccc", "watchtower", "platform", "queue", "ticket_counts", "workers", "events"}
QUEUE_KEYS = {"auto_drain", "desired_workers", "claim_types", "backend"}
WORKER_KEYS = {"id", "engine", "idle_seconds", "idle_source", "lifecycle_decision", "released", "matched_ticket"}
EVENT_KEYS = {"verb", "seconds_ago"}


@pytest.fixture
def server():
    return importlib.import_module("server")


def test_snapshot_has_closed_schema_and_drops_every_poison_value(server, monkeypatch):
    poison = "DO-NOT-LEAK-9f3c"
    monkeypatch.setattr(server, "_wt_read_config", lambda: {"FEEDBACK": {
        "auto_drain": True, "desired_workers": 3, "claim_types": ["bug"],
        "github_repo": poison, "repo_path": "/Users/person/" + poison,
    }})
    monkeypatch.setattr(server, "_wt_read_workers", lambda: [{
        "worker_id": "feedback-full-secret-uuid", "queue": "FEEDBACK", "engine": "codex",
        "idle_seconds": 3700, "idle_source": "codex_rollout", "session_id": poison,
        "log": "/tmp/" + poison, "prompt": poison,
    }])
    monkeypatch.setattr(server._q, "list_items", lambda: [{
        "ref": "FEEDBACK-7", "project": "FEEDBACK", "status": "in_progress",
        "type": "bug", "claimed_by": "feedback-full-secret-uuid",
        "title": poison, "body": poison, "comments": [poison],
    }])
    monkeypatch.setattr(server, "_wt_diagnostic_events", lambda *_args, **_kwargs: [
        {"verb": "RELEASE", "seconds_ago": 61}
    ])

    snapshot = server._build_queue_diagnostic_snapshot("feedback", now=1785758400)
    encoded = json.dumps(snapshot, sort_keys=True)

    assert set(snapshot) == TOP_KEYS
    assert set(snapshot["queue"]) == QUEUE_KEYS
    assert all(set(row) == WORKER_KEYS for row in snapshot["workers"])
    assert all(set(row) == EVENT_KEYS for row in snapshot["events"])
    assert snapshot["workers"][0]["id"].startswith("w-")
    assert "feedback-full-secret-uuid" not in encoded
    assert poison not in encoded
    assert "/Users/person" not in encoded


def test_renderer_is_human_readable_and_bounded(server, monkeypatch):
    snapshot = {
        "schema_version": 1, "generated_at": "2026-08-03T12:00:00Z",
        "request_id": "123e4567-e89b-42d3-a456-426614174000",
        "ccc": {"version": "5.19.0", "build": "abc1234"},
        "watchtower": {"version": "0.5.0", "service": "running"},
        "platform": {"os": "darwin", "engine": "mixed"},
        "queue": {"auto_drain": True, "desired_workers": 3, "claim_types": ["bug"], "backend": "github"},
        "ticket_counts": {"open": 2, "in_progress": 1, "closed": 4, "claimable": 0, "needs_input": 0, "run_requested": 0},
        "workers": [{"id": "w-a1b2c3d4", "engine": "codex", "idle_seconds": 3700, "idle_source": "codex_rollout", "lifecycle_decision": "release overdue", "released": False, "matched_ticket": False}],
        "events": [{"verb": "RELEASE", "seconds_ago": 61}],
    }
    text = server._render_queue_diagnostic_text(snapshot)
    assert text.startswith("CCC private queue diagnostics\n")
    assert "Queue policy\n" in text
    assert "w-a1b2c3d4" in text
    assert "Raw log" not in text
    assert len(text) <= 48_000


def test_invalid_queue_is_rejected(server):
    with pytest.raises(ValueError, match="queue"):
        server._build_queue_diagnostic_snapshot("../../private")


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def read(self, _limit):
        return self.payload


def test_private_relay_sends_only_closed_envelope(server):
    payload = {
        "schema_version": 1,
        "request_id": "123e4567-e89b-42d3-a456-426614174000",
        "ccc_version": "5.19.0",
        "report_text": "visible and edited",
    }
    with mock.patch.object(
        server.urllib.request, "urlopen",
        return_value=_Response({"ok": True, "report_id": "RPT-123E4567"}),
    ) as opened:
        result = server._send_private_diagnostic(
            payload, endpoint="https://support.example.invalid/v1/report"
        )
    request = opened.call_args.args[0]
    assert json.loads(request.data) == payload
    assert result == {"ok": True, "report_id": "RPT-123E4567"}


@pytest.mark.parametrize("change", [
    {"session_id": "hidden"},
    {"request_id": "not-a-uuid"},
    {"report_text": ""},
    {"report_text": "x" * 48_001},
])
def test_private_relay_rejects_invalid_or_extra_fields(server, change):
    payload = {
        "schema_version": 1,
        "request_id": "123e4567-e89b-42d3-a456-426614174000",
        "ccc_version": "5.19.0",
        "report_text": "visible",
    }
    payload.update(change)
    with mock.patch.object(server.urllib.request, "urlopen") as opened:
        result = server._send_private_diagnostic(payload)
    assert result["ok"] is False
    opened.assert_not_called()
