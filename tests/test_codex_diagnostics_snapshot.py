"""Slow collectors must not hold Metadata HTTP requests or multiply workers."""
import collections
import threading
import time
from unittest import mock

import pytest
import server
from ccc_server import codex_diagnostics as diagnostics


@pytest.fixture
def cache(monkeypatch):
    entries = collections.OrderedDict()
    monkeypatch.setattr(diagnostics, '_codex_diagnostics_cache', entries)
    monkeypatch.setattr(diagnostics, '_CODEX_DIAGNOSTICS_WAIT_S', 0.01)
    monkeypatch.setattr(diagnostics, '_CODEX_DIAGNOSTICS_MAX_ENTRIES', 4)
    yield entries
    for entry in list(entries.values()):
        if entry['running']:
            assert entry['done'].wait(3)


def test_cold_deadline_coalesces_and_eventually_returns_full_result(cache):
    release = threading.Event()
    def collect(sid):
        assert release.wait(3)
        return {'ok': True, 'session_id': sid, 'verdict': {'state': 'idle'}}
    with mock.patch.object(server, 'build_codex_session_diagnostics', side_effect=collect) as build:
        try:
            start = time.monotonic()
            first = diagnostics.codex_session_diagnostics_snapshot('session-a')
            assert time.monotonic() - start < 0.3
            assert first['pending'] is True
            assert 'verdict' not in first, 'no idle or missing-storage verdict is invented'
            assert diagnostics.codex_session_diagnostics_snapshot('session-a')['pending']
            assert build.call_count == 1
        finally:
            release.set()
            assert cache['session-a']['done'].wait(3)
        ready = diagnostics.codex_session_diagnostics_snapshot('session-a')
        assert ready['verdict']['state'] == 'idle'
        assert not ready.get('pending')
        assert ready['refreshing'] is False


def test_stale_snapshot_is_visible_during_one_refresh(cache):
    release = threading.Event()
    old = {'ok': True, 'session_id': 'session-a', 'verdict': {'state': 'idle'}}
    def collect(sid):
        assert release.wait(3)
        return {**old, 'verdict': {'state': 'running_exec'}}
    with mock.patch.object(server, 'build_codex_session_diagnostics', return_value=old):
        diagnostics.codex_session_diagnostics_snapshot('session-a')
        assert cache['session-a']['done'].wait(3)
    cache['session-a']['updated'] -= 10
    with mock.patch.object(server, 'build_codex_session_diagnostics', side_effect=collect) as build:
        try:
            for _ in range(5):
                stale = diagnostics.codex_session_diagnostics_snapshot('session-a')
                assert stale['verdict']['state'] == 'idle'
                assert stale['refreshing'] is True
                assert stale['snapshot_age_s'] >= 10
            assert build.call_count == 1
            assert 'refreshing' not in old, 'cached payload stays immutable'
        finally:
            release.set()
            assert cache['session-a']['done'].wait(3)
    assert diagnostics.codex_session_diagnostics_snapshot('session-a')['verdict']['state'] == 'running_exec'


def test_collector_concurrency_and_cache_size_are_bounded(cache):
    release = threading.Event()
    def collect(sid):
        assert release.wait(3)
        return {'ok': True, 'session_id': sid}
    with mock.patch.object(server, 'build_codex_session_diagnostics', side_effect=collect) as build:
        try:
            for i in range(12):
                assert diagnostics.codex_session_diagnostics_snapshot('session-' + str(i))['pending']
            assert build.call_count == 2
            assert len(cache) == 4
        finally:
            release.set()
            for entry in list(cache.values()):
                if entry['running']:
                    assert entry['done'].wait(3)


def test_errors_and_empty_ids_do_not_leave_a_collector_running(cache):
    with mock.patch.object(server, 'build_codex_session_diagnostics', side_effect=RuntimeError('failed')):
        assert diagnostics.codex_session_diagnostics_snapshot('session-a')['ok'] is False
    assert cache['session-a']['running'] is False
    assert diagnostics.codex_session_diagnostics_snapshot('')['error'] == 'missing session_id'
