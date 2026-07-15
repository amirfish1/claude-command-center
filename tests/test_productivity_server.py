import inspect
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

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


def test_project_lookup_does_not_assign_shared_parent_to_first_child(tmp_path):
    workspace = tmp_path / "workspace"
    repo = workspace / "child-repo"
    repo.mkdir(parents=True)
    project = server._productivity_project_for_path(
        workspace,
        [{"id": "child", "name": "Child", "path": str(repo), "paths": [str(repo)]}],
    )
    assert project is None


def test_productivity_payload_selects_cached_range(monkeypatch):
    cached = {
        "generated_at": 100.0,
        "payload": {
            "schema": server._PRODUCTIVITY_SCHEMA,
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


def test_hardened_payload_schema_invalidates_pre_hardening_cache(monkeypatch):
    assert server._PRODUCTIVITY_SCHEMA != 1
    cached = {
        "generated_at": time.time(),
        "payload": {
            "schema": 1,
            "datasets": {"8": {"ok": True, "range": {"weeks": 8}}},
            "coverage": {},
        },
    }
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore(cached))
    monkeypatch.setattr(
        server,
        "_productivity_refresh_start",
        lambda: {"state": "building", "started_at": 50.0},
    )

    payload, status = server._productivity_payload(weeks=8)

    assert status == 202
    assert payload["state"] == "building"


def test_failed_first_build_waits_for_explicit_retry(monkeypatch):
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore())
    monkeypatch.setattr(
        server,
        "_PRODUCTIVITY_REFRESH",
        {
            "state": "failed",
            "started_at": 10.0,
            "completed_at": 11.0,
            "error": "Productivity refresh failed. Retry manually.",
            "error_code": "refresh_failed",
        },
    )
    starts = []
    monkeypatch.setattr(
        server,
        "_productivity_refresh_start",
        lambda force=False: starts.append(force) or dict(server._PRODUCTIVITY_REFRESH),
    )

    payload, status = server._productivity_payload(weeks=8)

    assert status == 503
    assert starts == []
    assert payload["state"] == "failed"
    assert payload["error_code"] == "refresh_failed"


def test_explicit_retry_can_restart_failed_refresh(monkeypatch):
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore())
    monkeypatch.setattr(
        server,
        "_PRODUCTIVITY_REFRESH",
        {"state": "failed", "error": "Productivity refresh failed. Retry manually."},
    )
    starts = []
    monkeypatch.setattr(
        server,
        "_productivity_refresh_start",
        lambda force=False: starts.append(force) or {"state": "building"},
    )

    payload, status = server._productivity_payload(weeks=8, force_refresh=True)

    assert status == 202
    assert payload["state"] == "building"
    assert starts == [True]


def test_refresh_failure_exposes_generic_error_and_logs_detail(monkeypatch, capsys):
    class _ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    private_detail = "/Users/private/work/productivity.db is locked"
    monkeypatch.setattr(
        server,
        "_PRODUCTIVITY_REFRESH",
        {"state": "idle", "started_at": None, "completed_at": None, "error": None},
    )
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        server,
        "_productivity_build_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError(private_detail)),
    )

    refresh = server._productivity_refresh_start()

    assert refresh["state"] == "failed"
    assert refresh["error"] == "Productivity refresh failed. Retry manually."
    assert refresh["error_code"] == "refresh_failed"
    assert private_detail not in str(refresh)
    assert private_detail in capsys.readouterr().err


def test_refresh_preserves_last_good_snapshot_when_watchtower_is_unavailable(monkeypatch):
    class _ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    cached = {
        "generated_at": 100.0,
        "payload": {
            "schema": server._PRODUCTIVITY_SCHEMA,
            "datasets": {"8": {"ok": True, "summary": {"watchtower_closed": 4}}},
            "coverage": {"watchtower": {"available": True, "items": 4, "reason": None}},
        },
    }
    store = _FakeStore(cached)
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", store)
    monkeypatch.setattr(
        server,
        "_PRODUCTIVITY_REFRESH",
        {"state": "idle", "started_at": None, "completed_at": None, "error": None},
    )
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        server,
        "_productivity_build_snapshot",
        lambda: {
            "schema": server._PRODUCTIVITY_SCHEMA,
            "generated_at": 200.0,
            "datasets": {"8": {"ok": True, "summary": {"watchtower_closed": 0}}},
            "coverage": {
                "watchtower": {"available": False, "items": 0, "reason": "read_failed"}
            },
        },
    )

    refresh = server._productivity_refresh_start()

    assert refresh["state"] == "failed"
    assert store.saved is None
    assert store.cached["payload"]["datasets"]["8"]["summary"]["watchtower_closed"] == 4


def test_status_only_payload_omits_large_dataset(monkeypatch):
    assert "status_only" in inspect.signature(server._productivity_payload).parameters
    cached = {
        "generated_at": time.time(),
        "payload": {
            "schema": server._PRODUCTIVITY_SCHEMA,
            "datasets": {
                "8": {
                    "ok": True,
                    "range": {"weeks": 8},
                    "daily": [{"date": "2026-07-14"}],
                    "deliveries": [{"title": "Large evidence row"}],
                }
            },
            "coverage": {"repositories": 3},
        },
    }
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore(cached))
    monkeypatch.setattr(server, "_productivity_refresh_public", lambda: {"state": "building"})

    payload, status = server._productivity_payload(weeks=8, status_only=True)

    assert status == 202
    assert payload["ok"] is True
    assert payload["state"] == "building"
    assert "daily" not in payload
    assert "deliveries" not in payload


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


def test_productivity_turn_rows_prefer_effective_session_cwd(monkeypatch, tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("{}\n")
    monkeypatch.setattr(
        server,
        "_throughput_file_turns",
        lambda path, extract: [
            {
                "message_id": "message-a",
                "trigger_type": "user_text",
                "t_start": "2026-07-14T08:00:00Z",
                "t_end": "2026-07-14T08:01:00Z",
                "dur_sec": 60,
                "tokens_in": 10,
                "tokens_out": 5,
            }
        ],
    )
    rows, _coverage = server._productivity_turn_rows(
        [
            {
                "session_id": "session-a",
                "engine": "claude",
                "session_cwd": str(repo_b),
                "folder_path": str(repo_a),
                "jsonl_path": str(transcript),
                "modified": datetime(2026, 7, 14, 9, tzinfo=UTC).timestamp(),
            }
        ],
        [
            {"id": "repo-a", "name": "Repo A", "path": str(repo_a), "paths": [str(repo_a)]},
            {"id": "repo-b", "name": "Repo B", "path": str(repo_b), "paths": [str(repo_b)]},
        ],
        datetime(2026, 7, 14, tzinfo=UTC).timestamp(),
    )
    assert rows[0]["project_id"] == "repo-b"


def test_snapshot_uses_rule_aware_local_timezone(monkeypatch):
    marker = ZoneInfo("Asia/Jerusalem")
    captured = []
    monkeypatch.setattr(server, "system_local_timezone", lambda: marker, raising=False)
    monkeypatch.setattr(server, "find_all_conversations", lambda **kwargs: [])
    monkeypatch.setattr(server._q, "list_items", lambda: [])
    monkeypatch.setattr(server, "_productivity_known_repos", lambda rows: ([], []))
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore())

    def aggregate(**kwargs):
        captured.append(kwargs["tzinfo"])
        return {"range": {}}

    monkeypatch.setattr(server, "aggregate_productivity", aggregate)
    server._productivity_build_snapshot(
        now=datetime(2026, 7, 15, 12, tzinfo=UTC)
    )
    assert captured == [marker, marker, marker, marker]


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


def test_build_snapshot_marks_watchtower_unavailable_on_read_failure(monkeypatch):
    monkeypatch.setattr(server, "find_all_conversations", lambda **kwargs: [])
    monkeypatch.setattr(
        server._q,
        "list_items",
        lambda: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )
    monkeypatch.setattr(server, "_productivity_known_repos", lambda rows: ([], []))
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore())

    snapshot = server._productivity_build_snapshot(
        now=datetime(2026, 7, 14, 12, tzinfo=UTC)
    )

    assert snapshot["coverage"]["watchtower"] == {
        "available": False,
        "items": 0,
        "reason": "read_failed",
    }


def test_build_snapshot_marks_unsupported_presence_sampler(monkeypatch):
    monkeypatch.setattr(server, "find_all_conversations", lambda **kwargs: [])
    monkeypatch.setattr(server._q, "list_items", lambda: [])
    monkeypatch.setattr(server, "_productivity_known_repos", lambda rows: ([], []))
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", _FakeStore())
    monkeypatch.setattr(server.sys, "platform", "linux")

    snapshot = server._productivity_build_snapshot(
        now=datetime(2026, 7, 14, 12, tzinfo=UTC)
    )

    presence = snapshot["coverage"]["presence"]
    assert presence["sampler_available"] is False
    assert presence["reason"] == "unsupported_platform"
    assert presence["source"] == "unavailable"


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
