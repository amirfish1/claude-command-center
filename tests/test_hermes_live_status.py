import json
import os
import sqlite3
import time

import server


def _write_hermes_db(path, sid):
    con = sqlite3.connect(path)
    try:
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL);
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp REAL,
                active INTEGER
            );
        """)
        con.execute("INSERT INTO sessions VALUES (?, ?)", (sid, 1.0))
        con.commit()
    finally:
        con.close()


def _reset_hermes_caches():
    server._HERMES_ID_CACHE.update(key=None, ids=set())
    server._HERMES_DB_INDEX.update(key=None, by_session={})
    server._HERMES_ACTIVE_REGISTRY_CACHE.update(key=None, entries=[])


def test_hermes_live_status_uses_runtime_owner_and_exposes_freshness(tmp_path, monkeypatch):
    sid = "20260916_044330_test"
    db = tmp_path / "state.db"
    _write_hermes_db(db, sid)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "active_sessions.json").write_text(json.dumps({"entries": [{
        "lease_id": "lease-test",
        "session_id": sid,
        "surface": "desktop",
        "pid": os.getpid(),
        "metadata": {"live_session_id": "desktop-runtime"},
        "track_liveness": True,
    }]}), encoding="utf-8")
    monkeypatch.setattr(server, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(server, "HERMES_STATE_DB", db)
    monkeypatch.setattr(server, "HERMES_PROFILES_DIR", tmp_path / "profiles")
    _reset_hermes_caches()

    status = server.session_live_status(sid, "/tmp/example")

    assert status["live"] is True
    assert status["pid"] == os.getpid()
    assert status["status"] == "idle"
    assert status["kind"] == "hermes"
    assert status["transcript_mtime"] > 0
    assert status["cwd"] == "/tmp/example"
    assert server._conv_parse_jsonl_mtime(sid) == server._hermes_session_cache_key(sid)


def test_hermes_live_status_ignores_dead_runtime_owner(tmp_path, monkeypatch):
    sid = "20260916_044330_dead"
    db = tmp_path / "state.db"
    _write_hermes_db(db, sid)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "active_sessions.json").write_text(json.dumps({"entries": [{
        "lease_id": "lease-dead",
        "session_id": sid,
        "surface": "desktop",
        "pid": 999_999_999,
        "track_liveness": True,
    }]}), encoding="utf-8")
    monkeypatch.setattr(server, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(server, "HERMES_STATE_DB", db)
    monkeypatch.setattr(server, "HERMES_PROFILES_DIR", tmp_path / "profiles")
    _reset_hermes_caches()

    status = server.session_live_status(sid, "/tmp/example")

    assert status["live"] is False
    assert status["status"] == "history"
    assert status["pid"] is None


def test_hermes_pid_liveness_rejects_recycled_pid_identity():
    assert server._hermes_pid_alive(os.getpid()) is True
    assert server._hermes_pid_alive(os.getpid(), time.time()) is False
