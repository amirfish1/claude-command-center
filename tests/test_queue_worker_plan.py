"""The queue status strip shows what a worker spawned RIGHT NOW would run as.

`compute_queue_worker_plan` mirrors WatchTower's `config.engine()/model()/
effort()` resolution chain on the same two inputs (queue-config entry, CCC
spawn-defaults). These pin the chain order and the provenance labels the
dashboard renders (CCC-1036).
"""

import unittest
from unittest import mock

import server
from ccc_server import queue_events
from ccc_server.queue_events import compute_queue_worker_plan, compute_queues_health


SPAWN = {
    "worker_engine": "kimi",
    "worker_model": "kimi-code/kimi-for-coding",
    "worker_reasoning_effort": "high",
    "models": {"claude": "fable-5-1", "codex": "gpt-5.6-terra", "kimi": "kimi-code/k3"},
}


class WorkerPlanChainTests(unittest.TestCase):
    def test_queue_engine_override_falls_through_to_shared_model(self):
        # Queue pins claude; worker_model belongs to kimi so it must NOT leak.
        plan = compute_queue_worker_plan({"engine": "claude"}, SPAWN)
        self.assertEqual(plan["engine"], "claude")
        self.assertEqual(plan["engine_source"], "queue")
        self.assertEqual(plan["model"], "claude-fable-5-1")
        self.assertEqual(plan["model_source"], "ccc_default")
        self.assertEqual(plan["effort"], "high")
        self.assertEqual(plan["effort_source"], "ccc_worker_default")
        self.assertFalse(plan["is_default"])

    def test_unpinned_queue_uses_ccc_worker_defaults_and_reads_default(self):
        plan = compute_queue_worker_plan({}, SPAWN)
        self.assertEqual(plan["engine"], "kimi")
        self.assertEqual(plan["engine_source"], "ccc_worker_default")
        self.assertEqual(plan["model"], "kimi-code/kimi-for-coding")
        self.assertEqual(plan["model_source"], "ccc_worker_default")
        self.assertTrue(plan["is_default"])

    def test_explicit_queue_model_and_effort_win(self):
        plan = compute_queue_worker_plan(
            {"engine": "claude", "model": "opus-5", "effort": "max"}, SPAWN)
        self.assertEqual(plan["model"], "claude-opus-5", "alias table applies")
        self.assertEqual(plan["model_source"], "queue")
        self.assertEqual(plan["effort"], "max")
        self.assertEqual(plan["effort_source"], "queue")

    def test_no_defaults_at_all_falls_back_by_path(self):
        with mock.patch.object(queue_events, "_worker_plan_engine_on_path", return_value=False):
            plan = compute_queue_worker_plan({}, {})
        self.assertEqual(plan["engine"], "claude")
        self.assertEqual(plan["engine_source"], "fallback")
        self.assertEqual(plan["model"], "")
        self.assertEqual(plan["model_source"], "engine_default")
        self.assertEqual(plan["effort_source"], "engine_default")
        with mock.patch.object(queue_events, "_worker_plan_engine_on_path", return_value=True):
            self.assertEqual(compute_queue_worker_plan({}, {})["engine"], "codex")

    def test_rollup_row_carries_the_plan(self):
        config = {"CCC": {"auto_drain": True, "engine": "claude"}}
        with mock.patch.object(server, "_wt_read_config", return_value=config), \
                mock.patch.object(server, "_load_spawn_defaults", return_value=SPAWN):
            rows = {r["queue"]: r for r in compute_queues_health(
                health=[{"project": "CCC", "depth": 0}], wt_workers=[], items=[])}
        self.assertEqual(rows["CCC"]["worker_plan"]["engine"], "claude")
        self.assertEqual(rows["CCC"]["worker_plan"]["model"], "claude-fable-5-1")


if __name__ == "__main__":
    unittest.main()
