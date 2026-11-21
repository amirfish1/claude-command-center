"""Explain real topic-cache misses without leaking their dependency values."""
import copy
import json
import sqlite3

import pytest

import server
from ccc_server import continuation_topics as topics


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / 'topics.sqlite3')
    conn.execute('CREATE TABLE session_meta (sid TEXT PRIMARY KEY, continuation_origin TEXT)')
    monkeypatch.setattr(topics._sg, '_get_connection', lambda: conn)
    monkeypatch.setattr(server, '_load_session_name_overrides', lambda: {})
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {}})
    monkeypatch.setattr(topics, '_TOPIC_CACHE', {'token': None, 'entries': {}})
    yield conn
    conn.close()


def warm(rows):
    topics.attach_topics(rows, names={})
    topics.attach_topics(rows, names={})


def test_diagnostics_preserve_topic_projection_and_hide_values(store):
    rows = [{'session_id': 'private-root', 'display_name': 'PRIVATE TITLE'},
            {'session_id': 'private-child', 'continued_from_session_id': 'private-root'}]
    warm(rows)
    expected = copy.deepcopy(rows)
    detail = {}
    topics.attach_topics(rows, names={}, diagnostics=detail)
    assert rows == expected
    assert detail == {'hit': True, 'reason': 'hit'}
    assert 'private' not in json.dumps(detail).lower()


def test_database_edit_rejects_cache_and_returns_current_title(store):
    rows = [{'session_id': 'root', 'display_name': 'BEFORE'},
            {'session_id': 'child', 'continued_from_session_id': 'root'}]
    warm(rows)
    store.execute("UPDATE continuation_topics SET topic_title='AFTER' WHERE sid='root'")
    store.commit()
    detail = {}
    topics.attach_topics(rows, names={}, diagnostics=detail)
    assert detail == {'hit': False, 'reason': 'database'}
    assert rows[1]['continuation_topic_title'] == 'AFTER'


def test_metadata_title_discovery_names_only_the_changed_dependency(store):
    warm([{'session_id': 'private-root'}])
    server._conv_meta_cache['private-root.jsonl'] = {'custom_title': 'PRIVATE TITLE'}
    detail = {}
    topics.attach_topics([{'session_id': 'private-root'}], names={}, diagnostics=detail)
    assert detail == {'hit': False, 'reason': 'title-discovered'}
    assert 'private' not in json.dumps(detail).lower()
    assert store.execute("SELECT topic_title FROM continuation_topics WHERE sid='private-root'").fetchone()[0] == 'PRIVATE TITLE'


def test_new_row_is_not_misreported_as_database_invalidation(store):
    warm([{'session_id': 'existing'}])
    detail = {}
    topics.attach_topics([{'session_id': 'existing'}, {'session_id': 'new'}],
                         names={}, diagnostics=detail)
    assert detail == {'hit': False, 'reason': 'new-session'}


def test_small_cache_miss_keeps_established_entries_after_fallback_churn(store):
    rows = [{'session_id': 'root', 'display_name': 'ROOT: task'},
            {'session_id': 'child', 'continued_from_session_id': 'root'},
            {'session_id': 'untitled'}]
    # A standalone session can already be indexed without having appeared
    # in this dashboard's full projection.
    topics._connection(store)
    store.execute("INSERT INTO continuation_topics VALUES ('other', '', 'other', 'OTHER', 0)")
    store.commit()
    warm(rows)
    expected = copy.deepcopy(rows)

    server._conv_meta_cache['unrelated.jsonl'] = {'custom_title': 'Unrelated'}
    topics.attach_topics([{'session_id': 'other'}], names={})
    detail = {}
    topics.attach_topics(rows, names={}, diagnostics=detail)

    assert detail == {'hit': True, 'reason': 'hit'}
    assert rows == expected

    server._conv_meta_cache['untitled.jsonl'] = {'custom_title': 'FOUND: task'}
    topics.attach_topics(rows, names={}, diagnostics=detail)
    assert detail == {'hit': False, 'reason': 'title-discovered'}
    assert store.execute(
        "SELECT topic_title FROM continuation_topics WHERE sid='untitled'"
    ).fetchone()[0] == 'FOUND'


@pytest.mark.parametrize('source', ['row', 'metadata', 'native', 'indexed-origin'])
def test_empty_continuation_title_reuses_cache_until_root_changes(store, monkeypatch, source):
    root = '00000000-0000-0000-0000-000000000001'
    native = {}
    monkeypatch.setattr(server, '_codex_thread_row', lambda sid: native.get(sid))
    rows = [{'session_id': root},
            {'session_id': 'child', 'continued_from_session_id': root}]
    warm(rows)
    expected = copy.deepcopy(rows)
    detail = {}
    topics.attach_topics(rows, names={}, diagnostics=detail)
    assert detail == {'hit': True, 'reason': 'hit'}
    assert rows == expected
    assert rows[1]['continuation_topic_title'] == ''
    assert rows[1]['continuation_topic_root_session_id'] == root

    expected_root = root
    if source == 'row':
        rows[0]['display_name'] = 'FOUND: task'
    elif source == 'metadata':
        server._conv_meta_cache[root + '.jsonl'] = {'custom_title': 'FOUND: task'}
    elif source == 'native':
        # Discovery changes the direct lookup without publishing a new title
        # snapshot, so the cache must still query the unloaded native root.
        rows.pop(0)
        native[root] = {'title': 'FOUND: task'}
    else:
        expected_root = 'ancestor'
        store.execute('INSERT INTO session_meta VALUES (?, ?)', (root, expected_root))
        store.commit()
        rows.insert(0, {'session_id': expected_root, 'display_name': 'FOUND: task'})
    topics.attach_topics(rows, names={}, diagnostics=detail)
    assert detail['hit'] is False
    assert rows[-1]['continuation_topic_title'] == 'FOUND'
    assert rows[-1]['continuation_topic_root_session_id'] == expected_root


@pytest.mark.parametrize('explicit_first', [False, True])
def test_equivalent_persisted_edges_reuse_cache_without_sql(store, explicit_first):
    topics.record_continuation('root', 'child', title='ROOT: task')
    explicit = [{'session_id': 'child', 'continued_from_session_id': 'root'}]
    implicit = [{'session_id': 'child'}]
    first, second = (explicit, implicit) if explicit_first else (implicit, explicit)
    warm(first)
    queries = []
    store.set_trace_callback(queries.append)
    detail = {}
    topics.attach_topics(second, names={}, diagnostics=detail)
    assert detail == {'hit': True, 'reason': 'hit'}
    assert queries == []
    assert second[0]['continuation_topic_title'] == 'ROOT'
    assert second[0]['continuation_topic_root_session_id'] == 'root'


def test_different_explicit_edge_still_invalidates_cache(store):
    topics.record_continuation('root', 'child', title='ROOT: task')
    topics.attach_topics([{'session_id': 'other', 'display_name': 'OTHER: task'}], names={})
    warm([{'session_id': 'child'}])
    rows = [{'session_id': 'child', 'continued_from_session_id': 'other'}]
    detail = {}
    topics.attach_topics(rows, names={}, diagnostics=detail)
    assert detail == {'hit': False, 'reason': 'origin-changed'}
    assert rows[0]['continuation_topic_title'] == 'OTHER'
    assert rows[0]['continuation_topic_root_session_id'] == 'other'


def test_invalid_explicit_edge_does_not_poison_implicit_root(store):
    warm([{'session_id': 'root', 'display_name': 'ROOT: task'}])
    invalid = [{'session_id': 'root', 'continued_from_session_id': 'root'}]
    topics.attach_topics(invalid, names={})
    detail = {}
    rows = [{'session_id': 'root'}, {'session_id': 'child', 'continued_from_session_id': 'root'}]
    topics.attach_topics(rows, names={}, diagnostics=detail)
    assert detail == {'hit': False, 'reason': 'origin-changed'}
    assert rows[1]['continuation_topic_title'] == 'ROOT'
