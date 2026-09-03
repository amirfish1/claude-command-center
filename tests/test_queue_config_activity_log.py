"""Queue-config writes from the dashboard leave a CONFIG row in WatchTower's
activity log (CCC-1037), so the queue's Log panel shows who changed what.

Covers the diff formatter and the writer; the writer is pointed at a temp
file through WatchTower's own $WATCHTOWER_ACTIVITY_LOG isolation hook.
"""

import os
import tempfile
import unittest
from unittest import mock

import server


class QueueConfigDiffTests(unittest.TestCase):
    def test_changed_keys_only_with_unset_and_on_off_spelling(self):
        diff = server._queue_config_diff(
            {"engine": "claude", "auto_drain": False, "model": "opus-5", "desired_workers": 1},
            {"engine": "kimi", "auto_drain": True, "desired_workers": 1, "effort": "high"},
        )
        self.assertEqual(diff, [
            "auto_drain: off → on",
            "effort: unset → high",
            "engine: claude → kimi",
            "model: opus-5 → unset",
        ])

    def test_no_change_is_empty(self):
        self.assertEqual(server._queue_config_diff({"a": 1}, {"a": 1}), [])
        self.assertEqual(server._queue_config_diff(None, {}), [])

    def test_lists_join(self):
        self.assertEqual(server._queue_config_diff({}, {"claim_types": ["bug"]}),
                         ["claim_types: unset → bug"])


class QueueConfigActivityLogTests(unittest.TestCase):
    def test_writes_one_config_row_in_wt_format(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "activity.log")
            with mock.patch.dict(os.environ, {"WATCHTOWER_ACTIVITY_LOG": log}):
                ok = server._wt_log_queue_config_change(
                    "CCC", ["auto_drain: off → on", "desired_workers: 1 → 2"])
            self.assertTrue(ok)
            with open(log) as f:
                lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertIn("  CCC             CONFIG   CCC updated — auto_drain: off → on; desired_workers: 1 → 2 (via CCC dashboard)", line)
        # Same column layout the queue filter parses.
        self.assertEqual(server._wt_log_line_queue(line), "CCC")

    def test_delete_and_created_actions(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "activity.log")
            with mock.patch.dict(os.environ, {"WATCHTOWER_ACTIVITY_LOG": log}):
                server._wt_log_queue_config_change("OLD", "", action="deleted")
                server._wt_log_queue_config_change("NEW", "no changes", action="created")
            with open(log) as f:
                text = f.read()
        self.assertIn("CONFIG   OLD deleted (via CCC dashboard)", text)
        self.assertIn("CONFIG   NEW created — no changes (via CCC dashboard)", text)

    def test_blank_queue_is_a_noop(self):
        self.assertFalse(server._wt_log_queue_config_change("", ["x"]))


if __name__ == "__main__":
    unittest.main()
