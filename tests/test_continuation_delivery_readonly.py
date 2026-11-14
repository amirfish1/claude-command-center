"""Delivery lineage stays readable while background index writers run."""

import sqlite3
import threading

import server
from ccc_server import continuation, ship_graph


def test_forwarding_uses_committed_lineage_without_schema_or_index_writes(tmp_path, monkeypatch):
    database = tmp_path / 'ship-graph.sqlite'
    writer = ship_graph._connect(database)
    ship_graph._init_db(writer)
    for sid, origin, started in [('old', '', 1), ('successor', 'old', 2)]:
        writer.execute(
            'INSERT INTO session_meta VALUES (?,?,?,?,?,?,?,?,?)',
            (sid, 'repo', '/repo', started, started, '[]', '{}', '[]', origin),
        )
    writer.commit()
    reader = sqlite3.connect(database, timeout=0.1)
    reader.execute('PRAGMA query_only=ON')
    reader.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action in (
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX,
    ) else sqlite3.SQLITE_OK)
    runtime = threading.local()
    runtime.conn = reader
    monkeypatch.setattr(ship_graph, '_tls', runtime)
    monkeypatch.setattr(ship_graph, '_last_sync_ts', 0)
    monkeypatch.setattr(ship_graph, '_get_db_path', lambda: database)
    monkeypatch.setattr(continuation, '_manual_forward_path', lambda: str(tmp_path / 'manual.json'))
    monkeypatch.setattr(server, '_sessions_state_snapshot', lambda: {})
    writer.execute('BEGIN IMMEDIATE')
    try:
        assert continuation.forward_target('old') == 'successor'
        assert continuation.forward_target('unrelated') == 'unrelated'
    finally:
        writer.rollback()
        reader.close()
        writer.close()
