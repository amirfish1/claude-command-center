import importlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class TestClaudeOrganizationResolver(unittest.TestCase):
    def setUp(self):
        # The functions under test live in the extracted module, but they reach
        # _resolve_claude_bin through ccc_server._CoreProxy, which resolves against
        # sys.modules["server"] on every access. Patch it there.
        self.server = importlib.import_module("ccc_server.recall_usage")
        self.core = importlib.import_module("server")
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.config_file = root / ".claude.json"
        self.state_file = root / "state" / "claude-org-id.json"
        self.patches = [
            mock.patch.object(self.server, "_CLAUDE_ACCOUNT_FILE", self.config_file),
            mock.patch.object(self.server, "_CLAUDE_ORG_ID_STATE_FILE", self.state_file),
            mock.patch.object(self.core, "_resolve_claude_bin", return_value={"available": True, "bin": "claude"}),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def _write_cached_org(self, org_id):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"org_id": org_id}), encoding="utf-8")

    def test_fresh_cli_success_persists_only_the_organization_uuid(self):
        org_id = "11111111-2222-4333-8444-555555555555"
        completed = subprocess.CompletedProcess(["claude"], 0, json.dumps({"orgId": org_id}), "")
        with mock.patch.object(self.server.subprocess, "run", return_value=completed) as run:
            self.assertEqual(self.server._get_claude_org_id(), org_id)

        self.assertEqual(json.loads(self.state_file.read_text(encoding="utf-8")), {"org_id": org_id})
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertIsNone(self.server._claude_org_id_diagnostic())

    def test_timeout_uses_last_successful_org_uuid(self):
        org_id = "11111111-2222-4333-8444-555555555555"
        self._write_cached_org(org_id)
        with mock.patch.object(self.server.subprocess, "run", side_effect=subprocess.TimeoutExpired(["claude"], 15)):
            self.assertEqual(self.server._get_claude_org_id(), org_id)

        self.assertIn("timed out", self.server._claude_org_id_diagnostic())

    def test_timeout_without_cached_org_uuid_returns_no_org(self):
        with mock.patch.object(self.server.subprocess, "run", side_effect=subprocess.TimeoutExpired(["claude"], 15)):
            self.assertIsNone(self.server._get_claude_org_id())

        self.assertIn("timed out", self.server._claude_org_id_diagnostic())

    def test_invalid_cli_output_does_not_use_cached_org_uuid(self):
        self._write_cached_org("11111111-2222-4333-8444-555555555555")
        completed = subprocess.CompletedProcess(["claude"], 0, '{"orgId":"not-a-uuid"}', "")
        with mock.patch.object(self.server.subprocess, "run", return_value=completed):
            self.assertIsNone(self.server._get_claude_org_id())

        self.assertIn("invalid organization ID", self.server._claude_org_id_diagnostic())

    def test_later_success_replaces_timeout_fallback(self):
        old_org_id = "11111111-2222-4333-8444-555555555555"
        new_org_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        self._write_cached_org(old_org_id)
        completed = subprocess.CompletedProcess(["claude"], 0, json.dumps({"orgId": new_org_id}), "")
        with mock.patch.object(
            self.server.subprocess,
            "run",
            side_effect=[subprocess.TimeoutExpired(["claude"], 15), completed],
        ):
            self.assertEqual(self.server._get_claude_org_id(), old_org_id)
            self.assertEqual(self.server._get_claude_org_id(), new_org_id)

        self.assertEqual(json.loads(self.state_file.read_text(encoding="utf-8")), {"org_id": new_org_id})
