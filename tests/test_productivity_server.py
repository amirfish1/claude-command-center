import inspect
from datetime import date, datetime, timezone

import server


UTC = timezone.utc


class _FakeStore:
    def __init__(self, cached=None):
        self.cached = cached
        self.saved = None

    def load_payload(self):
        return self.cached

    def save_payload(self, payload, generated_at=None):
        self.saved = {"payload": payload, "generated_at": generated_at}

    def load_presence(self, start_date, end_date, tzinfo=None):
        return []


def test_productivity_repo_discovery_deduplicates_remote(monkeypatch, tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(server, "_load_recent_repos", lambda: [str(first)])
    monkeypatch.setattr(server, "_load_custom_repos", lambda: [str(second)])
    monkeypatch.setattr(server, "_wt_read_config", lambda: {})
    monkeypatch.setattr(server.Path, "cwd", lambda: first)
    monkeypatch.setattr(
        server,
        "describe_git_repo",
        lambda path: {
            "path": str(path),
            "id": "same",
            "name": "Project",
            "identity": "example.test/owner/project",
        },
    )
    repos, warnings = server._productivity_known_repos([])
    assert len(repos) == 1
    assert repos[0]["id"] == "same"
    assert warnings == []


def test_productivity_payload_selects_cached_range(monkeypatch):
    cached = {
        "generated_at": 100.0,
        "payload": {
            "schema": 1,
            "datasets": {
                "8": {"ok": True, "range": {"weeks": 8}, "summary": {"features": 2}}
            },
            "coverage": {"repositories": 3},
        },
    }
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore(cached))
    monkeypatch.setattr(server, "_productivity_refresh_start", lambda: {"state": "idle"})
    payload, status = server._productivity_payload(weeks=8)
    assert status == 200
    assert payload["summary"]["features"] == 2
    assert payload["coverage"]["repositories"] == 3
    assert payload["refresh"]["cached"] is True
    assert payload["refresh"]["generated_at"] == 100.0


def test_productivity_payload_starts_first_build(monkeypatch):
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore())
    monkeypatch.setattr(
        server,
        "_productivity_refresh_start",
        lambda: {"state": "building", "started_at": 50.0},
    )
    payload, status = server._productivity_payload(weeks=12)
    assert status == 202
    assert payload["ok"] is True
    assert payload["state"] == "building"
    assert payload["range"]["weeks"] == 12


def test_ticket_normalization_keeps_safe_project_evidence():
    normalized = server._productivity_normalize_ticket(
        {
            "ref": "PRODUCTIVITY-7",
            "project": "PRODUCTIVITY",
            "type": "feature",
            "status": "closed",
            "title": "Add project trends",
            "created_at": "2026-07-14T07:00:00Z",
            "closed_at": "2026-07-14T09:00:00Z",
        },
        {"PRODUCTIVITY": {"id": "repo-a", "name": "Repo A"}},
    )
    assert normalized == {
        "ref": "PRODUCTIVITY-7",
        "project_id": "repo-a",
        "project_name": "Repo A",
        "kind": "feature",
        "status": "closed",
        "title": "Add project trends",
        "created_at": "2026-07-14T07:00:00Z",
        "closed_at": "2026-07-14T09:00:00Z",
    }


def test_productivity_turn_rows_deduplicate_replayed_messages(monkeypatch, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("{}\n")
    duplicate = {
        "message_id": "msg_same",
        "request_id": "req_same",
        "engine": "claude",
        "model": "claude-test",
        "trigger_type": "user_text",
        "t_start": "2026-07-14T08:00:00Z",
        "t_end": "2026-07-14T08:01:00Z",
        "dur_sec": 60,
        "tokens_in": 100,
        "tokens_out": 20,
    }
    monkeypatch.setattr(
        server,
        "_throughput_file_turns",
        lambda path, extract: [dict(duplicate), dict(duplicate)],
    )
    rows, coverage = server._productivity_turn_rows(
        [
            {
                "session_id": "session-a",
                "engine": "claude",
                "folder_path": str(tmp_path),
                "jsonl_path": str(transcript),
                "modified": datetime(2026, 7, 14, 9, tzinfo=UTC).timestamp(),
            }
        ],
        [
            {
                "id": "repo-a",
                "name": "Repo A",
                "path": str(tmp_path),
                "paths": [str(tmp_path)],
            }
        ],
        datetime(2026, 7, 14, tzinfo=UTC).timestamp(),
    )
    assert len(rows) == 1
    assert rows[0]["tokens"] == 120
    assert rows[0]["human_trigger"] is True
    assert coverage["transcripts_parsed"] == 1


def test_build_snapshot_reads_each_global_source_once(monkeypatch):
    calls = {"conversations": 0, "tickets": 0}

    def conversations(**kwargs):
        calls["conversations"] += 1
        return []

    def tickets():
        calls["tickets"] += 1
        return []

    monkeypatch.setattr(server, "find_all_conversations", conversations)
    monkeypatch.setattr(server._q, "list_items", tickets)
    monkeypatch.setattr(server, "_productivity_known_repos", lambda rows: ([], []))
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore())
    snapshot = server._productivity_build_snapshot(
        now=datetime(2026, 7, 14, 12, tzinfo=UTC)
    )
    assert calls == {"conversations": 1, "tickets": 1}
    assert set(snapshot["datasets"]) == {"6", "8", "12", "16"}
    assert snapshot["coverage"]["conversations_considered"] == 0


def test_build_snapshot_caps_repository_warning_details(monkeypatch):
    monkeypatch.setattr(server, "find_all_conversations", lambda **kwargs: [])
    monkeypatch.setattr(server._q, "list_items", lambda: [])
    monkeypatch.setattr(
        server,
        "_productivity_known_repos",
        lambda rows: ([], [f"Unavailable candidate {number}" for number in range(20)]),
    )
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore())
    snapshot = server._productivity_build_snapshot(
        now=datetime(2026, 7, 14, 12, tzinfo=UTC)
    )
    assert snapshot["coverage"]["warning_count"] == 20
    assert len(snapshot["coverage"]["warnings"]) == 12


def test_productivity_route_is_additive_and_range_limited():
    source = inspect.getsource(server.CommandCenterHandler.do_GET)
    assert 'path == "/api/productivity"' in source
    assert "(6, 8, 12, 16)" in source
    assert 'path == "/productivity.html"' in source
    assert 'STATIC_DIR / "productivity.html"' in source
