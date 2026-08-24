"""The spawn-timeline store is shared by two processes.

The worker records the engine marks (thread_start_done ... finalize_done) and
the dashboard records row_in_session_list, because worker_engines.py lazily
imports server and so each process runs its own copy of this module state.
The JSON file is the only thing they share.

Two bugs lived here, both of which made the debug panel useless:

  1. The file was read once at startup, so the dashboard answered every query
     from a dict that predated the spawn it was being asked about.
  2. A save wrote the process's whole dict, so a freshly booted process that
     started one spawn and saved replaced the file with that single record and
     wiped every other spawn.

These pin the read-merge-write invariant that fixes both.
"""

import importlib
import json
import tempfile
import unittest
from pathlib import Path


class SpawnTimelineStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "spawn-timeline.json"
        self._orig_file = self.server._SPAWN_TIMELINE_FILE
        self.server._SPAWN_TIMELINE_FILE = self.tmp
        self.reboot()

    def tearDown(self):
        self.server._SPAWN_TIMELINE_FILE = self._orig_file
        self.reboot()

    def reboot(self):
        """Simulate a process boot: empty dict, nothing seen on disk yet."""
        self.server._SPAWN_TIMELINE.clear()
        self.server._SPAWN_TIMELINE_FILE_SIG = None

    def on_disk(self):
        return json.loads(self.tmp.read_text())

    def test_a_second_process_sees_what_the_first_one_wrote(self):
        """The dashboard must not answer from its startup snapshot."""
        server = self.server
        server._spawn_timeline_start("sid-a", engine="codex")
        server._spawn_timeline_mark("sid-a", "thread_start_done", 1234.5)
        server._spawn_timeline_save()

        self.reboot()  # <- the other process
        got = server._spawn_timeline_get("sid-a")
        self.assertIsNotNone(got, "a fresh process saw nothing on disk")
        self.assertEqual(got["marks"]["thread_start_done"], 1234.5)

    def test_saving_after_a_bare_start_does_not_wipe_other_spawns(self):
        """The regression: start + save with no mark used to truncate."""
        server = self.server
        for sid in ("sid-a", "sid-b"):
            self.reboot()
            server._spawn_timeline_start(sid)
            server._spawn_timeline_mark(sid, "thread_start_done", 1.0)
            server._spawn_timeline_save()

        self.reboot()
        server._spawn_timeline_start("sid-c")
        server._spawn_timeline_save()

        self.assertEqual(sorted(self.on_disk()), ["sid-a", "sid-b", "sid-c"])

    def test_a_mark_from_the_other_process_survives_a_later_save(self):
        """row_in_session_list is written by the dashboard, not the worker."""
        server = self.server
        server._spawn_timeline_start("sid-a")
        server._spawn_timeline_mark("sid-a", "thread_start_done", 1.0)
        server._spawn_timeline_save()

        self.reboot()  # dashboard
        server._spawn_timeline_mark("sid-a", "row_in_session_list", 99.0)
        server._spawn_timeline_save()

        self.reboot()  # worker again, records an unrelated spawn
        server._spawn_timeline_start("sid-b")
        server._spawn_timeline_save()

        entry = self.on_disk()["sid-a"]
        self.assertEqual(entry["marks"]["row_in_session_list"], 99.0)
        self.assertEqual(entry["marks"]["thread_start_done"], 1.0)

    def test_mark_reports_whether_it_wrote(self):
        """Hot paths persist on the transition, so first-write must be visible."""
        server = self.server
        server._spawn_timeline_start("sid-a")
        self.assertTrue(server._spawn_timeline_mark("sid-a", "row_in_session_list"))
        self.assertFalse(
            server._spawn_timeline_mark("sid-a", "row_in_session_list"),
            "first write must win, and the repeat must report that it did not write",
        )

    def test_local_marks_win_over_the_file_copy(self):
        """Our in-memory mark was recorded live; the file copy may predate it."""
        server = self.server
        server._spawn_timeline_start("sid-a")
        server._spawn_timeline_mark("sid-a", "thread_start_done", 1.0)
        server._spawn_timeline_save()

        # A stale copy on disk claiming a different value for the same mark.
        stale = self.on_disk()
        stale["sid-a"]["marks"]["thread_start_done"] = 9999.0
        self.tmp.write_text(json.dumps(stale))
        server._SPAWN_TIMELINE_FILE_SIG = None  # force a re-read

        self.assertEqual(
            server._spawn_timeline_get("sid-a")["marks"]["thread_start_done"], 1.0
        )


if __name__ == "__main__":
    unittest.main()
