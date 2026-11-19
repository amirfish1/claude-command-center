"""Sidebar gap filling must not inspect replay logs for known sessions."""

import json

import pytest

import server
from ccc_server import devin


@pytest.mark.parametrize("count", [1, 200])
def test_existing_sessions_skip_tail_io_but_still_read_db_snapshot(monkeypatch, count):
    rows = [{"session_id": f"devincli-known-{n}"} for n in range(count)]
    reads = []

    def snapshot(*args, **kwargs):
        reads.append(kwargs)
        return rows

    def forbidden(*args):
        raise AssertionError("known session replay was inspected")

    monkeypatch.setattr(devin, "_find_devin_cli_db_conversations", snapshot)
    monkeypatch.setattr(devin, "_devin_acp_row_tail", forbidden)
    assert devin.find_devin_cli_conversations(
        stale_ok=True, exclude_session_ids={row["session_id"] for row in rows},
    ) == []
    # Discovery still owns signature checks and background cache refreshes.
    assert reads == [{"stale_ok": True}]


def test_overlay_checks_current_missing_tail_before_applying_recency(monkeypatch, tmp_path):
    db = tmp_path / "sessions.db"
    db.touch()
    rows = [
        {"session_id": "devincli-known", "modified": 1},
        {"session_id": "devincli-missing", "modified": 1},
    ]
    replay = tmp_path / "missing.jsonl"
    replay.write_text(json.dumps({"type": "assistant", "ts": 2000,
                                  "text": "Current answer"}) + "\n")
    inspected = []

    def transcript(harness, sid):
        assert harness == "devin"
        assert sid == "missing"
        inspected.append(sid)
        return replay

    monkeypatch.setattr(server, "_devin_cli_db_paths", lambda: [db])
    monkeypatch.setattr(devin, "_find_devin_cli_db_conversations", lambda **kw: rows)
    monkeypatch.setattr(devin, "_DEVIN_ACP_ROW_TAIL_CACHE", {})
    monkeypatch.setattr(server, "_acp_transcript_path", transcript)
    out = server._archive_overlay_devin_cli_sessions(
        [{"session_id": "devincli-known"}], now=2001,
    )
    assert inspected == ["missing"]
    assert len(out) == 1
    assert out[0]["session_id"] == "devincli-missing"
    assert out[0]["modified"] == 2000
    assert out[0]["last_assistant_text"] == "Current answer"
    assert rows[1]["modified"] == 1

    # The same missing row must see a later request despite a warm DB cache.
    with replay.open("a") as handle:
        handle.write(json.dumps({"type": "user_text", "ts": 2002}) + "\n")
    out = server._archive_overlay_devin_cli_sessions(
        [], now=2003, existing_ids={"devincli-known"},
    )
    assert out[0]["modified"] == 2002
    assert out[0]["last_assistant_text"] == ""
    assert out[0]["session_state"] is None


@pytest.mark.parametrize("excluded", [None, set()])
def test_default_discovery_keeps_all_current_tails(monkeypatch, excluded):
    rows = [{"session_id": "devincli-one", "modified": 1},
            {"session_id": "devincli-two", "modified": 1}]
    inspected = []
    monkeypatch.setattr(devin, "_find_devin_cli_db_conversations", lambda: rows)

    def tail(sid):
        inspected.append(sid)
        return {"ts": 2, "text": sid, "session_state": None}

    monkeypatch.setattr(devin, "_devin_acp_row_tail", tail)
    out = devin.find_devin_cli_conversations(exclude_session_ids=excluded)
    assert inspected == ["one", "two"]
    assert [row["last_assistant_text"] for row in out] == inspected
    assert all(row["modified"] == 1 for row in rows)
