"""Terminal Antigravity sessions must reach bulk liveness and census."""
from unittest import mock
import os
from pathlib import Path

import pytest

import server
from ccc_server import antigravity, antigravity_compact


SID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OTHER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


def test_plain_terminal_session_reaches_bulk_discovery(tmp_path):
    log = tmp_path / 'log' / 'cli-test.log'
    with mock.patch.object(server, '_scan_engine_processes', return_value=[
        ('123', 'pts/1', 'agy', 'agy'),
    ]), mock.patch.object(antigravity, '_cli_process_logs', return_value={log}), \
         mock.patch.object(server, '_antigravity_cli_log_meta', return_value={'session_id': SID}), \
         mock.patch.object(server, '_raw_engine_process_commands', return_value=[]), \
         mock.patch.object(server, '_spawned_engine_session_ids', return_value=set()), \
         mock.patch.object(server, '_engine_live_sids_cache', {'ts': 0, 'sids': frozenset()}):
        assert SID in server._live_engine_session_ids()
        with mock.patch.object(server, '_detect_session_engine', return_value='antigravity'):
            assert server._archive_session_is_live_uncached(SID)


def test_current_log_identity_wins_over_original_resume_argument():
    with mock.patch.object(server, '_scan_engine_processes', return_value=[
        ('123', 'pts/1', 'agy', f'agy --conversation {OTHER}'),
    ]), mock.patch.object(antigravity, '_cli_process_logs', return_value={'cli-test.log'}), \
         mock.patch.object(server, '_antigravity_cli_log_meta', return_value={'session_id': SID}):
        assert antigravity.live_cli_session_ids() == {SID}


def test_resume_without_readable_log_still_discovered():
    with mock.patch.object(server, '_scan_engine_processes', return_value=[
        ('123', 'pts/1', 'agy', f'agy --conversation {SID}'),
        ('124', 'pts/2', 'agy', 'agy'),
        ('125', 'pts/3', 'bash', f'bash -c "agy --conversation {OTHER}"'),
    ]), mock.patch.object(antigravity, '_cli_process_logs', return_value=set()):
        assert antigravity.live_cli_session_ids() == {SID}


def test_compaction_pty_is_not_a_user_terminal(monkeypatch):
    monkeypatch.setattr(server, '_scan_engine_processes', lambda: [
        ('123', 'pts/1', 'agy', f'agy --conversation {SID}'),
        ('124', 'pts/2', 'agy', f'agy --conversation {OTHER}'),
    ])
    monkeypatch.setattr(antigravity_compact, '_HIDDEN_PIDS', {123})
    monkeypatch.setattr(antigravity_compact, '_ACTIVE', {SID})
    monkeypatch.setattr(server, '_proc_cwd', lambda pid: '/test')
    monkeypatch.setattr(server, '_proc_ancestor_terminal', lambda pid: (None, None))
    for name in ('hermes', 'kimi', 'grok', 'devin_cli', 'codex', 'gemini', 'cursor'):
        monkeypatch.setattr(server, f'_is_{name}_session', lambda sid: False)
    monkeypatch.setattr(server, '_is_antigravity_session', lambda sid: True)

    assert [proc['pid'] for proc in server.find_live_antigravity_processes()] == [124]
    status = server.session_live_status(SID, '/test')
    assert status['live'] and status['status'] == 'running' and status['tty'] is None


def test_unrelated_prompts_do_not_enter_shared_command_token_cache(monkeypatch):
    command = 'codex exec -- ' + ('unrelated-prompt ' * 2000)
    monkeypatch.setattr(server, '_scan_engine_processes', lambda: [
        ('123', 'pts/1', 'codex', command),
        ('124', 'pts/2', 'bash', f'bash -c "agy --conversation {OTHER}"'),
    ])
    monkeypatch.setattr(server, '_command_tokens', lambda command: pytest.fail(
        'unrelated process reached full command parsing'))
    monkeypatch.setattr(antigravity, '_cli_process_logs', lambda pid: pytest.fail(
        'unrelated process reached log discovery'))
    assert antigravity.live_cli_session_ids() == set()


def test_quoted_executable_and_malformed_prompt_keep_resume_fallback(monkeypatch):
    monkeypatch.setattr(server, '_scan_engine_processes', lambda: [
        ('123', 'pts/1', 'unknown', f'"/opt/Example Tools/agy" --conversation {SID}'),
        ('124', 'pts/2', 'agy', f'agy --conversation {OTHER} "unfinished'),
    ])
    monkeypatch.setattr(antigravity, '_cli_process_logs', lambda pid: set())
    assert antigravity.live_cli_session_ids() == {SID, OTHER}


@pytest.mark.skipif(not Path('/proc').is_dir(), reason='Linux procfs check')
def test_linux_open_log_discovery(tmp_path):
    logs = tmp_path / 'log'
    logs.mkdir()
    log = logs / 'cli-test.log'
    with log.open('w'):
        assert log in antigravity._cli_process_logs(os.getpid())
