"""Delta selection retains ancestors without repeating window eligibility work."""
import pytest

import server


@pytest.mark.parametrize('window,seconds', [('8h', 28800), ('1d', 86400), ('7d', 604800)])
def test_delta_resends_only_context_ancestors_and_changed_rows(monkeypatch, window, seconds):
    now = 2000000
    rows = [
        {'session_id': 'parent', 'mtime': 1, 'parent_session_id': 'grandparent'},
        {'session_id': 'grandparent', 'mtime': 1, 'parent_session_id': 'parent'},
        {'session_id': 'child', 'mtime': now, 'parent_session_id': 'parent'},
        {'session_id': 'pinned', 'mtime': 1, 'pinned': True},
        {'session_id': 'import', 'mtime': 1, 'engine': 'hermes'},
        {'session_id': 'moved', 'mtime': 1},
        {'session_id': 'unrelated', 'mtime': 1},
        {'session_id': 'boundary', 'mtime': now - seconds},
    ]
    ideas = {'moved': {'moved_at': now}}
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: ideas)
    monkeypatch.setattr(server._session_graph, 'parent_snapshot', lambda: {})
    # Capture actual selected rows without topic persistence or ticket writes.
    monkeypatch.setattr(server, '_archive_list_project_rows', lambda selected, **kw: selected)
    monkeypatch.setattr(server, '_queue_health_stats', lambda *a, **kw: {})
    state = dict(nonce='test', ver=2, ctx='context', track_since=1,
                 row_rev={'child': 2}, base=rows, extra=[])
    token = 'test.1.context.0'
    calls = []
    allows = server._archive_list_window_allows_row

    def counted(row, cutoff, ideas, **kwargs):
        calls.append(row['session_id'])
        return allows(row, cutoff, ideas, **kwargs)

    monkeypatch.setattr(server, '_archive_list_window_allows_row', counted)
    result = server._archive_list_delta_payload(state, token, rows, window=window, now=now)
    assert result['ids'] == ['child', 'pinned', 'import', 'moved', 'boundary',
                             'parent', 'grandparent']
    assert [r['session_id'] for r in result['conversations']] == ['child', 'parent', 'grandparent']
    assert calls == [r['session_id'] for r in rows]

    # A parent moving into the window ceases to be context-only immediately.
    ideas['parent'] = {'moved_at': now}
    result = server._archive_list_delta_payload(state, token, rows, window=window, now=now)
    assert [r['session_id'] for r in result['conversations']] == ['child', 'grandparent']
    # Expiry drops direct activity and its context-only ancestors together.
    result = server._archive_list_delta_payload(state, token, rows, window=window,
                                               now=now + seconds + 1)
    assert result['ids'] == ['pinned', 'import']


def test_all_window_does_not_resend_unchanged_ancestors(monkeypatch):
    rows = [{'session_id': 'parent', 'mtime': 1},
            {'session_id': 'child', 'mtime': 2, 'parent_session_id': 'parent'}]
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: {})
    monkeypatch.setattr(server._session_graph, 'parent_snapshot', lambda: {})
    monkeypatch.setattr(server, '_archive_list_project_rows', lambda selected, **kw: selected)
    monkeypatch.setattr(server, '_queue_health_stats', lambda *a, **kw: {})
    state = dict(nonce='test', ver=2, ctx='context', track_since=1,
                 row_rev={}, base=rows, extra=[])
    result = server._archive_list_delta_payload(state, 'test.1.context.0', rows, window='all')
    assert result['ids'] == ['parent', 'child']
    assert result['conversations'] == []
