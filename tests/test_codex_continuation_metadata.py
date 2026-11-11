"""Continuation metadata must survive preambles and engine-specific paths."""
import json
from unittest.mock import Mock

import server
from ccc_server import codex_parse


def test_codex_list_keeps_origin_beyond_preview(tmp_path, monkeypatch):
    origin = 'origin-session-123'
    prompt = '<instructions>' + 'preamble ' * 100 + '</instructions>\nOrigin session id: ' + origin
    rollout = tmp_path / 'rollout.jsonl'
    rollout.write_text('{}\n')
    fake = Mock()
    fake._codex_fetch_threads.return_value = [{'id': 'child-session-123', 'first_user_message': prompt}]
    for name in ['_auto_titled_session_ids', '_load_repo_pins', '_load_session_name_overrides',
                 '_load_last_interactions', '_codex_spawn_pid_by_thread_id',
                 '_codex_spawn_parent_by_child', '_load_codex_parent_links', '_codex_goals_snapshot',
                 '_extract_codex_tail_meta', '_codex_app_server_activity_fields',
                 '_codex_activity_fields_from_tail', '_codex_stale_tool_fields']:
        getattr(fake, name).return_value = {}
    fake._session_graph.parent_snapshot.return_value = {}
    fake._load_conversation_lifecycle_sets.return_value = (set(), set())
    fake._load_verified_conversations.return_value = []
    fake._codex_capture_rows.return_value = []
    fake._codex_rollout_path_from_row.return_value = rollout
    fake._codex_ts_seconds.return_value = 0
    fake._strip_ccc_session_state_instruction.side_effect = lambda s: s or ''
    fake._strip_host_system_instruction.side_effect = lambda s: s or ''
    fake._spawn_timeline_mark.return_value = False
    fake._codex_display_name.return_value = 'A task'
    fake._codex_agent_task_label.return_value = ''
    fake._first_existing_dir.return_value = ''
    fake._continued_from_session_id_from_text.side_effect = server._continued_from_session_id_from_text
    monkeypatch.setattr(codex_parse, '_core', fake)
    rows = codex_parse.find_codex_conversations(repo_only=False, resolve_pr_states=False, resolve_worktree_dirty=False)
    assert len(rows[0]['first_message']) == 200
    assert 'Origin session id:' not in rows[0]['first_message']
    assert rows[0]['continued_from_session_id'] == origin


def test_conversation_open_reads_codex_origin_without_claude_transcript(tmp_path, monkeypatch):
    origin = 'origin-session-123'
    rollout = tmp_path / 'rollout.jsonl'
    rollout.write_text(json.dumps({'type': 'response_item', 'payload': {
        'type': 'message', 'role': 'user', 'content': [{'type': 'input_text',
        'text': 'Origin session id: ' + origin}]}}) + '\n')
    monkeypatch.setattr(server, '_session_acp_harness', lambda sid: None)
    monkeypatch.setattr(server, '_conv_response_bytes_get', lambda *a, **k: None)
    monkeypatch.setattr(server, '_conv_response_bytes_put', lambda *a, **k: None)
    monkeypatch.setattr(server, 'parse_conversation', lambda *a, **k: {'events': [], 'last_line': 1})
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'codex')
    monkeypatch.setattr(server, '_resolve_conversation_path', lambda sid: tmp_path / 'missing.jsonl')
    monkeypatch.setattr(server, '_resolve_codex_rollout_path', lambda sid: rollout)
    response = server._conversation_open_response('child-session-123', '', '')
    assert json.loads(response['body'])['continued_from_session_id'] == origin


def test_cached_codex_origin_survives_registry_pruning(tmp_path, monkeypatch):
    origin = 'origin-session-123'
    rollout = tmp_path / 'rollout.jsonl'
    rollout.write_text(json.dumps({'type': 'response_item', 'payload': {
        'type': 'message', 'role': 'user', 'content': [{'type': 'input_text',
        'text': 'Origin session id: ' + origin}]}}) + '\n')
    for name in ['_load_session_name_overrides', '_load_last_interactions',
                 '_spawn_registry_entries_by_session', '_load_spawn_markers',
                 '_load_session_overrides', '_load_session_registry',
                 '_codex_titles_snapshot', '_auto_titled_session_ids']:
        monkeypatch.setattr(server, name, lambda: {})
    for name in ['_load_verified_conversations', '_load_pinned_conversations',
                 '_discover_live_session_ids', '_codex_deleted_thread_ids']:
        monkeypatch.setattr(server, name, lambda: set())
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), set()))
    monkeypatch.setattr(server._session_graph, 'parent_of', lambda sid: origin)
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: {})
    for name in ['_apply_pinned_conversation_fields', '_sort_pinned_conversations_first',
                 '_apply_session_lane_overrides', '_apply_spawn_markers', '_stamp_archive_state',
                 '_stamp_archive_goals', '_apply_watchtower_worker_display_names']:
        monkeypatch.setattr(server, name, lambda *a: None)
    for preserved_origin in ['', origin]:
        rows = server._rehydrate_archive_cached_rows([{
            'session_id': 'child-session-123', 'engine': 'codex', 'mtime': 1,
            'first_message': 'preamble ' * 25, 'jsonl_path': str(rollout),
            'continued_from_session_id': preserved_origin,
        }])
        assert rows[0]['continued_from_session_id'] == origin
