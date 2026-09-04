import importlib
import os
import pathlib
import unittest
from unittest import mock


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "spawn-ledger.jsonl"


class SpawnLedgerTests(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("ccc_server.spawn_ledger")

    def test_payload_reads_rows_newest_first_and_counts_bad_lines(self):
        payload = self.mod.spawn_ledger_payload(path=FIXTURE)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["path"], str(FIXTURE))
        self.assertEqual(payload["ignored_lines"], 1)
        self.assertEqual(payload["row_count"], 6)
        self.assertEqual(payload["graded_count"], 4)
        self.assertEqual(payload["rows"][0]["session_id"], "s-bad-grade")
        self.assertIsNone(payload["rows"][0]["grade"])

    def test_scorecard_groups_by_engine_model_and_task_type(self):
        payload = self.mod.spawn_ledger_payload(path=FIXTURE)
        rows = {
            (row["engine"], row["model"]): row
            for row in payload["scorecard"]["rows"]
        }

        self.assertEqual(payload["scorecard"]["task_types"], ["bugfix", "feature"])
        codex = rows[("codex", "gpt-5")]
        self.assertEqual(codex["tasks"]["feature"], {"avg": 3.0, "n": 2})
        self.assertEqual(codex["tasks"]["bugfix"], {"avg": 3.0, "n": 1})
        self.assertEqual(codex["overall"], {"avg": 3.0, "n": 3})
        self.assertEqual(rows[("claude", "sonnet")]["tasks"]["feature"], {"avg": 4.0, "n": 1})

    def test_env_path_override_is_used_by_default_payload(self):
        with mock.patch.dict(os.environ, {"SPAWN_LEDGER_PATH": str(FIXTURE)}):
            payload = self.mod.spawn_ledger_payload()

        self.assertEqual(payload["path"], str(FIXTURE))
        self.assertEqual(payload["row_count"], 6)

    def test_missing_ledger_returns_empty_payload(self):
        missing = FIXTURE.parent / "missing-spawn-ledger.jsonl"
        payload = self.mod.spawn_ledger_payload(path=missing)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["rows"], [])
        self.assertEqual(payload["scorecard"], {"task_types": [], "rows": []})
        self.assertEqual(payload["error"], "ledger not found")

    def test_server_exports_api_helper_after_adoption(self):
        with mock.patch.dict(os.environ, {"SPAWN_LEDGER_PATH": str(FIXTURE)}):
            server = importlib.import_module("server")
            payload = server.spawn_ledger_payload()

        self.assertEqual(payload["row_count"], 6)
        self.assertEqual(payload["scorecard"]["rows"][0]["engine"], "claude")


if __name__ == "__main__":
    unittest.main()
