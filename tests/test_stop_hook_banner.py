import importlib.util
import json
import os
import tempfile
import unittest
import io
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location(
    "ccc_stop_hook", os.path.join(os.path.dirname(__file__), "..", "hooks", "stop.py"))
stop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stop)


class FirstPromptSnippetTests(unittest.TestCase):
    def _snippet(self, *lines):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(l, separators=(",", ":")) for l in lines))
        try:
            return stop.first_prompt_snippet(f.name)
        finally:
            os.unlink(f.name)

    def test_user_message(self):
        got = self._snippet({"type": "user", "message": {"content": [{"type": "text", "text": "Fix the  bug"}]}})
        self.assertEqual(got, "Fix the bug")

    def test_queue_enqueue_and_truncation(self):
        got = self._snippet({"type": "queue-operation", "operation": "enqueue", "content": "x" * 100})
        self.assertEqual(got, "x" * 60 + "…")

    def test_missing_file(self):
        self.assertEqual(stop.first_prompt_snippet("/nonexistent"), "")


class OutcomeBannerTests(unittest.TestCase):
    def _banner(self, payload=None, lines=()):
        with tempfile.TemporaryDirectory() as directory:
            transcript = os.path.join(directory, "transcript.jsonl")
            with open(transcript, "w") as f:
                f.write("\n".join(json.dumps(line) for line in lines))
            data = {"session_id": "test-session", "transcript_path": transcript}
            data.update(payload or {})
            with patch.object(stop, "LIVE_STATE_DIR", directory), \
                    patch.object(stop, "session_title", return_value="Video summary"), \
                    patch.object(stop, "request_auto_title"), \
                    patch.object(stop, "notify_session") as notify, \
                    patch("sys.stdin", io.StringIO(json.dumps(data))):
                stop.main()
            return notify.call_args.args[2]

    def test_banner_uses_outcome_from_stop_payload(self):
        self.assertEqual(self._banner({"last_assistant_message": (
            "Full answer.\n<session-state>\n"
            "DID: Pulled the subtitles and summarized the video.\n"
            "NEXT_STEP_USER: None.\nCOMPLETION: {}\n</session-state>"
        )}), "Pulled the subtitles and summarized the video.")

    def test_banner_includes_required_user_action(self):
        self.assertEqual(self._banner({"last_assistant_message": (
            "<session-state>\nDID: Prepared the release.\n"
            "NEXT_STEP_USER: Approve publishing version 2.\n</session-state>"
        )}), "Prepared the release. Needs you: Approve publishing version 2.")

    def test_transcript_fallback_uses_last_assistant_text_only(self):
        self.assertEqual(self._banner(lines=[
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Old answer"}]}},
            {"type": "user", "message": {"content": "Summarize this video"}},
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "Private reasoning"}, {"type": "tool_use", "name": "Read"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "**Summary:** The video covers three practical tips."}]}},
        ]), "Summary: The video covers three practical tips.")

    def test_long_required_action_has_priority_over_outcome(self):
        action = "Approve publishing the prepared release after reviewing " + "the release notes " * 9
        body = self._banner({"last_assistant_message": (
            "<session-state>\nDID: " + "Prepared the release. " * 12
            + "\nNEXT_STEP_USER: " + action + "\n</session-state>"
        )})
        self.assertIn(action.strip(), body)
        self.assertLessEqual(len(body), 240)

    def test_no_stale_outcome_from_previous_turn(self):
        self.assertEqual(self._banner(lines=[
            {"type": "assistant", "message": {"content": "Previous result"}},
            {"type": "user", "message": {"content": "New request"}},
        ]), "Turn finished — open the session for details.")

    def test_fallback_and_length_limit(self):
        self.assertEqual(self._banner(), "Turn finished — open the session for details.")
        body = self._banner({"last_assistant_message": "word " * 200})
        self.assertLessEqual(len(body), 240)
        self.assertTrue(body.endswith("…"))


if __name__ == "__main__":
    unittest.main()
