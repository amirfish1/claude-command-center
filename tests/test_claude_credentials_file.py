"""Token lookup from ~/.claude/.credentials.json (Linux/Windows layout).

Claude Code nests the login under "claudeAiOauth"; the reader must find it.
No real token: HOME points at a temp dir and the env/keychain paths are off.
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ccc_server import recall_usage  # noqa: E402

FAKE_TOKEN = "sk-ant-test-XXXX-oauth-token"


class ClaudeCredentialsFileTests(unittest.TestCase):
    def _creds_with(self, payload):
        with tempfile.TemporaryDirectory() as home:
            claude_dir = pathlib.Path(home) / ".claude"
            claude_dir.mkdir()
            (claude_dir / ".credentials.json").write_text(json.dumps(payload))
            with mock.patch.dict("os.environ", {}, clear=False) as env, \
                    mock.patch.object(recall_usage.Path, "home", return_value=pathlib.Path(home)), \
                    mock.patch.object(recall_usage.subprocess, "run", side_effect=FileNotFoundError):
                env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
                return recall_usage._get_claude_credentials()

    def test_nested_claude_ai_oauth_access_token_is_used(self):
        creds = self._creds_with({"claudeAiOauth": {"accessToken": FAKE_TOKEN}})
        self.assertEqual(creds, {"oauth_token": FAKE_TOKEN})

    def test_flat_access_token_still_works(self):
        creds = self._creds_with({"accessToken": FAKE_TOKEN})
        self.assertEqual(creds, {"oauth_token": FAKE_TOKEN})


if __name__ == "__main__":
    unittest.main()
