"""Terminal AGY sessions reach the sidebar while its archive is stale."""
import json

import pytest

import server
from ccc_server import antigravity, engines

SID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OLD = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    repo = tmp_path / 'project'
    repo.mkdir()
    log = tmp_path / 'log' / 'cli-test.log'
    transcript = tmp_path / 'transcript_full.jsonl'
    transcript.touch()
    monkeypatch.setattr(server, 'find_live_antigravity_processes', lambda: [{
        'pid': 123, 'cwd': str(repo), 'command': f'agy --conversation {OLD}',
    }])
    monkeypatch.setattr(antigravity, '_cli_process_logs', lambda pid: {log})
    monkeypatch.setattr(server, '_antigravity_cli_log_meta', lambda p: {
        'session_id': SID, 'cwd': str(repo), 'model': 'test-model',
    })
    monkeypatch.setattr(server, '_antigravity_transcript_path', lambda sid: transcript)
    monkeypatch.setattr(server, '_extract_antigravity_tail_meta', lambda p: {
        'first_message': 'Help with this project', 'last_meaningful_ts': 1234,
        'last_event_type': 'user',
    })
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), set()))
    monkeypatch.setattr(server, '_load_session_name_overrides', lambda: {})
    monkeypatch.setattr(server, '_load_repo_pins', lambda: {})
    monkeypatch.setattr(server, '_load_antigravity_summary_titles', lambda: {})
    monkeypatch.setattr(server, '_antigravity_cli_conversation_path', lambda sid: tmp_path / 'missing.db')
    monkeypatch.setattr(server, '_antigravity_app_conversation_path', lambda sid: None)
    monkeypatch.setattr(server, 'find_antigravity_conversations', lambda *a, **kw: pytest.fail('full scan'))
    return repo


def test_stale_archive_gets_current_terminal_chat(terminal):
    rows = antigravity.archive_terminal_overlay([])
    assert len(rows) == 1
    row = rows[0]
    assert row['session_id'] == SID  # current log, not original resume argv
    assert row['folder_path'] == str(terminal)
    assert row['display_name'] == 'Help with this project'
    assert row['model'] == 'test-model'
    assert row['modified'] == 1234
    assert row['is_live']


def test_terminal_overlay_consumes_prefetched_processes(terminal, monkeypatch):
    processes = server.find_live_antigravity_processes()
    monkeypatch.setattr(server, 'find_live_antigravity_processes',
                        lambda: pytest.fail('duplicate process scan'))
    rows = antigravity.archive_terminal_overlay([], process_reader=lambda: processes)
    assert [row['session_id'] for row in rows] == [SID]


def test_snapshot_and_lifecycle_exclusions_skip_transcript_reads(terminal, monkeypatch):
    monkeypatch.setattr(server, '_extract_antigravity_tail_meta', lambda p: pytest.fail('excluded row'))
    assert antigravity.archive_terminal_overlay([{'session_id': SID}]) == []
    for archived, trashed in (({SID}, set()), (set(), {SID})):
        monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (archived, trashed))
        assert antigravity.archive_terminal_overlay([]) == []


def test_missing_transcript_and_log_allow_resume_identity(terminal, monkeypatch):
    monkeypatch.setattr(antigravity, '_cli_process_logs', lambda pid: set())
    monkeypatch.setattr(server, '_antigravity_transcript_path', lambda sid: None)
    rows = antigravity.archive_terminal_overlay([])
    assert rows[0]['session_id'] == OLD
    assert rows[0]['session_cwd'] == str(terminal)
    assert rows[0]['display_name'] == 'Antigravity session'
    assert rows[0]['jsonl_path'] == ''


def test_source_includes_terminal_overlay_in_body_version(terminal, monkeypatch):
    base = [{'session_id': 'stored'}]
    monkeypatch.setattr(server, '_archive_serve_rows_versioned', lambda *a, **kw: (base, True, 7))
    for name in ('codex', 'acp', 'devin_cli', 'wt_worker', 'claude_registry'):
        monkeypatch.setattr(server, f'_archive_overlay_{name}_sessions', lambda *a, **kw: [])
    monkeypatch.setattr(server, '_archive_overlay_claude_registry_sessions',
                        lambda *a, **kw: [{'session_id': 'terminal-claude'}])
    monkeypatch.setattr(server, '_apply_session_lane_overrides', lambda rows: None)
    captured = []
    monkeypatch.setattr(server, '_archive_list_body_ver', lambda key, rows, extra: captured.extend(extra) or 8)
    rows, cached, version = server._archive_list_source_rows_read({})
    assert [row['session_id'] for row in rows] == ['stored', SID, 'terminal-claude']
    assert base == [{'session_id': 'stored'}]
    assert captured == rows[1:]
    assert cached and version == 8


def test_native_launch_requires_parent_receipt_at_the_invoke_result_step(tmp_path, monkeypatch):
    peer = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
    paths = {}
    for sid in (SID, OLD, peer):
        folder = tmp_path / sid / '.system_generated' / 'logs'
        folder.mkdir(parents=True)
        paths[sid] = folder / 'transcript_full.jsonl'
        paths[sid].touch()
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_antigravity_tail_resume', {})
    monkeypatch.setattr(server, '_antigravity_transcript_path', paths.get)
    monkeypatch.setattr(server, '_antigravity_transcript_paths', lambda: [paths[SID], paths[peer]])
    monkeypatch.setattr(server, '_load_repo_pins', lambda: {})
    monkeypatch.setattr(server, '_load_session_name_overrides', lambda: {})
    monkeypatch.setattr(server, '_load_antigravity_summary_titles', lambda: {})
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), set()))
    monkeypatch.setattr(engines, '_antigravity_spawn_pid_by_session_id', lambda: {})
    monkeypatch.setattr(engines, '_antigravity_cli_log_paths', lambda *a: [])
    monkeypatch.setattr(engines, '_antigravity_list_context_usage', lambda *a, **kw: (0, 0))

    def append(sid, **event):
        with paths[sid].open('a') as stream:
            stream.write(json.dumps(event) + '\n')
        server._conv_meta_cache.pop(str(paths[sid]), None)

    for sid in (SID, peer):
        append(sid, type='SYSTEM_MESSAGE', source='SYSTEM', step_index=0,
               content=f'Engine message\n<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-19T19:54:13Z '
                       f'sender={OLD} priority=MESSAGE_PRIORITY_HIGH content=Check the release.\n</SYSTEM_MESSAGE>')
        append(sid, type='PLANNER_RESPONSE', source='MODEL', status='DONE', step_index=1,
               content='I checked the release.')

    append(OLD, type='PLANNER_RESPONSE', source='MODEL', step_index=41, tool_calls=[
        {'name': 'run_command'},
        {'name': 'invoke_subagent', 'args': {'Role': 'Release checker'}},
    ])
    server._extract_antigravity_tail_meta(paths[OLD])
    receipt = 'Created the following subagent:\n' + json.dumps({'conversationId': peer})
    append(OLD, type='GENERIC', source='MODEL', status='DONE', step_index=42, content=receipt)
    receipt = 'Created the following subagent:\n' + json.dumps({'conversationId': SID})
    append(OLD, type='GENERIC', source='MODEL', status='DONE', step_index=43, content=receipt)
    rows = {row['session_id']: row for row in
            engines.find_antigravity_conversations(repo_only=False, resolve_worktree_dirty=False)}
    assert rows[SID]['parent_session_id'] == OLD
    assert rows[SID]['display_name'] == 'Release checker'
    assert rows[SID]['first_message'] == 'Check the release.'
    assert rows[SID]['turn_driver']['id'] == OLD
    assert rows[peer]['parent_session_id'] == ''
    assert rows[peer]['display_name'] == 'Antigravity session'
