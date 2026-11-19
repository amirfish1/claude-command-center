"""Request-local topic reuse must preserve concurrent database corrections."""
from contextlib import contextmanager
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
