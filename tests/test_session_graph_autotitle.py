"""Regression coverage for the text-only automatic title prompt."""

import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


class AutoTitlePromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        importlib.import_module("server")
        cls.session_graph = importlib.import_module("ccc_server.session_graph")

    def test_image_path_prefix_is_not_sent_to_the_title_model(self):
        image_path = "/tmp/pasted-images/annotation.png"
        first_message = f"{image_path} Describe the annotation and fix the overlap"
        captured = {}

        def run(argv, **kwargs):
            captured["instruction"] = argv[-1]
            return SimpleNamespace(returncode=0, stdout="Fix annotation overlap\n", stderr="")

        with mock.patch.object(
            self.session_graph._core,
            "_resolve_claude_bin",
            return_value={"available": True, "bin": "/usr/bin/claude"},
        ), mock.patch.object(self.session_graph.subprocess, "run", side_effect=run):
            result = self.session_graph._summarize_title_text(first_message, validate=True)

        self.assertTrue(result["ok"])
        self.assertNotIn(image_path, captured["instruction"])
        self.assertIn("Describe the annotation and fix the overlap", captured["instruction"])

    def test_handoff_opener_uses_actual_conversation_task(self):
        g = self.session_graph
        opener = "I am tired of waiting; pick this up 11111111-2222-4333-8444-555555555555"
        records = [
            {"type": "user", "message": {"role": "user", "content": opener}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file": "hidden-tool-input"}},
                {"type": "text", "text": "Review the client inbox and booking fixes. " * 5}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "hidden-tool-output"}]}},
            {"type": "user", "message": {"role": "user", "content": "Start with the inbox walkthrough."}},
        ]
        captured = {}

        def run(argv, **kwargs):
            captured["prompt"] = argv[-1]
            return SimpleNamespace(returncode=0, stdout="Review inbox and booking fixes", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text("\n".join(json.dumps(r) for r in records))
            with mock.patch.object(g._core, "_find_session_jsonl", return_value=transcript), \
                 mock.patch.object(g._core, "_resolve_claude_bin", return_value={"available": True, "bin": "claude"}), \
                 mock.patch.object(g._core, "_log_activity"), \
                 mock.patch.object(g, "_record_titler_target"), \
                 mock.patch.object(g.subprocess, "run", side_effect=run), \
                 mock.patch.object(g._core, "rename_session", return_value={"ok": True, "method": "jsonl"}), \
                 mock.patch.object(g, "_auto_title_record") as saved:
                result = g.summarize_session_title("test-session", validate=True)
        self.assertTrue(result["ok"])
        self.assertIn("client inbox and booking fixes", captured["prompt"])
        self.assertIn("inbox walkthrough", captured["prompt"])
        self.assertNotIn("hidden-tool", captured["prompt"])
        saved.assert_called_once_with("test-session", "Review inbox and booking fixes")

    def test_generic_handoff_answer_is_not_persisted_even_for_manual_regeneration(self):
        g = self.session_graph
        with mock.patch.object(g._core, "_resolve_claude_bin", return_value={"available": True, "bin": "claude"}), \
             mock.patch.object(g.subprocess, "run", return_value=SimpleNamespace(
                 returncode=0, stdout="Pick up and continue pending work", stderr="")):
            result = g._summarize_title_text("Resume the earlier session")
        self.assertFalse(result["ok"])

    def test_failed_regeneration_keeps_previous_saved_title(self):
        g = self.session_graph
        with mock.patch.object(g, "_extract_first_message", return_value="Fix the sidebar scroll jump on new messages"), \
             mock.patch.object(g._core, "_log_activity"), \
             mock.patch.object(g, "_summarize_title_text", return_value={"ok": True, "title": "Fix sidebar scrolling"}), \
             mock.patch.object(g._core, "rename_session", return_value={"ok": False, "error": "write failed"}), \
             mock.patch.object(g, "_auto_title_record") as saved, \
             mock.patch.object(g, "_claude_title_context") as context:
            result = g.summarize_session_title("test-session")
        self.assertFalse(result["ok"])
        saved.assert_not_called()
        context.assert_not_called()

    def test_context_samples_recent_clarification_without_tool_dump(self):
        g = self.session_graph
        def user(text):
            return json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text(user("continue") + user("Fix inbox history") +
                                  json.dumps({"type": "progress", "data": "x" * 600_000}) + "\n" +
                                  user("Also preserve booking confirmations"))
            with mock.patch.object(g._core, "_find_session_jsonl", return_value=transcript):
                context = g._claude_title_context("test-session")
        self.assertEqual(context, ["user: Fix inbox history", "user: Also preserve booking confirmations"])

    def test_context_finds_answer_after_large_cli_request_diagnostics(self):
        g = self.session_graph
        answer = "Review the client inbox and booking fixes. " * 6
        records = [
            {"type": "user", "message": {"role": "user", "content": "pick this up"}},
            {"type": "api-request-blob", "data": "x" * 850_000},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Checking the previous session."}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}},
            {"type": "api-request-blob", "data": "x" * 850_000},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text("\n".join(json.dumps(r) for r in records))
            with mock.patch.object(g._core, "_find_session_jsonl", return_value=transcript):
                self.assertEqual(g._claude_title_context("test-session"), ["assistant: " + answer.strip()])

    def test_specific_tasks_are_not_mistaken_for_generic_handoffs(self):
        g = self.session_graph
        self.assertFalse(g._title_is_generic_handoff("Continue sidebar pagination repair"))
        self.assertFalse(g._title_is_generic_handoff("Resume uploaded video processing"))
        self.assertTrue(g._title_is_generic_handoff("Resume previous session"))

    def test_regenerated_title_reaches_an_already_cached_title_map(self):
        g = self.session_graph
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(g._core, "SIDECAR_STATE_DIR", Path(tmp)), \
             mock.patch.dict(g._auto_titled_cache, {"key": None, "data": {}}):
            g._auto_title_record("test-session", "Pick up and continue pending work")
            self.assertEqual(g._auto_titled_session_ids()["test-session"], "Pick up and continue pending work")
            g._auto_title_record("test-session", "Review inbox and booking fixes")
            self.assertEqual(g._auto_titled_session_ids()["test-session"], "Review inbox and booking fixes")


if __name__ == "__main__":
    unittest.main()
