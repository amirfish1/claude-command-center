"""CCC-267: the dashboard keeps an existing usage DB current."""

import fcntl
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from ccc_server.usage_db import autoingest


class AutoIngestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "throughput.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_db_is_never_created(self):
        with mock.patch.object(subprocess, "run") as run:
            self.assertEqual(autoingest.run_once(self.db), "no_db")
        run.assert_not_called()
        self.assertFalse(os.path.exists(self.db))

    def test_runs_incremental_ingest_against_the_db(self):
        open(self.db, "w").close()
        with mock.patch.object(subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(autoingest.run_once(self.db), "ok")
        cmd = run.call_args.args[0]
        tail = cmd[cmd.index("ccc_server.usage_db") + 1:]
        self.assertEqual(tail, ["--db", self.db, "ingest", "--json"])
        self.assertNotIn("--full-rebuild", cmd)

    def test_skips_while_another_ingest_holds_the_lock(self):
        open(self.db, "w").close()
        with open(self.db + ".ingest.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with mock.patch.object(subprocess, "run") as run:
                self.assertEqual(autoingest.run_once(self.db), "busy")
            run.assert_not_called()

    def test_failed_exit_is_reported(self):
        open(self.db, "w").close()
        with mock.patch.object(subprocess, "run", return_value=mock.Mock(returncode=2)):
            self.assertEqual(autoingest.run_once(self.db), "failed")


if __name__ == "__main__":
    unittest.main()
