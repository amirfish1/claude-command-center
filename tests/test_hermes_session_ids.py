"""Hermes session ids are read from every state DB in one step per DB."""
import sqlite3
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
