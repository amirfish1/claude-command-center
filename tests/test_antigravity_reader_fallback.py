"""Reader coverage for CLI failures after a transcript is seeded."""
import json

import pytest

import server
from ccc_server import engines


@pytest.fixture
def reader_files(tmp_path, monkeypatch):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({
        "type": "USER_INPUT", "source": "USER_EXPLICIT",
        "content": "Continue the previous task with its full context.",
    }) + "\n")
    stdout = tmp_path / "spawn-antigravity-example.log"
    debug = tmp_path / "spawn-antigravity-example.log.agy.log"
    debug.write_text("")
    db = tmp_path / "conversation.db"
    monkeypatch.setattr(server, "_antigravity_transcript_path", lambda sid: transcript)
    monkeypatch.setattr(engines, "_antigravity_step_usage_map", lambda sid: {})
    monkeypatch.setattr(engines, "_antigravity_db_live_step_events", lambda *a, **kw: [])
    monkeypatch.setattr(engines, "_antigravity_cli_log_meta_for_session",
                        lambda sid: {"log_path": str(debug)})
    monkeypatch.setattr(engines, "_antigravity_db_path", lambda sid: db)
    return transcript, stdout, debug, db


def test_prompt_only_transcript_shows_cli_failure_and_incremental_cursor(reader_files):
    transcript, stdout, debug, db = reader_files
    stdout.write_text("Error: Individual quota reached.")
    parsed = engines._parse_antigravity_conversation("example")
    users = [ev for ev in parsed["events"] if ev["type"] == "user_text"]
    assert [ev["text"] for ev in users] == [
        "Continue the previous task with its full context."]
    assert any("Individual quota reached" in block.get("text", "")
               for ev in parsed["events"] for block in ev.get("blocks", []))
    tail = engines._parse_antigravity_conversation("example", after_line=1)
    assert tail["events"] == [ev for ev in parsed["events"] if ev["line"] > 1]
    assert engines._parse_antigravity_conversation(
        "example", after_line=parsed["last_line"]) == {
            "events": [], "last_line": parsed["last_line"]}


def test_completed_transcript_does_not_append_old_cli_output(reader_files):
    transcript, stdout, debug, db = reader_files
    with transcript.open("a") as f:
        f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "Completed."}) + "\n")
    stdout.write_text("Old CLI output")
    parsed = engines._parse_antigravity_conversation("example", after_line=1)
    assert len(parsed["events"]) == 1
    assert parsed["events"][0]["blocks"] == [{"kind": "text", "text": "Completed."}]
    assert parsed["last_line"] == 2


@pytest.mark.parametrize("source", ["stdout", "debug", "db", "wal"])
def test_cache_key_tracks_sources_even_with_cached_transcript(reader_files, monkeypatch, source):
    transcript, stdout, debug, db = reader_files
    monkeypatch.setattr(server, "_detect_session_engine", lambda sid: "antigravity")
    monkeypatch.setitem(server._CONV_PATH_CACHE, "example", str(transcript))
    before = server._conv_parse_jsonl_mtime("example")
    target = {"stdout": stdout, "debug": debug, "db": db,
              "wal": db.with_name(db.name + "-wal")}[source]
    target.write_text("New CLI activity")
    assert server._conv_parse_jsonl_mtime("example") != before
