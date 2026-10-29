"""Regression coverage for OPS-1275: when CCC's app-server `thread/start`
loses a shared-state-db race and falls back to a bare `codex exec`
subprocess, that subprocess's own native rollout write can silently stall
after `session_meta`/`task_started`, even though the run completed
correctly and CCC's own spawn log captured the whole thing. The dashboard
should render that spawn log instead of showing a blank transcript."""
import json

import server  # noqa: F401
from ccc_server import codex as codex_mod
from ccc_server import codex_parse


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_stub_rollout_is_detected(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    _write_jsonl(rollout, [
        {"type": "session_meta", "payload": {"id": "t1"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    assert codex_mod._codex_rollout_is_stub(rollout) is True


def test_populated_rollout_is_not_a_stub(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    _write_jsonl(rollout, [
        {"type": "session_meta", "payload": {"id": "t1"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "hi"}},
    ])
    assert codex_mod._codex_rollout_is_stub(rollout) is False


def test_find_spawn_log_matches_by_thread_id(tmp_path):
    thread_id = "01a0ee9e-e7c2-70f2-a796-9afab59953e5"
    log_dir = tmp_path / ".claude" / "logs"
    log_dir.mkdir(parents=True)
    other_log = log_dir / "spawn-codex-other-task-20260101T000000.log"
    other_log.write_text('{"type":"thread.started","thread_id":"some-other-id"}\n')
    matching_log = log_dir / "spawn-codex-muse-password-20260929T122301.log"
    matching_log.write_text(
        "Reading additional input from stdin...\n"
        f'{{"type":"thread.started","thread_id":"{thread_id}"}}\n'
        '{"type":"turn.started"}\n'
    )

    found = codex_mod._find_ccc_spawn_log_for_thread(thread_id, str(tmp_path))
    assert found == matching_log


def test_find_spawn_log_returns_none_without_match(tmp_path):
    log_dir = tmp_path / ".claude" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "spawn-codex-unrelated-20260101T000000.log").write_text(
        '{"type":"thread.started","thread_id":"unrelated"}\n'
    )
    assert codex_mod._find_ccc_spawn_log_for_thread("missing-thread-id", str(tmp_path)) is None


def test_parse_exec_log_event_renders_agent_message_and_result():
    agent_event = {"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "the answer"}}
    parsed = codex_parse._parse_codex_exec_log_event(agent_event, 10)
    assert parsed["type"] == "assistant"
    assert parsed["blocks"] == [{"kind": "text", "text": "the answer"}]

    command_event = {
        "type": "item.completed",
        "item": {"id": "item_1", "type": "command_execution", "command": "ls", "status": "completed", "exit_code": 0},
    }
    parsed_cmd = codex_parse._parse_codex_exec_log_event(command_event, 7)
    assert parsed_cmd["type"] == "assistant"
    assert parsed_cmd["blocks"][0]["kind"] == "tool_use"
    assert parsed_cmd["blocks"][0]["command"] == "ls"

    turn_event = {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}
    parsed_turn = codex_parse._parse_codex_exec_log_event(turn_event, 11)
    assert parsed_turn["type"] == "result"
    assert parsed_turn["token_usage"] == {"input_tokens": 10, "output_tokens": 2}


def test_parse_exec_log_event_ignores_unrelated_lines():
    assert codex_parse._parse_codex_exec_log_event({"type": "turn.started"}, 1) is None
    assert codex_parse._parse_codex_exec_log_event(
        {"type": "item.completed", "item": {"id": "x", "type": "reasoning"}}, 2,
    ) is None


def test_resolve_conversation_reader_falls_back_to_spawn_log(tmp_path, monkeypatch):
    thread_id = "thread-stub-fallback"
    rollout = tmp_path / "rollout.jsonl"
    _write_jsonl(rollout, [
        {"type": "session_meta", "payload": {"id": thread_id}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])

    repo = tmp_path / "repo"
    log_dir = repo / ".claude" / "logs"
    log_dir.mkdir(parents=True)
    spawn_log = log_dir / "spawn-codex-demo-20260101T000000.log"
    spawn_log.write_text(
        f'{{"type":"thread.started","thread_id":"{thread_id}"}}\n'
        '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"done"}}\n'
    )

    monkeypatch.setattr(server, "_resolve_codex_rollout_path", lambda tid: rollout)
    filepath, parser = server._resolve_conversation_reader(thread_id, repo_path=str(repo))
    assert filepath == spawn_log
    assert parser is server._parse_codex_exec_log_event


def _stub_reader_fixture(tmp_path, monkeypatch):
    sid = 'test-recovery-thread'
    rollout = tmp_path / 'rollout.jsonl'
    _write_jsonl(rollout, [
        {'type': 'session_meta', 'payload': {'id': sid}},
        {'type': 'event_msg', 'payload': {'type': 'task_started'}},
    ])
    log_dir = tmp_path / '.claude' / 'logs'
    log_dir.mkdir(parents=True)
    capture = log_dir / 'spawn-codex-demo.log'
    _write_jsonl(capture, [
        {'type': 'thread.started', 'thread_id': sid},
        {'type': 'item.completed', 'item': {'id': 'answer', 'type': 'agent_message', 'text': 'Captured answer'}},
    ])
    monkeypatch.setattr(server, '_resolve_conversation_path', lambda *a, **kw: tmp_path / 'missing.jsonl')
    monkeypatch.setattr(server, '_resolve_codex_rollout_path', lambda _: rollout)
    monkeypatch.setattr(server, '_detect_session_engine', lambda _: 'codex')
    monkeypatch.setattr(server, '_codex_thread_row', lambda _: {'cwd': str(tmp_path), 'first_user_message': 'Test request'})
    monkeypatch.setattr(server, '_git_toplevel_for_existing_dir', lambda _: None)
    monkeypatch.setattr(server, '_codex_logs_for_session', lambda _: [(1, str(capture))])
    monkeypatch.setattr(server, '_get_queued_events_for_session', lambda _: [])
    server._CONV_PARSE_CACHE.clear()
    server._CONV_PATH_CACHE.clear()
    return sid, rollout


def _answer_texts(result):
    return [block['text'] for event in result['events'] for block in event.get('blocks', []) if block.get('kind') == 'text']


def test_normal_viewer_without_repo_argument_recovers(tmp_path, monkeypatch):
    sid, _ = _stub_reader_fixture(tmp_path, monkeypatch)
    assert _answer_texts(server.parse_conversation(sid)) == ['Captured answer']


def test_native_history_replaces_cached_capture(tmp_path, monkeypatch):
    sid, rollout = _stub_reader_fixture(tmp_path, monkeypatch)
    assert _answer_texts(server.parse_conversation(sid)) == ['Captured answer']
    with rollout.open('a') as handle:
        handle.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'Native answer'}}) + '\n')
    assert _answer_texts(server.parse_conversation(sid)) == ['Native answer']
