"""Reuse selection positions without retaining stale projected sidebar state."""
import copy

import pytest

import server
from ccc_server.bounded_cache import ByteBudgetCache


@pytest.fixture(autouse=True)
def selection_cache(monkeypatch):
    cache = ByteBudgetCache(8 * 1024 * 1024, 8)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_INITIAL_SELECTION_CACHE', cache)
    return cache


def select(rows, *, window='all', now=1000000, parents=None, ideas=None, version=1):
    return server._archive_list_initial_selection(
        rows, window, now, parents or {}, ideas or {}, version)


def ids(result):
    return [r['session_id'] for r in result[0]]


def test_positions_reuse_current_objects_and_keep_pins_ancestors(selection_cache):
    rows = [{'session_id': str(i), 'mtime': i} for i in range(600)]
    rows[0]['pinned'] = True
    rows[-1]['parent_session_id'] = '1'
    rows[1]['parent_session_id'] = '2'
    rows[2]['parent_session_id'] = '1'
    first = select(rows)
    current = copy.deepcopy(rows)
    second = select(current)
    assert first[1:] == (600, False)
    assert second[1:] == (600, True)
    assert ids(first) == ids(second)
    assert set(ids(second)) == {str(i) for i in range(100, 600)} | {'0', '1', '2'}
    assert all(any(r is source for source in current) for r in second[0])
    entry = next(iter(selection_cache._items.values()))[0]
    assert all(type(i) is int for i in entry[0])


@pytest.mark.parametrize('window,seconds', [('8h', 28800), ('1d', 86400), ('7d', 604800)])
def test_window_boundary_clock_rewind_and_ancestor_fallout(window, seconds):
    now = 1000000
    rows = [{'session_id': 'parent', 'mtime': 1},
            {'session_id': 'child', 'mtime': now - seconds + 1,
             'parent_session_id': 'parent'},
            {'session_id': 'pinned', 'mtime': 1, 'pinned': True},
            {'session_id': 'hermes', 'mtime': 1, 'engine': 'hermes'}]
    assert set(ids(select(rows, window=window))) == {'parent', 'child', 'pinned', 'hermes'}
    assert select(rows, window=window, now=now + .5)[2] is True
    # Equality remains included, but reuse stops at the boundary.
    assert 'child' in ids(select(rows, window=window, now=now + 1))
    after = select(rows, window=window, now=now + 1.01)
    assert set(ids(after)) == {'pinned', 'hermes'}
    rewind = select(rows, window=window, now=now)
    assert rewind[2] is False
    assert 'child' in ids(rewind) and 'parent' in ids(rewind)


def test_ticket_activity_and_archive_and_parent_changes_invalidate():
    rows = [{'session_id': 'old', 'mtime': 1},
            {'session_id': 'new', 'mtime': 1000000}]
    assert ids(select(rows, window='8h')) == ['new']
    assert ids(select(rows, window='8h', ideas={'old': {'moved_at': 1000000}})) == ['old', 'new']
    assert set(ids(select(rows, window='8h', parents={'new': 'old'}))) == {'old', 'new'}
    removed = select(rows, window='8h', parents={'new': 'old'},
                     ideas={'old': {'explicitly_archived': True}})
    assert ids(removed) == ['new'] and removed[2] is False
    assert select(rows, window='8h', parents={'new': 'old'},
                  ideas={'old': {'explicitly_archived': True}})[2] is True


def test_cached_selection_expires_at_external_ticket_activity_boundary():
    now = 1000000
    rows = [{'session_id': 'old', 'mtime': 1}]
    ideas = {'old': {'moved_at': now - 28800 + 2}}
    assert ids(select(rows, window='8h', now=now, ideas=ideas)) == ['old']
    inside = select(rows, window='8h', now=now + 1, ideas=ideas)
    assert ids(inside) == ['old'] and inside[2] is True
    after = select(rows, window='8h', now=now + 3, ideas=ideas)
    assert ids(after) == [] and after[2] is False


def test_version_and_scope_and_missing_version_invalidate():
    rows = [{'session_id': 'a', 'mtime': 1}]
    assert select(rows)[2] is False
    assert select(rows)[2] is True
    changed = [{'session_id': 'b', 'mtime': 1000000}]
    assert ids(select(changed, version=2)) == ['b']
    assert select(changed, version=2, window='8h')[2] is False
    for version in (None, 0):
        assert select(changed, version=version)[2] is False
        assert select(changed, version=version)[2] is False


def test_mixed_source_and_duplicate_references_preserve_selection():
    row = {'session_id': 'same', 'mtime': 5}
    rows = [None, row, 'invalid', row]
    assert select(rows)[1:] == (2, False)
    assert select(rows)[1:] == (2, True)
    assert ids(select(rows)) == ['same', 'same']


def test_cache_bounds_and_oversized_guard_bypass(monkeypatch):
    cache = ByteBudgetCache(2000, 2)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_INITIAL_SELECTION_CACHE', cache)
    for version in range(1, 10):
        select([{'session_id': 'a'}], version=version)
        assert cache.bytes <= 2000 and len(cache) <= 2
    parents = {str(i): str(i + 1) for i in range(1000)}
    assert select([], parents=parents)[2] is False
    assert select([], parents=parents)[2] is False
    assert cache.bytes <= 2000


def test_hit_still_projects_current_state_and_queue_health(monkeypatch):
    parents, tickets, resumes = {}, {}, {}
    monkeypatch.setattr(server._session_graph, 'parent_snapshot', lambda: parents)
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: tickets)
    monkeypatch.setattr(server, '_load_usage_limit_resumes', lambda: resumes)
    projected, health = [], []
    def project(rows, **kw):
        projected.append(kw['_inputs'])
        return [dict(r, title=resumes.get('title')) for r in kw['_inputs'][3]]
    monkeypatch.setattr(server, '_archive_list_project_rows', project)
    monkeypatch.setattr(server, '_queue_health_stats', lambda rows, **kw: health.append(kw['now']) or {'now': kw['now']})
    monkeypatch.setattr(server, '_trace_row_flicker', lambda *a, **kw: None)
    rows = [{'session_id': 'a'}]
    one = server._archive_list_initial_payload(rows, selection_version=1, now=1000)
    resumes['title'] = 'current'
    phases = {}
    two = server._archive_list_initial_payload(rows, selection_version=1, now=1001, phases=phases)
    assert phases['selection']['hit'] is True
    assert one['conversations'][0]['title'] is None
    assert two['conversations'][0]['title'] == 'current'
    assert two['queue_health'] == {'now': 1001}
    assert len(projected) == len(health) == 2


@pytest.mark.parametrize('stale_ok,source_version,expected', [('1', 7, 7), ('0', 7, None), ('1', 0, 0)])
def test_http_uses_version_only_for_stale_allowed_sources(monkeypatch, stale_ok, source_version, expected):
    import json
    import threading
    import urllib.request
    seen = []
    monkeypatch.setattr(server, '_archive_list_source_rows_cached',
                        lambda *a, **kw: ([], True, source_version))
    def payload(rows, **kw):
        seen.append(kw['selection_version'])
        return {'ok': True, 'complete': True}
    monkeypatch.setattr(server, '_archive_list_initial_payload', payload)
    monkeypatch.setattr(server, 'record_diag', lambda *a, **kw: None)
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'http://127.0.0.1:{httpd.server_port}/api/conversations/list?initial=1&stale_ok={stale_ok}'
        with urllib.request.urlopen(url, timeout=5) as response:
            assert json.load(response)['complete'] is True
        assert seen == [expected]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_initial_recent_topic_retains_old_children_beyond_recent_500():
    rows = [{'session_id': str(i), 'mtime': i} for i in range(600)]
    rows[0]['parent_session_id'] = '599'
    rows[1]['parent_session_id'] = '0'
    result = select(rows)
    assert {'599', '0', '1'} <= set(ids(result))
    assert len(result[0]) == 502


def test_full_seven_day_selection_reuses_only_matching_preview():
    now = 1000000
    cutoff = now - 604800
    rows = [{'session_id': 'parent', 'mtime': 1},
            {'session_id': 'child', 'mtime': cutoff + 1},
            {'session_id': 'archived', 'mtime': 1},
            {'session_id': 'kept', 'mtime': now}]
    parents = {'child': 'parent', 'kept': 'archived'}
    ideas = {'archived': {'explicitly_archived': True}}
    version = ('default', 1)

    def full(source, at, graph=parents, tickets=ideas, tag=version):
        phases = {}
        selected = server._archive_list_full_selection(
            source, '7d', at, graph, tickets, tag, phases)
        return [row['session_id'] for row in selected], phases['window_selection']['hit']

    assert full(rows, now) == (['child', 'kept', 'parent'], False)
    select(rows, window='7d', now=now, parents=parents, ideas=ideas, version=version)
    current = copy.deepcopy(rows)
    assert full(current, now + .5) == (['child', 'kept', 'parent'], True)
    assert full(current, now + 2) == (['kept'], False)
    assert full(current, now - 1) == (['child', 'kept', 'parent'], False)
    assert full(current, now, graph={}) == (['child', 'kept'], False)
    assert full(current, now, tickets={}) == (['child', 'kept', 'parent', 'archived'], False)
    assert full(current, now, tag=('other', 1)) == (['child', 'kept', 'parent'], False)
    assert full(current, now, tag=('default', 2)) == (['child', 'kept', 'parent'], False)


def test_full_seven_day_selection_reuses_membership_before_preview_finishes(monkeypatch):
    now = 1000000
    version = ('default', 1)
    rows = [{'session_id': str(i), 'mtime': now - 600 + i} for i in range(600)]
    rows[0]['mtime'] = 1
    rows[-1]['parent_session_id'] = '0'
    family_rows = server._archive_list_family_rows
    observed = []

    def check_during_preview(source, selected, ideas, parents, **kwargs):
        if len(selected) == 500:
            current = copy.deepcopy(rows)
            phases = {}
            full = server._archive_list_full_selection(
                current, '7d', now, parents, ideas, version, phases)
            observed.append(phases['window_selection']['hit'])
            assert len(full) == 600
            assert all(row is expected for row, expected in zip(full, current[1:] + current[:1]))
        return family_rows(source, selected, ideas, parents, **kwargs)

    monkeypatch.setattr(server, '_archive_list_family_rows', check_during_preview)
    preview = select(rows, window='7d', now=now, version=version)
    assert observed == [True]
    assert ids(preview) == [str(i) for i in range(100, 600)] + ['0']
    assert select(rows, window='7d', now=now, version=version)[2] is True
