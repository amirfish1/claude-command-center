"""Archived WatchTower queues (`wt queue archive`) must stay out of every
queue list the dashboard renders — wt status hides them; CCC reads
queue-config.json directly and used to leak them (the picker dropdowns kept
offering retired queues)."""

import unittest
from unittest import mock

import server
from ccc_server import queue_events as qe
from ccc_server.session_queue import queues_for_session


CFG = {
    "CCC": {"repo_path": "/tmp/x"},
    "OLD": {"repo_path": "/tmp/y", "archived": True,
            "archived_at": "2026-10-29T00:00:00Z"},
}


class HealthRowsTest(unittest.TestCase):
    def test_archived_queue_not_in_health_rows(self):
        with mock.patch.object(server, "_wt_read_config", return_value=CFG):
            rows = qe.compute_queues_health(
                health=[{"project": "CCC", "depth": 1},
                        {"project": "OLD", "depth": 0}],
                wt_workers=[],
                items=[{"project": "OLD", "status": "closed"}])
        names = {r["queue"] for r in rows}
        self.assertIn("CCC", names)
        self.assertNotIn("OLD", names)


class ConfigOptionsTest(unittest.TestCase):
    def test_config_options_skips_archived(self):
        with mock.patch.object(server, "_wt_read_config", return_value=CFG):
            payload = server._queue_config_options()
        names = {q["queue"] for q in payload["queues"]}
        self.assertEqual(names, {"CCC"})


class SessionLinkTest(unittest.TestCase):
    def test_queues_for_session_skips_archived(self):
        cfg = dict(CFG)
        cfg["OLD"] = dict(CFG["OLD"], session_id="s1")
        cfg["CCC"] = dict(CFG["CCC"], session_id="s1")
        self.assertEqual(queues_for_session(cfg, "s1"), ["CCC"])


if __name__ == "__main__":
    unittest.main()
