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
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_CTX_LINKS', collections.OrderedDict())
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_DIGESTS', {})
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', tmp_path / 'archive-cache.json')
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_NONCE', 'aaaaaaaa')
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_LINEAGE_LOADER', None)
    ideas = {}
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: dict(ideas))
    monkeypatch.setattr(server, '_load_usage_limit_resumes', lambda: {})
    monkeypatch.setattr(list_lineage, '_loaded', {})
    monkeypatch.setattr(list_lineage, '_ctx_refs', {})
    monkeypatch.setattr(list_lineage, '_blob_touched', {})
    monkeypatch.setattr(list_lineage, '_blob_cache', {})
    monkeypatch.setattr(list_lineage, '_saved', dict(list_lineage._saved, sig=None))
    server._test_ideas = ideas
    yield server
    del server._test_ideas


def _restart(server, monkeypatch, rows, nonce='bbbbbbbb'):
    """A fresh process: new nonce, empty caches, same saved files."""
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_NONCE', nonce)
    server._archive_serve_cache.clear()
    server._ARCHIVE_DELTA_CTX_HIST.clear()
    server._ARCHIVE_DELTA_CTX_LINKS.clear()
    server._ARCHIVE_DELTA_DIGESTS.clear()
    list_lineage._loaded.clear()
    list_lineage._blob_cache.clear()
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


def test_context_is_saved_once_as_shared_blobs(srv, monkeypatch):
    now = time.time()
    srv._test_ideas['a'] = {'ref': 'IDEAS-1', 'note': 'x' * 5000}
    _, state = _publish(srv, [_row('a', 'A', now), _row('b', 'B', now)])
    token = state['token']
    assert list_lineage.save(srv, force=True)
    folder = list_lineage.directory(srv)
    blobs = sorted(p.name for p in (folder / 'blobs').iterdir())
    assert blobs and all(len(name) == 40 for name in blobs)
    # The ideas text lives in a blob, not in the per-save lineage file.
    assert 'IDEAS-1' not in (folder / 'aaaaaaaa.json').read_text()
    stamps = {p.name: p.stat().st_mtime_ns for p in (folder / 'blobs').iterdir()}

    _publish(srv, [_row('a', 'A', now), _row('b', 'B2', now)])
    assert list_lineage.save(srv, force=True)
    assert {p.name: p.stat().st_mtime_ns for p in (folder / 'blobs').iterdir()} == stamps

    base, state = _restart(srv, monkeypatch, [_row('a', 'A', now), _row('b', 'B2', now)])
    delta = srv._archive_list_delta_payload(state, token, base, window='7d')
    assert [r['session_id'] for r in delta['conversations']] == ['b']

    # A pruned or tampered blob means the ctx can't vouch: full body.
    victim = folder / 'blobs' / blobs[0]
    victim.write_text('tampered')
    list_lineage._loaded.clear(); list_lineage._blob_cache.clear()
    assert srv._archive_list_delta_reject(state, token) == 'ctx'


def test_prune_ages_out_old_blobs(srv):
    folder = list_lineage.directory(srv) / 'blobs'
    folder.mkdir(parents=True)
    old, fresh = folder / ('0' * 40), folder / ('1' * 40)
    old.write_text('x'); fresh.write_text('y')
    past = time.time() - list_lineage.MAX_AGE_S - 60
    os.utime(old, (past, past))
    list_lineage.prune(srv)
    assert not old.exists() and fresh.exists() and folder.is_dir()


def test_reader_child_waits_for_its_first_hydrated_reload():
    from ccc_server import list_reader
    import threading
    flag = threading.Event()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(list_reader, '_hydrated', flag)
        assert list_reader.wait_hydrated(0.01) is False
        assert list_reader.wait_hydrated(-5) is False  # deadline already past
        flag.set()
        assert list_reader.wait_hydrated(0) is True


def test_stale_inputs_body_catches_up_with_an_exact_delta(srv, monkeypatch, tmp_path):
    # CCC-704: a page load after a ticket change (new ctx) and a newer
    # snapshot gets the previous body at once; its token then yields a delta
    # with exactly the rows that moved, never another full body.
    monkeypatch.setattr(srv, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(srv, '_ARCHIVE_LIST_BODY_CACHE', srv.ByteBudgetCache(1 << 20, 8))
    monkeypatch.setattr(srv, '_ARCHIVE_LIST_LATEST', {})
    monkeypatch.setattr(srv, '_ARCHIVE_LIST_STALE_OK', lambda reason, slot: reason == 'inputs')
    key = srv._archive_response_cache_key()
    now = time.time()

    def page_load(base, state):
        detail = {}
        snap_ver = (state['ver'], state['ctx'])

        def build():
            return {'conversations': list(base), 'delta_token': state['token']}
        raw, _, _ = srv._archive_list_body_get_or_build(
            key, '7d', True, build, snap_ver, diagnostics=detail, allow_stale=True,
            stale_base=srv._archive_list_stale_base(state['ver'], state),
            stale_inputs=srv._archive_list_stale_inputs(state))
        return json.loads(raw), detail['reason']

    base, first = _publish(srv, [_row('a', 'A', now), _row('b', 'B', now), _row('c', 'C', now)])
    body, reason = page_load(base, first)
    assert reason == 'version-miss' and body['delta_token'] == first['token']

    srv._test_ideas['c'] = {'ref': 'CCC-1'}                      # new ctx
    base, second = _publish(srv, [_row('a', 'A2', now), _row('b', 'B', now), _row('c', 'C', now)])
    assert second['ctx'] != first['ctx'] and second['ver'] > first['ver']
    body, reason = page_load(base, second)
    assert reason == 'stale-inputs' and body['delta_token'] == first['token']

    # The page's follow-up read with the stand-in body's token.
    assert srv._archive_list_delta_reject(second, first['token']) is None
    delta = srv._archive_list_delta_payload(second, first['token'], base, window='7d')
    assert delta['delta'] is True
    assert sorted(r['session_id'] for r in delta['conversations']) == ['a', 'c']

    # Once that token's context is forgotten, the page load is rebuilt.
    srv._ARCHIVE_DELTA_CTX_HIST.pop(first['ctx'])
    assert srv._archive_list_delta_reject(second, first['token']) == 'ctx'
    body, reason = page_load(base, second)
    assert reason == 'version-miss' and body['delta_token'] == second['token']


def _churn_context(srv, rows, changes):
    """Mint one new ctx per change, with other windows reading in between,
    the way idea-ticket and usage-limit updates churn the live history."""
    state = None
    for i, sid in enumerate(changes):
        srv._test_ideas[sid] = {'ref': f'CCC-{i}'}
        base, state = _publish(srv, [dict(r) for r in rows])
        key = srv._archive_response_cache_key()
        for window in ('8h', '1d'):
            srv._archive_list_delta_state(key, base, window, True)
    return base, state


def test_forgotten_context_resolves_through_links(srv, monkeypatch):
    # CCC-709: a phone reloaded an hour later with a 7d token whose ctx had
    # left the 8-entry history and paid a 633 KB full body (7.9 s).
    now = time.time()
    rows = [_row('a', 'A', now), _row('b', 'B', now), _row('c', 'C', now)]
    _, first = _publish(srv, [dict(r) for r in rows])
    base, state = _churn_context(srv, rows, ['c'] * 6 + ['b'] + ['c'] * 6)
    assert first['ctx'] not in srv._ARCHIVE_DELTA_CTX_HIST
    assert first['ctx'] in srv._ARCHIVE_DELTA_CTX_LINKS
    # The newest entry of every scope stays in full.
    scopes = {e['scope'][1] for e in srv._ARCHIVE_DELTA_CTX_HIST.values()}
    assert scopes == {'7d', '1d', '8h'}

    assert srv._archive_list_delta_reject(state, first['token']) is None
    delta = srv._archive_list_delta_payload(state, first['token'], base, window='7d')
    assert delta['delta'] is True
    assert sorted(r['session_id'] for r in delta['conversations']) == ['b', 'c']

    # The links travel with the lineage to the next reader process.
    assert list_lineage.save(srv, force=True)
    base, state = _restart(srv, monkeypatch, [dict(r) for r in rows])
    assert srv._archive_list_delta_reject(state, first['token']) is None
    delta = srv._archive_list_delta_payload(state, first['token'], base, window='7d')
    assert sorted(r['session_id'] for r in delta['conversations']) == ['b', 'c']


def test_ctx_links_never_cross_scopes_or_loop(srv):
    entry = lambda scope: {'scope': scope, 'parents': {}, 'resumes': '{}',
                           'ideas': '{}', 'titles': '{}'}
    entries = {'cur': entry(('k', '7d', 1))}
    links = {'old': (('k', '7d', 1), 'mid', frozenset({'x'})),
             'mid': (('k', '7d', 1), 'cur', frozenset({'y'}))}
    resolve = srv._archive_delta_ctx_resolve
    assert resolve('old', entries, links) == (entries['cur'], {'x', 'y'})
    links['mid'] = (('k', '1d', 1), 'cur', frozenset())
    assert resolve('old', entries, links) is None
    links['mid'] = (('k', '7d', 1), 'old', frozenset())
    assert resolve('old', entries, links) is None
    assert resolve('unknown', entries, links) is None
