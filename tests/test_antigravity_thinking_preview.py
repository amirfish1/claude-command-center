"""Thought-only Gemini progress should explain a running Antigravity row."""
import json

import pytest

import server
from ccc_server import engines


@pytest.fixture
def chat(tmp_path, monkeypatch):
    logs = tmp_path / 'session-test' / '.system_generated' / 'logs'
    logs.mkdir(parents=True)
    transcript = logs / 'transcript_full.jsonl'
    transcript.touch()
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_antigravity_tail_resume', {})
    monkeypatch.setattr(server, '_antigravity_transcript_paths', lambda: [transcript])
    monkeypatch.setattr(server, '_load_repo_pins', lambda: {})
    monkeypatch.setattr(server, '_load_session_name_overrides', lambda: {})
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), set()))
    monkeypatch.setattr(server, '_load_verified_conversations', lambda: [])
    monkeypatch.setattr(server, '_load_last_interactions', lambda: {})
    monkeypatch.setattr(server, '_load_antigravity_summary_titles', lambda: {})
    monkeypatch.setattr(engines, '_antigravity_cli_log_paths', lambda *a: [])
    monkeypatch.setattr(engines, '_antigravity_list_context_usage', lambda *a, **k: (0, 0))
    monkeypatch.setattr(server, '_antigravity_app_conversation_path', lambda *a: None)
    monkeypatch.setattr(server, '_git_branch_for_cwd', lambda *a: '')
    alive = {'value': True}
    monkeypatch.setattr(engines, '_antigravity_spawn_pid_by_session_id', lambda: {
        'session-test': {'pid': 123, 'alive': alive['value']},
    })

    def append(**event):
        with transcript.open('a') as stream:
            stream.write(json.dumps(event) + '\n')
        # Deterministic file-version change, even on coarse-mtime filesystems.
        server._conv_meta_cache.pop(str(transcript), None)

    def row():
        return engines.find_antigravity_conversations(
            '', repo_only=False, include_old=True, resolve_worktree_dirty=False,
        )[0]

    return transcript, alive, append, row


def test_thought_only_progress_replaces_old_reply_only_while_live(chat):
    path, alive, append, row = chat
    append(type='PLANNER_RESPONSE', content='Previous answer.')
    append(type='USER_INPUT', source='USER_EXPLICIT', content='Check the next issue.')
    append(type='PLANNER_RESPONSE', thinking='I am checking the current configuration.',
           tool_calls=[{'name': 'view_file', 'args': {'AbsolutePath': '/example/config.py'}}])
    meta = server._extract_antigravity_tail_meta(path)
    assert meta['last_assistant_text'] == 'Previous answer.'
    assert row()['last_assistant_text'] == 'I am checking the current configuration.'
    assert row()['pending_tool'] == 'view_file'
    assert not row()['session_state']
    alive['value'] = False
    assert row()['last_assistant_text'] == 'Previous answer.'


def test_new_input_and_real_reply_clear_thought_preview(chat):
    path, alive, append, row = chat
    append(type='PLANNER_RESPONSE', thinking='Checking the configuration.')
    assert row()['last_assistant_text'] == 'Checking the configuration.'
    append(type='USER_INPUT', source='USER_EXPLICIT', content='Different question.')
    assert server._extract_antigravity_tail_meta(path)['last_thinking_text'] is None
    assert row()['last_event_type'] == 'user'
    append(type='PLANNER_RESPONSE', thinking='Considering the answer.', content='Here is the answer.')
    assert row()['last_assistant_text'] == 'Here is the answer.'
    assert server._extract_antigravity_tail_meta(path)['last_thinking_text'] is None


def test_thought_preview_is_bounded_and_invalidates_old_cache(chat):
    path, alive, append, row = chat
    append(type='PLANNER_RESPONSE', thinking='x' * 5000)
    server._conv_meta_cache[str(path)] = {
        'engine': 'antigravity', 'meta_version': 4, 'mtime': path.stat().st_mtime,
        'last_assistant_text': None,
    }
    assert len(row()['last_assistant_text']) == 1200
    assert server._extract_antigravity_tail_meta(path)['meta_version'] == 5
