"""CCC-153: a terminal-started Claude session must not wait for the archive
snapshot refresh (5 min TTL, and never under the saturation gate)."""
import importlib
import pytest


@pytest.fixture
def env(monkeypatch, tmp_path):
    server = importlib.import_module('server')
    reg_dir = tmp_path / 'sessions'
    reg_dir.mkdir()
    registry = {'term-session': {'pid': 4242, 'cwd': str(tmp_path)}}
    calls = []

    def load():
        calls.append(1)
        return dict(registry)

    monkeypatch.setattr(server, 'SESSIONS_REGISTRY', reg_dir)
    monkeypatch.setattr(server, '_ARCHIVE_CLAUDE_REG_CACHE',
                        {'sig': None, 'rows': [], 'changed_at': 0.0, 'built_at': 0.0})
    monkeypatch.setattr(server, '_load_session_registry', load)
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), set()))
    monkeypatch.setattr(server, '_live_registry_conversation_row',
                        lambda sid, meta, **kw: {'id': sid, 'session_id': sid, 'is_live': True})
    return server, registry, calls, reg_dir


def test_live_terminal_session_overlays_until_real_row_arrives(env):
    server, registry, calls, _ = env
    rows = server._archive_overlay_claude_registry_sessions([{'session_id': 'other'}])
    assert [r['session_id'] for r in rows] == ['term-session']
    assert server._archive_overlay_claude_registry_sessions([{'session_id': 'term-session'}]) == []


def test_existing_registry_sessions_skip_row_metadata_without_hiding_new_sessions(env, monkeypatch):
    server, registry, _, reg_dir = env
    built = []
    monkeypatch.setattr(server, '_live_registry_conversation_row',
                        lambda sid, meta, **kw: built.append(sid) or {'session_id': sid})
    assert server._archive_overlay_claude_registry_sessions(
        [{'id': 'term-session'}]) == []
    assert built == [], 'covered sessions must not build launch/repository metadata'
    registry['new-terminal'] = {'pid': 4343, 'cwd': str(reg_dir)}
    actual = server._archive_overlay_claude_registry_sessions(
        [], existing_ids={'term-session'})
    assert [row['session_id'] for row in actual] == ['new-terminal']
    assert built == ['new-terminal'], 'a gap must not build metadata for covered sessions'
    actual = server._archive_overlay_claude_registry_sessions(
        [], existing_ids={'new-terminal'})
    assert [row['session_id'] for row in actual] == ['term-session']
    assert built == ['new-terminal', 'term-session'], 'snapshot membership also keys the cache'


def test_hidden_registry_sessions_skip_row_metadata(env, monkeypatch):
    server, *_ = env
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), {'term-session'}))
    monkeypatch.setattr(server, '_archive_claude_registry_rows',
                        lambda: pytest.fail('trashed sessions are not gap candidates'))
    assert server._archive_overlay_claude_registry_sessions([]) == []


def test_registry_read_is_gated_by_dir_mtime(env, monkeypatch):
    server, registry, calls, reg_dir = env
    server._archive_claude_registry_rows()
    # Past the post-change settle window, an unchanged dir reads nothing.
    server._ARCHIVE_CLAUDE_REG_CACHE['changed_at'] -= 60
    server._ARCHIVE_CLAUDE_REG_CACHE['built_at'] -= 60
    for _ in range(20):
        server._archive_claude_registry_rows()
    assert len(calls) == 1
    (reg_dir / '4243.json').write_text('{}')
    import os
    st = os.stat(reg_dir)
    os.utime(reg_dir, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    server._archive_claude_registry_rows()
    assert len(calls) == 2


def test_archived_and_trashed_sessions_stay_hidden(env, monkeypatch):
    server, *_ = env
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: ({'term-session'}, set()))
    assert server._archive_overlay_claude_registry_sessions([]) == []


def test_broken_registry_does_not_break_list(env, monkeypatch):
    server, *_ = env
    def broken():
        raise ValueError('bad pid file')
    monkeypatch.setattr(server, '_load_session_registry', broken)
    assert server._archive_overlay_claude_registry_sessions([]) == []
