import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


def _write_summary(path: Path, body: str = "# Matched Work\n\nDiscussed fresh search data.") -> None:
    path.write_text(
        """---
session_id: "sess-recall-123"
source_harness: "claude-code"
project: "/Users/example/project"
date: "2026-06-09"
time_start: "22:06"
---

"""
        + body,
        encoding="utf-8",
    )


class TestTotalRecallSearch(unittest.TestCase):
    def test_total_recall_search_returns_only_session_results(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            session_summary = root / "session.md"
            _write_summary(
                session_summary,
                "# Matched Work\n\nDiscussed <script>bad()</script> fresh search data.",
            )
            knowledge_doc = root / "doc.md"
            knowledge_doc.write_text("# Knowledge doc\n", encoding="utf-8")
            payload = {
                "results": [
                    {
                        "path": str(session_summary),
                        "source_harness": "claude-code",
                        "summary": "Discussed <script>bad()</script> fresh search data.",
                    },
                    {
                        "path": str(knowledge_doc),
                        "source_harness": "knowledge",
                        "summary": "A document match, not a conversation.",
                    },
                ],
            }
            proc = subprocess.CompletedProcess(
                ["brain"], 0, stdout=json.dumps(payload), stderr=""
            )

            with mock.patch.object(server, "_total_recall_command", return_value=["brain"]), \
                 mock.patch.object(server.subprocess, "run", return_value=proc):
                out = server.search_total_recall_sessions("fresh search", limit=5)

        self.assertNotIn("error", out)
        self.assertEqual(len(out["results"]), 1)
        hit = out["results"][0]
        self.assertEqual(hit["session_id"], "sess-recall-123")
        self.assertEqual(hit["cwd"], "/Users/example/project")
        self.assertEqual(hit["_source"], "recall")
        self.assertIn("fresh search data", hit["snippet"])
        self.assertNotIn("<script>", hit["snippet"])
        self.assertIn("&lt;script&gt;", hit["snippet"])

    def test_total_recall_search_respects_repo_scope(self):
        with tempfile.TemporaryDirectory() as td:
            summary = Path(td) / "session.md"
            _write_summary(summary)
            payload = {
                "results": [
                    {
                        "path": str(summary),
                        "source_harness": "claude-code",
                        "summary": "Discussed fresh search data.",
                    },
                ],
            }
            proc = subprocess.CompletedProcess(
                ["brain"], 0, stdout=json.dumps(payload), stderr=""
            )

            with mock.patch.object(server, "_total_recall_command", return_value=["brain"]), \
                 mock.patch.object(server.subprocess, "run", return_value=proc):
                out = server.search_total_recall_sessions(
                    "fresh search",
                    limit=5,
                    cwd_like="/Users/example/other-project",
                )

        self.assertEqual(out["results"], [])

    def test_total_recall_search_is_optional_when_binary_missing(self):
        with mock.patch.object(server, "_total_recall_command", return_value=None), \
             mock.patch.object(server.subprocess, "run") as run:
            out = server.search_total_recall_sessions("fresh search", limit=5)

        self.assertEqual(out["results"], [])
        run.assert_not_called()

    def test_total_recall_search_timeout_degrades_to_empty_results(self):
        with mock.patch.object(server, "_total_recall_command", return_value=["brain"]), \
             mock.patch.object(
                 server.subprocess,
                 "run",
                 side_effect=subprocess.TimeoutExpired(["brain"], 3),
             ):
            out = server.search_total_recall_sessions("fresh search", limit=5)

        self.assertEqual(out["results"], [])
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
