"""CCC-166: a proc-less spawn entry (raw registry row) must not read as exited
while its pid is still running."""
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402,F401  (wires ccc_server.core)
from ccc_server import engines  # noqa: E402
from ccc_server import core as _core  # noqa: E402


class ProcLessSpawnPollTest(unittest.TestCase):
    def test_live_registry_row_stays_running_and_in_registry(self):
        child = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (child.kill(), child.wait()))
        row = {"pid": child.pid, "engine": "antigravity", "spawned_at": "20261024T234227"}
        with mock.patch.object(_core, "_remove_spawn_from_registry") as remove:
            self.assertIsNone(engines._poll_spawn_entry(row))
        remove.assert_not_called()

    def test_dead_registry_row_reads_exited_and_is_removed(self):
        child = subprocess.Popen(["true"])
        child.wait()
        row = {"pid": child.pid, "engine": "antigravity"}
        with mock.patch.object(_core, "_remove_spawn_from_registry") as remove:
            self.assertEqual(engines._poll_spawn_entry(row), -1)
        remove.assert_called_once_with(child.pid, exit_code=-1)

    def test_pidless_entry_keeps_exited_semantics(self):
        self.assertEqual(engines._proc_less_entry_poll({"pid": None, "engine": "kimi"}), -1)


if __name__ == "__main__":
    unittest.main()
