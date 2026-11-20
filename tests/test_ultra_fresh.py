"""Message-only, bounded live row feed; no list polling required."""
import json
import sqlite3
from contextlib import closing
from unittest import mock

import pytest

import server
from ccc_server import ultra_fresh
from ccc_server.ultra_fresh import FreshSession, message_text, session_ids


@pytest.fixture(autouse=True)
def _no_transcript_throttle(monkeypatch):
    # Tests drive read() back to back; the production throttle is covered below.
    monkeypatch.setattr(ultra_fresh, "TRANSCRIPT_CHECK_S", 0)


def event(text, line=2, ts="2026-10-23T10:00:02Z", **kw):
    return {"type": "assistant", "line": line, "ts": ts,
            "blocks": [{"kind": "text", "text": text}], **kw}


def test_subscription_is_bounded_and_never_accepts_paths():
    assert session_ids({"sessions": ["a,b,c,d,e,a,../../secret"]}) == ["a", "b", "c", "d"]
    assert session_ids({"sessions": ["../../secret,a,a"]}) == ["a"]


def test_finished_antigravity_choice_is_enriched_only_for_the_current_reply():
    text = "Ready for your call on the change: **1** (port the upgrade) or **2** (skip)?"
    question = event(text, turn_complete=True)
    newer_work = {"type": "assistant", "line": 3,
                  "blocks": [{"kind": "tool_use", "name": "run_command"}]}
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)), \
         mock.patch.object(server, "parse_conversation", side_effect=[
             {"events": [question], "last_line": 2},
             {"events": [question, newer_work], "last_line": 3},
         ]), mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(None, False)), \
         mock.patch.object(server, "_detect_session_engine", return_value="antigravity"):
        current = FreshSession("antigravity-test").read()[0]
        assert current["turn_complete"]
        assert [a["reply"] for a in current["session_state"]["next_actions"]] == ["1", "2"]
        older = FreshSession("antigravity-test").read()[0]
        assert not older.get("turn_complete")
        assert older["session_state"] is None


def test_only_agent_message_text_is_explanation():
    assert message_text(event("Checking the fix.")) == "Checking the fix."
    assert message_text(event("child", parent_tool_use_id="tool-1")) == ""
    for kind in ("thinking", "tool_use", "plan", "tool_result"):
        assert message_text({"type": "assistant", "blocks": [{"kind": kind, "text": "secret tool detail"}]}) == ""
    assert message_text({"type": "result", "text": "tool output"}) == ""


def test_hermes_message_only_read_preserves_lineage_and_cursor(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    with closing(sqlite3.connect(db)) as con, con:
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT);
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                timestamp REAL, tool_calls TEXT, reasoning TEXT
            );
            INSERT INTO sessions VALUES ('parent', NULL), ('child', 'parent');
        """)
        con.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "parent", "user", "Check the output", 1, None, None),
                (2, "parent", "assistant", None, 2, '[{"name":"Read"}]', None),
                (3, "parent", "tool", "retained tool output", 3, None, None),
                (4, "child", "assistant", None, 4, None, "Private reasoning"),
                (5, "child", "assistant", '{"intent":"ready"}', 5, None, None),
            ],
        )
    monkeypatch.setattr(server, "_detect_session_engine", lambda _sid: "hermes")
    monkeypatch.setattr(server, "_hermes_db_for_session", lambda _sid: db)
    monkeypatch.setenv("CCC_HERMES_SHOW_REASONING", "1")

    with mock.patch.object(server, "_conv_parse_cache_put") as cache_put:
        full = server.parse_conversation("child", tail=80)
        sidebar = server.parse_conversation("child", tail=80, messages_only=True)
        cache_put.assert_called_once_with("child", 0, None, full)

    def consumed(result):
        return [(e["line"], e["type"], e["ts"], message_text(e),
                 e.get("text") if e["type"] == "user_text" else None)
                for e in result["events"]]

    assert consumed(sidebar) == consumed(full)
    assert sidebar["last_line"] == full["last_line"]
    assert any(b["kind"] == "tool_use" for e in full["events"] for b in e.get("blocks", []))
    assert all(b["kind"] != "tool_use" for e in sidebar["events"] for b in e.get("blocks", []))
    assert any(e["type"] == "tool_result" and not e["text"] for e in sidebar["events"])
    assert '"intent": "ready"' in message_text(sidebar["events"][-1])


def test_new_message_arrives_from_transcript_without_archive_poll():
    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", side_effect=[(1, 1), (2, 2), (3, 3)]), \
         mock.patch.object(server, "parse_conversation", side_effect=[
             {"events": [event("Old explanation")], "last_line": 2},
             {"events": [event("Old explanation"), event("New explanation", line=3, ts="2026-10-23T10:00:03Z")], "last_line": 3},
             {"events": [{"type": "assistant", "line": 4, "blocks": [{"kind": "tool_use", "name": "Bash", "detail": "noisy command"}]}], "last_line": 4},
         ]) as parse, \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(None, False)), \
         mock.patch.object(server, "_detect_session_engine", return_value="claude"):
        assert reader.read()[-1]["text"] == "Old explanation"
        assert reader.read()[-1]["text"] == "New explanation"
        assert reader.read() == []
        assert reader.last_text == "New explanation"
        assert [call.kwargs for call in parse.call_args_list] == [
            {"tail": 80, "messages_only": True, "message_cursor": cursor}
            for cursor in (0, 2, 3)
        ]


def test_new_user_is_a_barrier_for_old_messages():
    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)), \
         mock.patch.object(server, "parse_conversation", return_value={"events": [
             {"type": "user_text", "line": 3, "ts": "2026-10-23T10:01:00Z", "text": "New request"},
             event("Late previous turn", line=4),
             event("Current reply", line=5, ts="2026-10-23T10:01:01Z")], "last_line": 5}), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(None, False)), \
         mock.patch.object(server, "_detect_session_engine", return_value="claude"):
        updates = reader.read()
        assert updates[0]["user_ts"] == "2026-10-23T10:01:00Z"
        assert [u["text"] for u in updates if "text" in u] == ["Current reply"]


def test_coordination_events_cannot_stall_incremental_explanations():
    reader = FreshSession("test-session")
    old = event("Checking the previous patch.", line=2)
    coord = {"type": "system", "line": "coord-test-session-turn-ended",
             "ts": "2026-10-23T10:00:03Z", "text": "Active turn went quiet"}
    user = {"type": "user_text", "line": 4, "ts": "2026-10-23T10:00:04Z",
            "text": "Review the revised patch."}
    reply = event("I am checking the revised patch.", line=5, ts="2026-10-23T10:00:05Z")
    tool = {"type": "assistant", "line": 6, "ts": "2026-10-23T10:00:06Z",
            "blocks": [{"kind": "tool_use", "name": "Bash"}]}
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", side_effect=[(1, 1), (2, 2), (3, 3)]), \
         mock.patch.object(server, "parse_conversation", side_effect=[
             {"events": [coord, old], "last_line": 2},
             {"events": [coord, old, user, reply], "last_line": 5},
             {"events": [coord, old, user, reply, tool], "last_line": 6},
         ]), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(None, False)), \
         mock.patch.object(server, "_detect_session_engine", return_value="codex"):
        assert reader.read()[-1]["text"] == "Checking the previous patch."
        update = reader.read()[-1]
        assert update["text"] == "I am checking the revised patch."
        assert update["line"] == 5
        assert update["user_ts"] == user["ts"]
        assert reader.read() == []
        assert reader.last_text == reply["blocks"][0]["text"]


def test_spawn_partial_message_uses_existing_normalizer_and_excludes_tools(tmp_path):
    log = tmp_path / "spawn.log"
    log.write_text("")
    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)), \
         mock.patch.object(server, "parse_conversation", return_value={"events": [], "last_line": 0}), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(log, True)), \
         mock.patch.object(server, "_detect_session_engine", return_value="claude"):
        assert reader.read() == []
        rows = [
            {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Checking "}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "tool detail"}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "the fix."}}},
        ]
        log.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        assert reader.read()[-1]["text"] == "Checking the fix."
        assert reader.read() == []


def test_fractional_timestamp_and_timezone_order_are_chronological():
    reader = FreshSession("test-session")
    assert reader._message("first", "2026-10-23T10:00:00Z")
    assert reader._message("new fraction", "2026-10-23T10:00:00.001Z")
    assert reader._message("old timezone", "2026-10-23T10:30:00+01:00") is None


def test_stream_coalesces_messages_and_caps_readers_without_archive_calls():
    from ccc_server.ultra_fresh import stream
    import io

    class Handler:
        wfile = io.BytesIO()
        send_response = lambda *args: None
        send_header = lambda *args: None
        end_headers = lambda *args: None

    handler = Handler()
    original_flush = handler.wfile.flush
    def disconnect():
        original_flush()
        raise BrokenPipeError()
    handler.wfile.flush = disconnect
    with mock.patch.object(FreshSession, "read", autospec=True,
                           side_effect=lambda reader: [{"session_id": reader.sid, "text": "Fresh agent message"}]) as read:
        stream(handler, {"sessions": ["a,b,c,d,e"]})
    assert [call.args[0].sid for call in read.call_args_list] == ["a", "b", "c", "d"]
    assert len(json.loads(handler.wfile.getvalue().decode().split("data: ")[1])["updates"]) == 4


def test_idle_stream_backs_off_and_snaps_back_on_update():
    import io
    from ccc_server.ultra_fresh import stream

    class Handler:
        wfile = io.BytesIO()
        send_response = lambda *args: None
        send_header = lambda *args: None
        end_headers = lambda *args: None

    # 6 quiet ticks, one update, then quiet until the test stops the loop.
    script = iter([[]] * 6 + [[{"session_id": "a", "text": "x"}]] + [[]] * 3)
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(round(seconds, 3))
        if len(sleeps) >= 10:
            raise BrokenPipeError()

    with mock.patch.object(FreshSession, "read", autospec=True,
                           side_effect=lambda reader: next(script)), \
            mock.patch.object(ultra_fresh.time, "sleep", side_effect=fake_sleep):
        stream(Handler(), {"sessions": ["a"]})
    assert sleeps[:6] == [0.15, 0.225, 0.338, 0.5, 0.5, 0.5]
    assert sleeps[6] == ultra_fresh.ACTIVE_TICK_S
    assert sleeps[7] == 0.15


def test_codex_live_snapshot_only_changes_on_new_message_and_never_replays_retired_turn():
    from ccc_server.codex_conversation import CODEX_CONVERSATIONS

    def snapshot(cursor, tid="t1", text="Retained reply", tools=False):
        items = [{"id": "m1", "type": "agentMessage", "text": text}]
        if tools:
            items.append({"id": "cmd", "type": "commandExecution", "command": "noisy command"})
        return {"generation": 1, "cursor": cursor, "thread": {"turns": [
            {"id": tid, "status": "inProgress", "items": items}]}}

    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)), \
         mock.patch.object(server, "parse_conversation", return_value={"events": [], "last_line": 0}), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(None, False)), \
         mock.patch.object(server, "_detect_session_engine", return_value="codex"), \
         mock.patch.object(CODEX_CONVERSATIONS, "snapshot", side_effect=[
             snapshot(1), snapshot(2), snapshot(3, text="Fresh text"),
             snapshot(4, text="Fresh text", tools=True), snapshot(5, tid="t2", text="New turn"),
             snapshot(6, tid="t1", text="Stale turn")]):
        assert reader.read() == []  # Retained baseline.
        assert reader.read() == []  # Another session changes the global cursor.
        assert reader.read()[-1]["text"] == "Fresh text"
        assert reader.read() == []  # Tools cannot replace explanation.
        assert reader.read()[-1]["text"] == "New turn"
        assert reader.read() == []  # Old turn cannot overwrite newer text.


def test_spawn_user_tool_result_cannot_clear_message_explanation(tmp_path):
    log = tmp_path / "spawn.log"
    log.write_text("")
    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)), \
         mock.patch.object(server, "parse_conversation", return_value={"events": [event("Keep the agent explanation")], "last_line": 2}), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(log, True)), \
         mock.patch.object(server, "_detect_session_engine", return_value="claude"):
        assert reader.read()[-1]["text"] == "Keep the agent explanation"
        log.write_text(json.dumps({"type": "user", "timestamp": "2026-10-23T10:01:00Z", "message": {
            "role": "user", "content": [{"type": "tool_result", "content": "tool output", "tool_use_id": "tool-1"}]}}) + "\n")
        assert reader.read() == []
        assert reader.last_text == "Keep the agent explanation"


def test_transcript_source_key_is_throttled_between_loop_ticks(monkeypatch):
    # CCC-152: per-tick source-key resolution (Codex SQLite, Hermes/Devin DB
    # keys) across every open stream starved the GIL and stalled /list.
    monkeypatch.setattr(ultra_fresh, "TRANSCRIPT_CHECK_S", 0.5)
    clock = [100.0]
    monkeypatch.setattr(ultra_fresh.time, "monotonic", lambda: clock[0])
    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)) as key, \
         mock.patch.object(server, "parse_conversation", return_value={"events": [], "last_line": 0}), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(None, False)), \
         mock.patch.object(server, "_detect_session_engine", return_value="claude"):
        for _ in range(5):
            reader.read()
            clock[0] += 0.1
        assert key.call_count == 1
        clock[0] += 0.5
        reader.read()
        assert key.call_count == 2


def test_codex_source_key_reuses_resolved_reader_path(tmp_path, monkeypatch):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("{}\n")
    monkeypatch.setattr(server, "_CODEX_READER_PATH_CACHE", {})
    with mock.patch.object(server, "_detect_session_engine", return_value="codex"), \
         mock.patch.object(server, "_resolve_conversation_reader",
                           return_value=(rollout, None)) as resolve:
        first = server._conv_parse_jsonl_mtime("codex-thread")
        rollout.write_text("{}\n{}\n")
        second = server._conv_parse_jsonl_mtime("codex-thread")
    assert resolve.call_count == 1
    assert first != second


@pytest.mark.parametrize('record', [
    {'isCompactSummary': True, 'message': {'content': 'Saved context'}},
    {'message': {'content': 'This session is being continued from a previous conversation. Summary: old work'}},
    {'message': {'content': '<command-name>/compact</command-name><command-args></command-args>'}},
    {'message': {'content': '<local-command-stdout>Compacted</local-command-stdout>'}},
])
def test_compaction_preserves_explanation_in_archive_and_live_feed(tmp_path, record):
    old = {'type': 'assistant', 'timestamp': '2026-10-23T10:00:02Z',
           'message': {'content': [{'type': 'text', 'text': 'Video delivered; ready to watch.'}]}}
    record = dict(record, type='user', timestamp='2026-10-23T10:01:00Z')
    transcript = tmp_path / 'session.jsonl'
    transcript.write_text('\n'.join(json.dumps(row, separators=(',', ':')) for row in [old, record]) + '\n')
    meta = server._extract_tail_meta(transcript)
    assert meta['last_event_type'] == 'assistant'
    assert meta['last_assistant_text'] == 'Video delivered; ready to watch.'
    # Old persisted user tails migrate once, then the cache stays hot.
    meta['bookkeeping_filtered'] = 0
    meta['last_event_type'] = 'user'
    meta = server._extract_tail_meta(transcript)
    assert meta['last_event_type'] == 'assistant'
    assert server._extract_tail_meta(transcript) is meta
    parsed = [server._parse_conversation_event(row, i) for i, row in enumerate([old, record], 1)]
    parsed = [row for row in parsed if row]
    log = tmp_path / 'spawn.log'
    log.write_text('')
    reader = FreshSession('test-session')
    with mock.patch.object(server, '_conv_parse_jsonl_mtime', return_value=(1, 1)), \
         mock.patch.object(server, 'parse_conversation', return_value={'events': parsed, 'last_line': 2}), \
         mock.patch.object(server, '_resolve_spawn_log_for_session', return_value=(log, True)), \
         mock.patch.object(server, '_detect_session_engine', return_value='claude'):
        assert reader.read()[-1]['text'] == 'Video delivered; ready to watch.'
        log.write_text(json.dumps(record) + '\n')
        assert reader.read() == []
        assert reader.last_text == 'Video delivered; ready to watch.'
        with log.open('a') as handle:
            handle.write(json.dumps({'type': 'user', 'timestamp': '2026-10-23T10:02:00Z',
                                     'message': {'content': 'Please revise the video.'}}) + '\n')
        assert reader.read()[-1]['clear'] is True
