import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

FAKE = "sk-ant-oat01-fake-token"


class TestClaudeCredentialsFile(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("ccc_server.recall_usage")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        (self.home / ".claude").mkdir()

    def _read(self, payload):
        (self.home / ".claude" / ".credentials.json").write_text(json.dumps(payload))
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.mod.Path, "home", return_value=self.home), \
                mock.patch.object(self.mod.subprocess, "run", side_effect=FileNotFoundError):
            return self.mod._get_claude_credentials()

    def test_nested_claude_ai_oauth(self):
        self.assertEqual(
            self._read({"claudeAiOauth": {"accessToken": FAKE}}),
            {"oauth_token": FAKE},
        )

    def test_flat_access_token(self):
        self.assertEqual(self._read({"accessToken": FAKE}), {"oauth_token": FAKE})


if __name__ == "__main__":
    unittest.main()
