"""CCC-1235: queue manager role models go through WatchTower's own setter."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from watchtower import config as wt_config, models as wt_models, roles as wt_roles, workers as wt_workers
    HAVE_ROLES = hasattr(wt_config, "set_role") and hasattr(wt_roles, "role_table")
except Exception:  # older or missing WatchTower
    HAVE_ROLES = False

from ccc_server import queue_roles


@unittest.skipUnless(HAVE_ROLES, "WatchTower without per-role models (WT-14)")
class QueueRolesTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tmp = Path(td.name)
        for attr, name in (("CONFIG_FILE", "config.json"),
                           ("CCC_MODEL_POLICY_FILE", "policy.json"),
                           ("CCC_SPAWN_DEFAULTS_FILE", "spawn.json")):
            patcher = mock.patch.object(wt_config, attr, tmp / name)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(wt_workers, "engine_available", lambda e: e in ("codex", "claude"))
        patcher.start()
        self.addCleanup(patcher.stop)
        wt_config.set_engine("RQ", "claude")
        self.codex_models = list(wt_models.catalog("codex") or ())
        if not self.codex_models:
            self.skipTest("no readable codex catalog on this machine")

    def test_state_lists_all_roles_and_only_catalog_engines(self):
        state = queue_roles.role_state("rq")
        self.assertTrue(state["ok"] and state["available"])
        # WatchTower owns the role list (it has grown: 'assessor' was added), so
        # pin the original four as a prefix and the rest to its own ROLES.
        names = [r["role"] for r in state["roles"]]
        self.assertEqual(names[:4], ["planner", "plan_reviewer", "builder", "verifier"])
        self.assertEqual(names, list(wt_roles.ROLES))
        builder = next(r for r in state["roles"] if r["role"] == "builder")
        self.assertFalse(builder["editable"])
        engines = {e["engine"]: e["models"] for e in state["engines"]}
        self.assertLessEqual(set(engines), {"codex", "claude"})
        for eng, ids in engines.items():
            for m in ids:
                self.assertTrue(wt_config.is_approved_model(eng, m), (eng, m))

    def test_set_roles_writes_through_set_role(self):
        pick = self.codex_models[0]
        state = queue_roles.set_roles("RQ", {"planner": {"engine": "codex", "model": pick}})
        self.assertTrue(state["ok"], state)
        self.assertEqual(wt_config.role_override("RQ", "planner"), ("codex", pick))
        planner = next(r for r in state["roles"] if r["role"] == "planner")
        self.assertEqual((planner["engine"], planner["model"], planner["source"]), ("codex", pick, "queue"))
        # "" clears back to the default.
        state = queue_roles.set_roles("RQ", {"planner": {"engine": "", "model": ""}})
        self.assertEqual(wt_config.role_override("RQ", "planner"), ("", ""))

    def test_invalid_model_is_reported_per_role(self):
        state = queue_roles.set_roles("RQ", {
            "verifier": {"engine": "codex", "model": "not-a-model"},
            "builder": {"engine": "codex", "model": ""},
        })
        self.assertFalse(state["ok"])
        self.assertIn("verifier", state["errors"])
        self.assertIn("builder", state["errors"])
        self.assertEqual(wt_config.role_override("RQ", "verifier"), ("", ""))

    def test_unknown_queue(self):
        self.assertFalse(queue_roles.role_state("NOPE")["ok"])
        self.assertFalse(queue_roles.set_roles("NOPE", {})["ok"])


if __name__ == "__main__":
    unittest.main()
