"""/api/repo/list must stay JSON-serializable, and send_json must never
let a bad payload escape as a dropped connection."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


class TestRepoUsageSignals(unittest.TestCase):
    def test_signals_are_serializable_without_a_projects_dir(self):
        """The early return (no ~/.claude/projects, e.g. a fresh install) used
        to hand back raw session sets, so /api/repo/list answered 500."""
        with patch.object(server, "PROJECTS_ROOT", Path("/nonexistent-ccc-projects")):
            signals = server._compute_repo_usage_signals(["/tmp/repo-a", "/tmp/repo-b"])
        json.dumps(signals)  # must not raise
        for info in signals.values():
            for window in ("d7", "d30", "all"):
                self.assertEqual(info["signals"][window]["sessions"], 0)
            self.assertEqual(info["score"], 0.0)

    def test_no_repo_paths_is_serializable(self):
        json.dumps(server._compute_repo_usage_signals([]))


class TestSendJsonSerializationFailure(unittest.TestCase):
    def test_unserializable_payload_becomes_a_500_body(self):
        handler = server.CommandCenterHandler.__new__(server.CommandCenterHandler)
        sent = {}

        handler.send_response = lambda status: sent.__setitem__("status", status)
        handler.send_header = lambda *_a: None
        handler.end_headers = lambda: None
        handler.headers = {}
        handler.wfile = type("W", (), {"write": staticmethod(lambda body: sent.__setitem__("body", body))})()
        handler._maybe_gzip = lambda body, ct: (body, None)

        with patch.object(server, "_record_server_error", lambda: None):
            handler.send_json({"leaked": {"session"}})

        self.assertEqual(sent["status"], 500)
        self.assertIn("serialization failed", json.loads(sent["body"])["error"])


if __name__ == "__main__":
    unittest.main()
