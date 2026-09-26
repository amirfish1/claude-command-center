"""Codex guardian auto-reviews record their parent only in the rollout's
session_meta line. Without reading it they render as loose top-level rows
(folded into repeat groups) instead of slim child rows under the reviewed
session."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ccc_server import codex_parse


GUARDIAN_SOURCE = json.dumps({"subagent": {"other": "guardian"}})


class CodexGuardianParentTests(unittest.TestCase):
    def setUp(self):
        codex_parse._CODEX_SESSION_META_PARENT_CACHE.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _rollout(self, first_line):
        path = Path(self._tmp.name) / "rollout.jsonl"
        path.write_text(json.dumps(first_line) + "\n" + '{"type":"event_msg"}\n', encoding="utf-8")
        return path

    def test_guardian_parent_read_from_session_meta(self):
        path = self._rollout({"type": "session_meta", "payload": {"parent_thread_id": "parent-123"}})
        self.assertEqual(
            codex_parse._codex_subagent_parent_id({"source": GUARDIAN_SOURCE}, path),
            "parent-123",
        )

    def test_thread_spawn_parent_needs_no_file_read(self):
        source = json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": "spawner-9"}}})
        with mock.patch("builtins.open", side_effect=AssertionError("file read")):
            self.assertEqual(
                codex_parse._codex_subagent_parent_id({"source": source}, "/nonexistent"),
                "spawner-9",
            )

    def test_top_level_rows_never_open_the_rollout(self):
        # Perf budget: only subagent rows may pay for a file read.
        with mock.patch("builtins.open", side_effect=AssertionError("file read")):
            for source in ("cli", "vscode", "", None, json.dumps({"exec": {}})):
                self.assertEqual(
                    codex_parse._codex_subagent_parent_id({"source": source}, "/nonexistent"),
                    "",
                )

    def test_session_meta_read_once_per_path(self):
        path = self._rollout({"type": "session_meta", "payload": {"parent_thread_id": "p"}})
        codex_parse._codex_subagent_parent_id({"source": GUARDIAN_SOURCE}, path)
        with mock.patch("builtins.open", side_effect=AssertionError("re-read")):
            self.assertEqual(
                codex_parse._codex_subagent_parent_id({"source": GUARDIAN_SOURCE}, path),
                "p",
            )

    def test_missing_or_malformed_rollout_is_parentless(self):
        self.assertEqual(
            codex_parse._codex_subagent_parent_id({"source": GUARDIAN_SOURCE}, "/nonexistent"),
            "",
        )
        path = self._rollout({"type": "turn_context", "payload": {"parent_thread_id": "nope"}})
        self.assertEqual(
            codex_parse._codex_subagent_parent_id({"source": GUARDIAN_SOURCE}, path),
            "",
        )


if __name__ == "__main__":
    unittest.main()
