"""Sidebar delta tokens survive a conv_reader restart (CCC-702)."""

import collections
import importlib
import json
import os
import time

import pytest

from ccc_server import list_lineage


def _row(sid, title, ts):
    return {'session_id': sid, 'title': title, 'modified': ts}


@pytest.fixture
def srv(monkeypatch, tmp_path):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_CTX_HIST', collections.OrderedDict())
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_DIGESTS', {})
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', tmp_path / 'archive-cache.json')
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_NONCE', 'aaaaaaaa')
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_LINEAGE_LOADER', None)
    ideas = {}
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: dict(ideas))
    monkeypatch.setattr(server, '_load_usage_limit_resumes', lambda: {})
    monkeypatch.setattr(list_lineage, '_loaded', {})
    monkeypatch.setattr(list_lineage, '_saved', dict(list_lineage._saved, sig=None))
    server._test_ideas = ideas
    yield server
    del server._test_ideas


def _restart(server, monkeypatch, rows, nonce='bbbbbbbb'):
    """A fresh process: new nonce, empty caches, same saved files."""
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_NONCE', nonce)
    server._archive_serve_cache.clear()
    server._ARCHIVE_DELTA_CTX_HIST.clear()
    server._ARCHIVE_DELTA_DIGESTS.clear()
    list_lineage._loaded.clear()
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_LINEAGE_LOADER',
                        lambda n: list_lineage.load(server, n))
    key = server._archive_response_cache_key()
    server._archive_serve_cache_store(key, rows, server._archive_serve_generation)
    base = server._archive_serve_cache[key]['rows']
    return base, server._archive_list_delta_state(key, base, '7d', True)


def _publish(server, rows):
    key = server._archive_response_cache_key()
    server._archive_serve_cache_store(key, rows, server._archive_serve_generation)
    base = server._archive_serve_cache[key]['rows']
    return base, server._archive_list_delta_state(key, base, '7d', True)


def test_old_token_gets_content_verified_delta_after_restart(srv, monkeypatch):
    now = time.time()
    rows = [_row('a', 'A', now), _row('b', 'B', now), _row('c', 'C', now), _row('gone', 'G', now)]
    _, state = _publish(srv, [dict(r) for r in rows])
    token = state['token']
    assert list_lineage.save(srv, force=True)

    # The next process: 'b' changed, 'gone' was removed, 'new' appeared.
    after = [_row('a', 'A', now), _row('b', 'B2', now), _row('c', 'C', now), _row('new', 'N', now)]
    base, state = _restart(srv, monkeypatch, after)
    assert state['nonce'] == 'bbbbbbbb' and not token.startswith('bbbbbbbb')
    assert srv._archive_list_delta_reject(state, token) is None
    delta = srv._archive_list_delta_payload(state, token, base, window='7d')
    assert delta['delta'] is True
    assert delta['ids'] == ['a', 'b', 'c', 'new']
    assert sorted(r['session_id'] for r in delta['conversations']) == ['b', 'new']


def test_rows_changed_after_the_token_in_the_old_process_are_resent(srv, monkeypatch):
    now = time.time()
    _, first = _publish(srv, [_row('a', 'A', now), _row('b', 'B', now)])
    old_token = first['token']
    # Still the old process: 'a' changes after the client's token.
    _, second = _publish(srv, [_row('a', 'A2', now), _row('b', 'B', now)])
    assert list_lineage.save(srv, force=True)

    base, state = _restart(srv, monkeypatch, [_row('a', 'A2', now), _row('b', 'B', now)])
    delta = srv._archive_list_delta_payload(state, old_token, base, window='7d')
    assert [r['session_id'] for r in delta['conversations']] == ['a']
    # The newest old token already holds 'a'.
    delta = srv._archive_list_delta_payload(state, second['token'], base, window='7d')
    assert delta['conversations'] == []


def test_context_inputs_that_moved_across_the_restart_are_resent(srv, monkeypatch):
    now = time.time()
    rows = [_row('a', 'A', now), _row('b', 'B', now)]
    _, state = _publish(srv, [dict(r) for r in rows])
    token = state['token']
    assert list_lineage.save(srv, force=True)

    srv._test_ideas['b'] = {'ref': 'IDEAS-2'}
    base, state = _restart(srv, monkeypatch, [dict(r) for r in rows])
    delta = srv._archive_list_delta_payload(state, token, base, window='7d')
    assert [r['session_id'] for r in delta['conversations']] == ['b']


def test_full_body_when_the_old_lineage_cannot_vouch(srv, monkeypatch):
    now = time.time()
    rows = [_row('a', 'A', now)]
    _, state = _publish(srv, [dict(r) for r in rows])
    saved_token = state['token']
    assert list_lineage.save(srv, force=True)
    # Minted after the last save: the new process can't know those rows.
    _, later = _publish(srv, [_row('a', 'A2', now)])
    nonce, ver, ctx, ovl = saved_token.split('.')

    base, state = _restart(srv, monkeypatch, [dict(r) for r in rows])
    reject = srv._archive_list_delta_reject
    assert reject(state, later['token']) == 'range'
    assert reject(state, '.'.join([nonce, ver, 'f' * 12, ovl])) == 'ctx'
    assert reject(state, '.'.join(['cccccccc', ver, ctx, ovl])) == 'nonce'
    # A traversal attempt never reaches the filesystem.
    assert list_lineage.load(srv, '../../etc') is None
    assert reject(state, '.'.join(['a/bb/ccc', ver, ctx, ovl])) == 'nonce'
    # Projection code changed between the processes.
    path = list_lineage.directory(srv) / f'{nonce}.json'
    data = json.loads(path.read_text())
    data['fingerprint'] = '0' * 16
    path.write_text(json.dumps(data))
    os.utime(path, ns=(time.time_ns() + 10**9,) * 2)
    assert reject(state, saved_token) == 'nonce'


def test_hand_off_save_extends_the_range_seen_by_the_next_process(srv, monkeypatch):
    # The previous child keeps answering (SO_REUSEPORT) after the next one
    # started; its final save on hand-off is re-read by stat signature.
    now = time.time()
    _, first = _publish(srv, [_row('a', 'A', now)])
    assert list_lineage.save(srv, force=True)
    _, later = _publish(srv, [_row('a', 'A2', now)])
    old_nonce = srv._ARCHIVE_DELTA_NONCE
    old_cache = {k: dict(v) for k, v in srv._archive_serve_cache.items()}
    old_hist = collections.OrderedDict(srv._ARCHIVE_DELTA_CTX_HIST)

    base, state = _restart(srv, monkeypatch, [_row('a', 'A2', now)])
    assert srv._archive_list_delta_reject(state, later['token']) == 'range'

    # The old child's hand-off save (simulated with its own state).
    new_state = (srv._ARCHIVE_DELTA_NONCE, dict(srv._archive_serve_cache),
                 collections.OrderedDict(srv._ARCHIVE_DELTA_CTX_HIST))
    monkeypatch.setattr(srv, '_ARCHIVE_DELTA_NONCE', old_nonce)
    srv._archive_serve_cache.clear(); srv._archive_serve_cache.update(old_cache)
    srv._ARCHIVE_DELTA_CTX_HIST.clear(); srv._ARCHIVE_DELTA_CTX_HIST.update(old_hist)
    path = list_lineage.directory(srv) / f'{old_nonce}.json'
    before = path.stat().st_mtime_ns
    assert list_lineage.save(srv, force=True)
    os.utime(path, ns=(before + 10**9,) * 2)
    monkeypatch.setattr(srv, '_ARCHIVE_DELTA_NONCE', new_state[0])
    srv._archive_serve_cache.clear(); srv._archive_serve_cache.update(new_state[1])
    srv._ARCHIVE_DELTA_CTX_HIST.clear(); srv._ARCHIVE_DELTA_CTX_HIST.update(new_state[2])

    assert srv._archive_list_delta_reject(state, later['token']) is None
    delta = srv._archive_list_delta_payload(state, later['token'], base, window='7d')
    assert delta['conversations'] == []


def test_disk_body_does_not_preempt_an_adoptable_token(srv, monkeypatch):
    now = time.time()
    _, state = _publish(srv, [_row('a', 'A', now)])
    token = state['token']
    assert list_lineage.save(srv, force=True)
    _restart(srv, monkeypatch, [_row('a', 'A', now)])
    loads = []
    monkeypatch.setattr(srv, '_archive_list_disk_load', lambda *a, **k: loads.append(a))
    monkeypatch.setattr(srv, '_ARCHIVE_LIST_BUILT', set())
    assert srv._archive_list_disk_candidate('k', '7d', token, now=srv._SERVER_START_TS) is None
    srv._archive_list_disk_candidate('k', '7d', 'cccccccc.1.x.0', now=srv._SERVER_START_TS)
    assert loads  # an unknown foreign token still gets the saved body


def test_fingerprint_follows_projection_code_only(srv, monkeypatch):
    list_lineage._fingerprint.clear()
    base = list_lineage.fingerprint(srv)
    list_lineage._fingerprint.clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(srv, '_archive_list_project_row', lambda row, **kw: dict(row))
        assert list_lineage.fingerprint(srv) != base
    list_lineage._fingerprint.clear()
    assert list_lineage.fingerprint(srv) == base
    list_lineage._fingerprint.clear()
