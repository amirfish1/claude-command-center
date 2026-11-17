"""Cold topic materialization stays bounded without losing revision semantics."""
import sqlite3

import pytest

from ccc_server import continuation_topics as topics


class WithoutJson(sqlite3.Connection):
    def execute(self, sql, parameters=(), /):
        if 'json_each' in sql:
            raise sqlite3.OperationalError('no such table: json_each')
        if 'json_group_array' in sql:
            raise sqlite3.OperationalError('no such function: json_group_array')
        return super().execute(sql, parameters)


@pytest.mark.parametrize('factory', [sqlite3.Connection, WithoutJson])
def test_chunked_flush_preserves_rows_revisions_and_rollback(factory):
    conn = sqlite3.connect(':memory:', factory=factory)
    try:
        topics._connection(conn)
        batch = topics._Batch(conn, [], {})
        for index in range(1201):
            sid = f'session-{index}'
            batch.put(sid, '', sid, f'Topic "{index}" שלום', index % 2)
        queries = []
        conn.set_trace_callback(queries.append)
        with conn:
            batch.flush()
        assert conn.execute('SELECT count(*) FROM continuation_topics').fetchone()[0] == 1201
        assert conn.execute('SELECT topic_title, user_named FROM continuation_topics WHERE sid=?',
                            ('session-600',)).fetchone() == ('Topic "600" שלום', 0)
        assert conn.execute('SELECT revision FROM continuation_topic_meta').fetchone() is None
        if factory is sqlite3.Connection:
            assert sum('FROM json_each' in sql for sql in queries) == 3
            assert sum('WHERE sid IN' in sql for sql in queries) == 3
            assert not any('WHERE sid=' in sql for sql in queries[:-3])

        # Only root/title/user changes on existing rows advance the revision.
        # An origin-only update and a new root retain the original semantics.
        updates = topics._Batch(conn, [], {})
        updates.put('session-0', 'origin', 'session-0', 'Topic "0" שלום', 0)
        updates.put('session-1', '', 'session-1', 'renamed', 1)
        updates.put('session-2', '', 'different-root', 'Topic "2" שלום', 0)
        updates.put('new-session', '', 'new-session', 'new', 0)
        with conn:
            updates.flush()
        assert conn.execute('SELECT revision FROM continuation_topic_meta').fetchone()[0] == 2

        # Flush remains part of the caller's transaction, including revision.
        rollback = topics._Batch(conn, [], {})
        rollback.put('session-1', '', 'session-1', 'discarded', 1)
        conn.execute('BEGIN')
        rollback.flush()
        conn.rollback()
        assert conn.execute('SELECT topic_title FROM continuation_topics WHERE sid=?',
                            ('session-1',)).fetchone()[0] == 'renamed'
        assert conn.execute('SELECT revision FROM continuation_topic_meta').fetchone()[0] == 2
    finally:
        conn.close()
