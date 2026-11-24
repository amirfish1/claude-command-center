"""Request-local topic reuse must preserve concurrent database corrections."""
from contextlib import closing, contextmanager
import sqlite3
from unittest.mock import patch

import pytest

from ccc_server import continuation_topics as topics


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / 'topics.sqlite3')
    topics._connection(conn)
    monkeypatch.setattr(topics, '_TOPIC_CACHE', {'token': None, 'entries': {}})
    topics.attach_topics([{'session_id': 'root', 'display_name': 'Original'}],
                         conn=conn, names={})
    yield conn
    conn.close()


def test_unchanged_database_resolves_new_topics_only_once(store):
    rows = [{'session_id': 'child', 'continued_from_session_id': 'root'}]
    with patch.object(topics, '_Batch', wraps=topics._Batch) as batches:
        topics.attach_topics(rows, conn=store, names={})
    assert batches.call_count == 1
    assert rows[0]['continuation_topic_title'] == 'Original'
    assert store.execute('SELECT root_sid FROM continuation_topics WHERE sid=?',
                         ('child',)).fetchone() == ('root',)
    assert not store.in_transaction


@pytest.mark.parametrize('writer', ['peer', 'same-connection', 'rolled-back'])
def test_change_before_writer_acquisition_requires_fresh_resolution(store, monkeypatch, writer):
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    peer = sqlite3.connect(filename)
    transaction = topics._transaction

    @contextmanager
    def concurrent_change(conn, **kwargs):
        target = peer if writer == 'peer' else conn
        target.execute('UPDATE continuation_topics SET topic_title=?, user_named=1 WHERE sid=?',
                       ('Corrected', 'root'))
        if writer == 'rolled-back':
            target.rollback()
        else:
            target.commit()
        with transaction(conn, **kwargs):
            yield

    monkeypatch.setattr(topics, '_transaction', concurrent_change)
    rows = [{'session_id': 'child', 'continued_from_session_id': 'root'}]
    try:
        with patch.object(topics, '_Batch', wraps=topics._Batch) as batches:
            topics.attach_topics(rows, conn=store, names={})
        assert batches.call_count == 2
        expected = 'Original' if writer == 'rolled-back' else 'Corrected'
        assert rows[0]['continuation_topic_title'] == expected
        assert store.execute('SELECT topic_title FROM continuation_topics WHERE sid=?',
                             ('child',)).fetchone() == (expected,)
        assert not store.in_transaction
    finally:
        peer.close()


def test_peer_ancestry_correction_before_lock_changes_the_resolved_root(store, monkeypatch):
    store.execute('CREATE TABLE session_meta (sid TEXT PRIMARY KEY, continuation_origin TEXT)')
    store.commit()
    topics.attach_topics([{'session_id': 'other', 'display_name': 'Earlier root'}],
                         conn=store, names={})
    peer = sqlite3.connect(store.execute('PRAGMA database_list').fetchone()[2])
    transaction = topics._transaction

    @contextmanager
    def discovered_ancestry(conn, **kwargs):
        peer.execute('INSERT INTO session_meta VALUES (?, ?)', ('root', 'other'))
        peer.commit()
        with transaction(conn, **kwargs):
            yield

    monkeypatch.setattr(topics, '_transaction', discovered_ancestry)
    rows = [{'session_id': 'child', 'continued_from_session_id': 'root'}]
    try:
        topics.attach_topics(rows, conn=store, names={})
        assert rows[0]['continuation_topic_root_session_id'] == 'other'
        assert rows[0]['continuation_topic_title'] == 'Earlier root'
        assert store.execute('SELECT root_sid FROM continuation_topics WHERE sid=?',
                             ('child',)).fetchone() == ('other',)
    finally:
        peer.close()


def test_new_session_prefetches_only_unknown_database_records(store, monkeypatch):
    store.execute('CREATE TABLE session_meta (sid TEXT PRIMARY KEY, continuation_origin TEXT)')
    store.commit()
    established = [{'session_id': f'old-{i}', 'display_name': f'Topic {i}'}
                   for i in range(600)]
    topics.attach_topics(established, conn=store, names={})
    fetch = topics._sqlite_fetch_rows
    reads = []

    def observed(conn, sql, params, columns):
        if 'WITH wanted(sid)' in sql:
            reads.extend(params)
        return fetch(conn, sql, params, columns)

    monkeypatch.setattr(topics, '_sqlite_fetch_rows', observed)
    rows = established + [{'session_id': 'new', 'continued_from_session_id': 'old-599'}]
    topics.attach_topics(rows, conn=store, names={})
    assert reads == ['new']
    assert rows[-1]['continuation_topic_title'] == 'Topic 599'
    assert store.execute('SELECT root_sid FROM continuation_topics WHERE sid=?',
                         ('new',)).fetchone() == ('old-599',)


def test_scoped_projection_batches_unloaded_topic_roots(store, monkeypatch):
    store.execute('CREATE TABLE session_meta (sid TEXT PRIMARY KEY, continuation_origin TEXT)')
    store.execute('INSERT INTO session_meta VALUES (?, ?)', ('indexed', 'other'))
    store.executemany('INSERT INTO continuation_topics VALUES (?, ?, ?, ?, ?)', [
        ('persisted', 'intermediate', 'root', 'Original', 0),
        ('other', '', 'other', 'Other title', 0),
    ])
    store.commit()
    fetch = topics._sqlite_fetch_rows
    reads = []

    def observed(conn, sql, params, columns):
        if 'WITH wanted(sid)' in sql:
            reads.append(tuple(params))
        return fetch(conn, sql, params, columns)

    monkeypatch.setattr(topics, '_sqlite_fetch_rows', observed)
    rows = [
        {'session_id': 'persisted'},
        {'session_id': 'indexed'},
        {'session_id': 'explicit', 'continued_from_session_id': 'other'},
    ]
    topics.attach_topics(rows, conn=store, names={})
    assert reads == [('persisted', 'indexed', 'explicit'), ('intermediate', 'root', 'other')]
    assert [row['continuation_topic_title'] for row in rows] == [
        'Original', 'Other title', 'Other title',
    ]


def test_read_only_projection_cannot_publish_under_a_newer_topic_revision(store, monkeypatch):
    topics.record_continuation('root', 'child', conn=store)
    project = topics._project_topics

    def project_then_rename(batch, rows):
        project(batch, rows)
        assert not batch.pending
        with closing(sqlite3.connect(store.execute('PRAGMA database_list').fetchone()[2])) as peer:
            peer.execute("UPDATE continuation_topics SET topic_title='Corrected' WHERE sid='root'")
            peer.commit()
        monkeypatch.setattr(topics, '_project_topics', project)

    monkeypatch.setattr(topics, '_project_topics', project_then_rename)
    previous = [{'session_id': 'child'}]
    topics.attach_topics(previous, conn=store, names={})
    assert previous[0]['continuation_topic_title'] == 'Original'
    current = [{'session_id': 'child'}]
    topics.attach_topics(current, conn=store, names={})
    assert current[0]['continuation_topic_title'] == 'Corrected'


@pytest.mark.parametrize('variable_limit', [None, 128])
def test_disconnected_projection_stops_after_first_topic_read(store, monkeypatch, variable_limit):
    class Disconnected(Exception):
        pass

    store.execute('CREATE TABLE session_meta (sid TEXT PRIMARY KEY, continuation_origin TEXT)')
    store.commit()
    getlimit = getattr(store, 'getlimit', None)
    if variable_limit is not None:
        if getlimit is None:
            pytest.skip('SQLite connection limits require Python 3.11')
        previous_limit = store.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, variable_limit)
    fetch = topics._sqlite_fetch_rows
    reads = []

    def observed(conn, sql, params, columns):
        reads.append(len(params))
        return fetch(conn, sql, params, columns)

    def check_client():
        if reads:
            raise Disconnected

    monkeypatch.setattr(topics, '_sqlite_fetch_rows', observed)
    rows = [{'session_id': f'child-{i}', 'continued_from_session_id': 'root'}
            for i in range(1600)]
    with pytest.raises(Disconnected):
        topics.attach_topics(rows, conn=store, names={}, check_client=check_client)
    first_batch = min(1500, getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)) if getlimit else 500
    assert reads == [first_batch]
    assert store.execute('SELECT count(*) FROM continuation_topics').fetchone() == (1,)
    assert not store.in_transaction

    if variable_limit is not None:
        store.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous_limit)
    topics.attach_topics(rows, conn=store, names={})
    assert rows[-1]['continuation_topic_title'] == 'Original'
    assert store.execute('SELECT count(*) FROM continuation_topics').fetchone() == (1601,)


def test_disconnected_cache_replay_leaves_projection_and_cache_available(store):
    class Disconnected(Exception):
        pass

    rows = [{'session_id': f'child-{i}', 'continued_from_session_id': 'root'}
            for i in range(300)]
    topics.attach_topics([dict(row) for row in rows], conn=store, names={})
    calls = 0

    def check_client():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise Disconnected

    with patch.object(topics, '_Batch', side_effect=AssertionError('expected cache hit')):
        with pytest.raises(Disconnected):
            topics.attach_topics(rows, conn=store, names={}, check_client=check_client)
        assert all('continuation_topic_title' not in row for row in rows)
        topics.attach_topics(rows, conn=store, names={})
    assert all(row['continuation_topic_title'] == 'Original' for row in rows)


def test_cached_records_do_not_hide_peer_rename_on_new_session(store):
    peer = sqlite3.connect(store.execute('PRAGMA database_list').fetchone()[2])
    try:
        peer.execute("UPDATE continuation_topics SET topic_title='Renamed' WHERE sid='root'")
        peer.commit()
        rows = [{'session_id': 'new', 'continued_from_session_id': 'root'}]
        topics.attach_topics(rows, conn=store, names={})
        assert rows[0]['continuation_topic_title'] == 'Renamed'
    finally:
        peer.close()


def test_cached_records_re_resolve_existing_children_when_root_edge_appears(store):
    topics.attach_topics([{'session_id': 'older', 'display_name': 'Older topic'},
                          {'session_id': 'child', 'continued_from_session_id': 'root'}],
                         conn=store, names={})
    rows = [{'session_id': 'root', 'continued_from_session_id': 'older'},
            {'session_id': 'child', 'continued_from_session_id': 'root'},
            {'session_id': 'new'}]
    topics.attach_topics(rows, conn=store, names={})
    assert rows[1]['continuation_topic_root_session_id'] == 'older'
    assert rows[1]['continuation_topic_title'] == 'Older topic'


def test_busy_writer_reuses_reads_but_retries_pending_writes(store):
    store.execute('PRAGMA journal_mode=WAL')
    topics.attach_topics([{'session_id': 'root'}], conn=store, names={})
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    with closing(sqlite3.connect(filename)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        for title, expected_reads in [('First topic', 2), ('Current topic', 0)]:
            rows = [
                {'session_id': 'new-root', 'display_name': title},
                {'session_id': 'new-child', 'continued_from_session_id': 'new-root'},
            ]
            diagnostics = {}
            topics.attach_topics(rows, conn=store, names={}, diagnostics=diagnostics)
            assert diagnostics['miss_detail']['writer_busy']
            assert diagnostics['miss_detail']['prefetch_rows'] == expected_reads
            assert rows[1]['continuation_topic_title'] == title
            assert store.execute("SELECT count(*) FROM continuation_topics").fetchone() == (1,)
        writer.execute(
            "INSERT INTO continuation_topics VALUES ('new-root', '', 'new-root', 'User title', 1)")
        writer.commit()
    topics.attach_topics(rows, conn=store, names={})
    assert rows[1]['continuation_topic_title'] == 'User title'
    persisted = store.execute(
        "SELECT root_sid, topic_title FROM continuation_topics WHERE sid='new-child'").fetchone()
    assert persisted == ('new-root', 'User title')
