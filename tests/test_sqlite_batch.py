"""Bounded index reads preserve SQLite data while avoiding per-row callbacks."""
import sqlite3

import pytest

from ccc_server.sqlite_batch import fetch_rows


def test_batch_preserves_order_nulls_unicode_and_integer_precision():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE sample (id INTEGER PRIMARY KEY, title TEXT, value REAL)')
        conn.executemany('INSERT INTO sample VALUES (?,?,?)', [
            (1, 'שלום \"quoted\"\ntext', None),
            (2, None, 0.12345678901234567),
            (9007199254740993, 'last', -0.5),
        ])
        sql = 'SELECT id, title, value FROM sample WHERE id >= ? ORDER BY id DESC LIMIT ?'
        expected = conn.execute(sql, (1, 3)).fetchall()
        conn.row_factory = sqlite3.Row
        assert fetch_rows(conn, sql, (1, 3), ('id', 'title', 'value')) == expected
        assert fetch_rows(conn, sql, (1, 0), ('id', 'title', 'value')) == []


def test_batch_retains_indexed_limit_and_one_python_row_callback():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE sample (sid TEXT PRIMARY KEY, title TEXT)')
        conn.executemany('INSERT INTO sample VALUES (?,?)',
                         [(f'session-{i:05d}', str(i)) for i in range(12000)])
        queries, callbacks = [], []
        conn.set_trace_callback(queries.append)

        def factory(cursor, row):
            callbacks.append(row)
            return row

        conn.row_factory = factory
        rows = fetch_rows(conn,
                          'SELECT sid, title FROM sample WHERE sid >= ? ORDER BY sid LIMIT ?',
                          ('session-00100', 500), ('sid', 'title'))
        assert len(rows) == 500 and rows[0] == ('session-00100', '100')
        assert len(callbacks) == 1  # not 500 Python callbacks/GIL resumptions
        assert len(queries) == 1 and 'LIMIT 500' in queries[0]
        plan = conn.execute('EXPLAIN QUERY PLAN ' + queries[0]).fetchall()
        assert any('SEARCH sample USING INDEX' in r[3] for r in plan)


def test_old_sqlite_without_json_uses_same_select():
    class LegacyConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if 'json_group_array' in sql:
                raise sqlite3.OperationalError('no such function: json_array')
            return super().execute(sql, parameters)
    with sqlite3.connect(':memory:', factory=LegacyConnection) as conn:
        assert fetch_rows(conn, 'SELECT ? AS value', ('hello',), ('value',)) == [('hello',)]


def test_blob_values_fall_back_without_changing_types():
    with sqlite3.connect(':memory:') as conn:
        assert fetch_rows(conn, 'SELECT ? AS data', (b'blob',), ('data',)) == [(b'blob',)]


def test_batch_does_not_hide_unrelated_sqlite_errors():
    with sqlite3.connect(':memory:') as conn:
        with pytest.raises(sqlite3.OperationalError, match='no such table'):
            fetch_rows(conn, 'SELECT sid FROM missing', (), ('sid',))
