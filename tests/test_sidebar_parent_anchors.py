"""A short archive window must still deliver a recent lane's topic."""
import importlib


def test_window_keeps_loaded_ancestors_and_graph_only_links():
    server = importlib.import_module('server')
    rows = [
        {'session_id': 'topic', 'modified': 1},
        {'session_id': 'parent', 'parent_session_id': 'topic', 'modified': 2},
        {'session_id': 'agent-test', 'id': 'parent:agent-test', 'modified': 100},
        {'session_id': 'unrelated', 'modified': 1},
    ]
    result = server._archive_list_window_rows(rows, 50, {}, {'agent-test': 'parent'})
    assert [r['session_id'] for r in result] == ['agent-test', 'parent', 'topic']
    assert len(rows) == 4


def test_removed_topics_and_cycles_do_not_expand_the_window():
    server = importlib.import_module('server')
    child = {'session_id': 'child', 'parent_session_id': 'parent', 'modified': 100}
    parent = {'session_id': 'parent', 'parent_session_id': 'child', 'modified': 1}
    for removal in [{'trashed': True}, {'explicitly_archived': True}]:
        assert server._archive_list_window_rows([child, dict(parent, **removal)], 50, {}, {}) == [child]
    assert len(server._archive_list_window_rows([child, parent], 50, {}, {})) == 2
    tickets = {'parent': {'explicitly_archived': True}}
    assert server._archive_list_window_rows([child, parent], 50, tickets, {}) == [child]


def test_delta_retains_unchanged_parent_and_sends_it_with_new_child(monkeypatch):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_archive_list_delta_reject', lambda *a: None)
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: {})
    monkeypatch.setattr(server, '_archive_list_project_rows', lambda rows, **kw: rows)
    old_parent = {'session_id': 'parent', 'modified': 1}
    child = {'session_id': 'child', 'parent_session_id': 'parent', 'modified': 100000}
    state = {'ctx': 'ctx', 'row_rev': {'child': 2}, 'track_since': 1,
             'base': [old_parent, child], 'extra': []}
    result = server._archive_list_delta_payload(
        state, 'nonce.1.ctx.0', state['base'], window='8h', now=100000)
    assert result['ids'] == ['child', 'parent']
    assert {r['session_id'] for r in result['conversations']} == {'child', 'parent'}


def test_visible_topic_keeps_old_lanes_continuations_and_graph_descendants():
    server = importlib.import_module('server')
    rows = [
        {'session_id': 'head', 'continued_from_session_id': 'origin', 'modified': 100},
        {'session_id': 'origin', 'modified': 1},
        {'session_id': 'child', 'parent_session_id': 'origin', 'modified': 2},
        {'session_id': 'nested', 'modified': 3},
        {'session_id': 'unrelated', 'modified': 1},
        {'session_id': 'removed', 'parent_session_id': 'head', 'modified': 1, 'trashed': True},
        {'session_id': 'archived', 'parent_session_id': 'head', 'modified': 1},
    ]
    context = set()
    result = server._archive_list_window_rows(rows, 50,
        {'archived': {'explicitly_archived': True}}, {'nested': 'child'}, ancestor_ids=context)
    assert {r['session_id'] for r in result} == {'head', 'origin', 'child', 'nested'}
    assert context == {id(rows[i]) for i in (1, 2, 3)}
    assert server._archive_list_window_rows(rows, 200, {}, {}) == []


def test_delta_resends_unchanged_old_children_of_newly_visible_parent(monkeypatch):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_archive_list_delta_reject', lambda *a: None)
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: {})
    monkeypatch.setattr(server, '_archive_list_project_rows', lambda rows, **kw: rows)
    parent = {'session_id': 'parent', 'modified': 100000}
    child = {'session_id': 'child', 'parent_session_id': 'parent', 'modified': 1}
    state = {'ctx': 'ctx', 'row_rev': {'parent': 2}, 'track_since': 1,
             'base': [parent, child], 'extra': []}
    result = server._archive_list_delta_payload(
        state, 'nonce.1.ctx.0', state['base'], window='8h', now=100000)
    assert result['ids'] == ['parent', 'child']
    assert {r['session_id'] for r in result['conversations']} == {'parent', 'child'}
