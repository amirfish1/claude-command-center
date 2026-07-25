"""Kimi (Moonshot "Kimi Code" CLI) usage + wire.jsonl throughput extraction."""

import json
from pathlib import Path

import pytest

import server


NOW = 1_784_092_800  # 2026-07-15T05:20:00Z


def _usages_payload():
    return {
        "user": {"membership": {"level": "LEVEL_ADVANCED"}},
        "usage": {
            "limit": "100",
            "used": "15",
            "remaining": "85",
            "resetTime": "2026-07-28T13:52:21.141644Z",
        },
        "limits": [
            {
                "window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                "detail": {
                    "limit": "100",
                    "remaining": "100",
                    "resetTime": "2026-07-21T05:00:00Z",
                },
            },
            {
                "window": {"duration": 1, "timeUnit": "TIME_UNIT_DAY"},
                "detail": {
                    "limit": "500",
                    "remaining": "400",
                    "resetTime": "2026-07-22T00:00:00Z",
                },
            },
        ],
    }


def test_kimi_usage_from_response_parses_full_payload():
    usage = server._kimi_usage_from_response(_usages_payload(), now_epoch=NOW)

    assert usage["weekly"]["pct"] == 15.0
    assert usage["weekly"]["window_minutes"] == 10080
    assert usage["weekly"]["resets_at"] == "2026-07-28T13:52:21.141644Z"
    # The exact 300-minute entry wins over the 1-day one.
    assert usage["session"]["pct"] == 0.0
    assert usage["session"]["window_minutes"] == 300
    assert usage["plan_type"] == "Advanced"
    assert usage["from_cache"] is False


def test_kimi_usage_derives_used_from_remaining():
    payload = _usages_payload()
    payload["usage"] = {
        "limit": "200",
        "remaining": "150",
        "resetTime": "2026-07-28T00:00:00Z",
    }

    usage = server._kimi_usage_from_response(payload, now_epoch=NOW)

    assert usage["weekly"]["pct"] == 25.0


def test_kimi_session_window_converts_hour_units_and_falls_back_to_smallest():
    payload = _usages_payload()
    payload["limits"] = [
        {
            "window": {"duration": 5, "timeUnit": "TIME_UNIT_HOUR"},
            "detail": {
                "limit": "50",
                "used": "10",
                "resetTime": "2026-07-21T05:00:00Z",
            },
        },
    ]
    usage = server._kimi_usage_from_response(payload, now_epoch=NOW)
    assert usage["session"]["window_minutes"] == 300
    assert usage["session"]["pct"] == 20.0

    # No 300-minute entry: the smallest window is the session limit.
    payload["limits"] = [
        {
            "window": {"duration": 2, "timeUnit": "TIME_UNIT_DAY"},
            "detail": {
                "limit": "10",
                "used": "5",
                "resetTime": "2026-07-23T00:00:00Z",
            },
        },
        {
            "window": {"duration": 60, "timeUnit": "TIME_UNIT_MINUTE"},
            "detail": {
                "limit": "10",
                "used": "1",
                "resetTime": "2026-07-21T01:00:00Z",
            },
        },
    ]
    usage = server._kimi_usage_from_response(payload, now_epoch=NOW)
    assert usage["session"]["window_minutes"] == 60
    assert usage["session"]["pct"] == 10.0


def test_kimi_usage_from_response_returns_none_without_weekly():
    assert server._kimi_usage_from_response({}, now_epoch=NOW) is None
    assert server._kimi_usage_from_response({"usage": {"used": "1"}}, now_epoch=NOW) is None


def test_read_kimi_usage_falls_back_to_last_snapshot_on_fetch_failure(
    tmp_path, monkeypatch
):
    creds = tmp_path / "kimi-code.json"
    creds.write_text(json.dumps({"access_token": "ccc-test-fake-token"}))
    monkeypatch.setattr(server, "_KIMI_CREDENTIALS_FILE", creds)
    monkeypatch.setattr(
        server, "_kimi_fetch_usages",
        lambda token: (_ for _ in ()).throw(OSError("offline")),
    )
    snapshots = tmp_path / "usage-snapshots.jsonl"
    persisted = server._kimi_usage_from_response(_usages_payload(), now_epoch=NOW)
    snapshots.write_text(json.dumps({
        "ts": server._usage_snapshot_iso(NOW),
        "source": "native",
        "kimi": persisted,
    }) + "\n")
    monkeypatch.setattr(server, "_USAGE_SNAPSHOTS_FILE", snapshots)

    usage = server._read_kimi_usage(now_epoch=NOW)

    assert usage["weekly"]["pct"] == 15.0
    assert usage["plan_type"] == "Advanced"
    assert usage["from_cache"] is True


def test_read_kimi_usage_returns_none_without_live_or_cached_data(
    tmp_path, monkeypatch
):
    creds = tmp_path / "kimi-code.json"
    creds.write_text(json.dumps({"access_token": "ccc-test-fake-token"}))
    monkeypatch.setattr(server, "_KIMI_CREDENTIALS_FILE", creds)
    monkeypatch.setattr(
        server, "_kimi_fetch_usages",
        lambda token: (_ for _ in ()).throw(OSError("offline")),
    )
    monkeypatch.setattr(
        server, "_USAGE_SNAPSHOTS_FILE", tmp_path / "missing-snapshots.jsonl"
    )

    assert server._read_kimi_usage(now_epoch=NOW) is None


def test_throughput_engine_filter_passes_kimi_through():
    assert server._throughput_engine_filter("kimi") == "kimi"
    assert server._throughput_engine_filter("Kimi") == "kimi"
    assert server._throughput_engine_filter("codex") == "codex"
    assert server._throughput_engine_filter("combined") == "claude"
    assert server._throughput_engine_filter(None) == "claude"


def _wire_session(tmp_path):
    session_dir = (
        tmp_path / "wd_some-repo_ab12cd34ef56" / "session_11111111-2222-3333-4444-555555555555"
    )
    main = session_dir / "agents" / "main"
    main.mkdir(parents=True)
    (main / "wire.jsonl").write_text("\n".join([
        json.dumps({"type": "metadata", "protocol_version": "1.4", "created_at": 1784092700000}),
        json.dumps({"type": "turn.prompt", "input": [{"type": "text", "text": "hi"}], "time": 1784092800000}),
        json.dumps({"type": "llm.request", "model": "k3", "modelAlias": "kimi-code/k3", "time": 1784092800500}),
        json.dumps({
            "type": "usage.record",
            "model": "kimi-code/k3",
            "usage": {"inputOther": 100, "output": 40, "inputCacheRead": 900, "inputCacheCreation": 0},
            "usageScope": "turn",
            "time": 1784092810000,
        }),
        json.dumps({
            "type": "usage.record",
            "model": "kimi-code/k3",
            "usage": {"inputOther": 50, "output": 20, "inputCacheRead": 800, "inputCacheCreation": 100},
            "usageScope": "turn",
            "time": 1784092830000,
        }),
        # Session-scope records restate totals — must not be counted.
        json.dumps({
            "type": "usage.record",
            "model": "kimi-code/k3",
            "usage": {"inputOther": 9999, "output": 9999, "inputCacheRead": 9999, "inputCacheCreation": 0},
            "usageScope": "session",
            "time": 1784092840000,
        }),
    ]) + "\n", encoding="utf-8")
    agent = session_dir / "agents" / "agent-1"
    agent.mkdir(parents=True)
    (agent / "wire.jsonl").write_text("\n".join([
        json.dumps({
            "type": "usage.record",
            "model": "kimi-code/k3",
            "usage": {"inputOther": 10, "output": 5, "inputCacheRead": 90, "inputCacheCreation": 0},
            "usageScope": "turn",
            "time": 1784092820000,
        }),
    ]) + "\n", encoding="utf-8")
    return session_dir


def test_kimi_wire_turns_from_file_extracts_token_deltas(tmp_path, monkeypatch):
    session_dir = _wire_session(tmp_path)
    monkeypatch.setattr(server, "KIMI_SESSIONS_ROOT", tmp_path)
    sid = session_dir.name[len("session_"):]

    assert server._kimi_session_dir(sid) is not None

    turns = server._throughput_kimi_turns_from_file(sid)

    # Two main-agent records plus one sub-agent record; session-scope skipped.
    assert len(turns) == 3
    assert all(t["engine"] == "kimi" for t in turns)
    assert all(t["model"] == "kimi-code/k3" for t in turns)
    first = turns[0]
    assert first["t_start"] == "2026-07-15T05:20:00.000Z"
    assert first["t_end"] == "2026-07-15T05:20:10.000Z"
    # raw context = inputOther + cacheRead + cacheCreation
    assert first["raw_context_tokens"] == 1000
    assert first["tokens_out"] == 40
    assert first["fresh_input_tokens"] == 100
    assert first["cache_read_tokens"] == 900
    # Turns from all agent wire files share the session id.
    assert {t["session_id"] for t in turns} == {sid}


def test_kimi_wire_turns_respect_cutoff(tmp_path, monkeypatch):
    session_dir = _wire_session(tmp_path)
    monkeypatch.setattr(server, "KIMI_SESSIONS_ROOT", tmp_path)
    sid = session_dir.name[len("session_"):]

    turns = server._throughput_kimi_turns_from_file(sid, cutoff_epoch=1784092800000 / 1000 + 25)

    assert len(turns) == 1
    assert turns[0]["raw_context_tokens"] == 950


def test_kimi_recent_conversations_scans_session_dirs(tmp_path, monkeypatch):
    session_dir = _wire_session(tmp_path)
    monkeypatch.setattr(server, "KIMI_SESSIONS_ROOT", tmp_path)
    sid = session_dir.name[len("session_"):]

    rows = server._throughput_recent_conversations("kimi", None)

    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == sid
    assert row["engine"] == "kimi"
    assert row["jsonl_path"].endswith("agents/main/wire.jsonl")
    assert row["folder_path"] == "some-repo"
