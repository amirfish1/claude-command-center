"""A registered worker must not wait for the transcript archive to refresh."""
import importlib
import pytest


@pytest.fixture
def env(monkeypatch):
    server = importlib.import_module('server')
    workers = [{'worker_id': 'ops-example', 'queue': 'OPS', 'session_id': 'worker-session',
                'pid': 123, 'engine': 'codex', 'model': 'test-model', 'alive': True,
                'repo_path': '/tmp/example', 'started_at': '2026-09-08T01:48:30Z', 'idle_seconds': 3}]
    monkeypatch.setattr(server, '_archive_live_wt_workers', lambda: workers)
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), set()))
    monkeypatch.setattr(server, '_wt_read_config', lambda: {})
    return server, workers


def test_worker_appears_before_archive_and_real_row_takes_over(env):
    server, workers = env
    rows = server._archive_overlay_wt_worker_sessions([])
    assert len(rows) == 1
    row = rows[0]
    assert row['session_id'] == 'worker-session'
    assert row['source'] == 'codex'
    assert row['spawned_via'] == 'watchtower'
    assert row['state'] == 'working'
    assert row['display_name'] == 'OPS worker'
    assert row['session_cwd'] == '/tmp/example'
    assert server._archive_overlay_wt_worker_sessions([{'session_id': 'worker-session'}]) == []
    workers.append(dict(workers[0]))
    assert len(server._archive_overlay_wt_worker_sessions([])) == 1


@pytest.mark.parametrize('change', [{'alive': False}, {'released_at': '2026-09-08T01:50:00Z'}, {'session_id': ''}])
def test_dead_released_and_unidentified_workers_are_not_sessions(env, change):
    server, workers = env
    workers[0].update(change)
    assert server._archive_overlay_wt_worker_sessions([]) == []


def test_manual_lifecycle_choice_is_preserved(env, monkeypatch):
    server, workers = env
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: ({'worker-session'}, {'worker-session'}))
    assert server._archive_overlay_wt_worker_sessions([]) == []


def test_overlay_content_stable_between_polls(env):
    server, workers = env
    first = server._archive_overlay_wt_worker_sessions([])
    assert server._archive_overlay_wt_worker_sessions([]) == first
    workers[0]['idle_seconds'] = 4
    assert server._archive_overlay_wt_worker_sessions([]) == first


def test_list_worker_snapshot_skips_transcript_discovery(monkeypatch):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_WT_WORKERS_CACHE', {'ts': 0, 'rows': []})
    calls = []
    monkeypatch.setattr(server, '_wt_read_workers', lambda **kw: calls.append(kw) or [])
    server._archive_live_wt_workers()
    server._archive_live_wt_workers()
    assert calls == [{'include_activity': False}]


def test_broken_worker_read_does_not_break_conversation_list(monkeypatch):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_WT_WORKERS_CACHE', {'ts': 0, 'rows': []})
    def broken(**kwargs): raise ValueError('bad worker metadata')
    monkeypatch.setattr(server, '_wt_read_workers', broken)
    assert server._archive_live_wt_workers() == []
