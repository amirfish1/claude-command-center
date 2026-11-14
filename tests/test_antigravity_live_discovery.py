"""Terminal Antigravity sessions must reach bulk liveness and census."""
from unittest import mock
import os
from pathlib import Path

import pytest

import server
from ccc_server import antigravity


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


@pytest.mark.skipif(not Path('/proc').is_dir(), reason='Linux procfs check')
def test_linux_open_log_discovery(tmp_path):
    logs = tmp_path / 'log'
    logs.mkdir()
    log = logs / 'cli-test.log'
    with log.open('w'):
        assert log in antigravity._cli_process_logs(os.getpid())
