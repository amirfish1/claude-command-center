"""Hermes session ids are read from every state DB in one step per DB."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import server  # noqa: F401  (binds ccc_server.core to the server module)
from ccc_server import core as _core
from ccc_server import hermes


class HermesSessionIdsTest(unittest.TestCase):
    def test_ids_and_owning_db_come_from_all_dbs(self):
        with tempfile.TemporaryDirectory() as tmp:
            main, profile = Path(tmp) / "state.db", Path(tmp) / "profile.db"
            for path, ids in ((main, ["a", "shared"]), (profile, ["b", "shared", None])):
                con = sqlite3.connect(path)
                con.execute("CREATE TABLE sessions (id TEXT, title TEXT)")
                con.executemany("INSERT INTO sessions (id) VALUES (?)", [(i,) for i in ids])
                con.commit()
                con.close()
            saved_ids, saved_index = dict(_core._HERMES_ID_CACHE), dict(_core._HERMES_DB_INDEX)
            try:
                with mock.patch.object(hermes, "_hermes_db_paths", return_value=[main, profile]), \
                        mock.patch.object(hermes, "_hermes_db_cache_key", return_value=("test", 1)):
                    self.assertEqual(hermes._hermes_session_ids(), {"a", "b", "shared"})
                    self.assertEqual(hermes._hermes_db_for_session("b"), profile)
                    # The main DB wins when two DBs hold the same id.
                    self.assertEqual(hermes._hermes_db_for_session("shared"), main)
            finally:
                _core._HERMES_ID_CACHE.clear(); _core._HERMES_ID_CACHE.update(saved_ids)
                _core._HERMES_DB_INDEX.clear(); _core._HERMES_DB_INDEX.update(saved_index)


def test_session_owner_uses_current_wal_without_rebuilding_bulk_index(tmp_path, monkeypatch):
    main = tmp_path / "state.db"
    profile = tmp_path / "profile.db"
    monkeypatch.setattr(hermes, "_hermes_db_paths", lambda: [main, profile])
    monkeypatch.setattr(_core, "_detect_session_engine", lambda _sid: "hermes")
    monkeypatch.setattr(_core, "_HERMES_ID_CACHE", {"key": None, "ids": set()})
    monkeypatch.setattr(_core, "_HERMES_DB_INDEX", {"key": None, "by_session": {}})
    with closing(sqlite3.connect(main)) as writer, closing(sqlite3.connect(profile)) as worker:
        writer.execute("PRAGMA journal_mode=WAL")
        for db in (writer, worker):
            db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
            db.commit()
        worker.execute("INSERT INTO sessions VALUES ('shared')")
        worker.commit()
        assert hermes._hermes_session_ids() == {"shared"}
        assert hermes._hermes_db_for_session("shared") == profile

        monkeypatch.setattr(hermes, "_hermes_session_ids",
                            mock.Mock(side_effect=AssertionError("unexpected bulk scan")))
        writer.execute("INSERT INTO sessions VALUES ('shared')")
        writer.commit()
        assert hermes._hermes_db_for_session("shared", build_index=False) == main
        assert _core._conv_parse_jsonl_mtime("shared") == hermes._hermes_file_key(
            (main, tmp_path / "state.db-wal"))
        assert hermes._hermes_db_for_session("absent", build_index=False) is None
        writer.execute("DELETE FROM sessions WHERE id = 'shared'")
        writer.commit()
        assert hermes._hermes_db_for_session("shared", build_index=False) == profile


if __name__ == "__main__":
    unittest.main()
