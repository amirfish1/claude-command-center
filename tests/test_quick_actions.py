"""Action metadata comes from the answer footer without a second model call."""

import importlib
import json
import unittest


class QuickActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def parse(self, action, next_step="Say yes to a dashboard and worker restart."):
        return self.server._parse_session_state(
            "<session-state>\nDID: Checked the running version.\n"
            "INSIGHT: The running processes are older.\n"
            + "NEXT_STEP_USER: " + next_step + "\nNEXT_ACTION: " + action + "\n</session-state>")

    def test_originating_answer_supplies_label_and_scoped_reply(self):
        action = {"label": "Restart", "reply": "Yes, restart the dashboard and worker."}
        parsed = self.parse(json.dumps(action))
        self.assertEqual(parsed["next_action"], action)
        self.assertEqual(parsed["did"], "Checked the running version.")
        self.assertEqual(parsed["next_step_user"], "Say yes to a dashboard and worker restart.")

    def test_legacy_footer_does_not_invent_an_action(self):
        parsed = self.server._parse_session_state(
            "<session-state>\nNEXT_STEP_USER: Say yes to a restart.\n</session-state>")
        self.assertEqual(parsed["next_step_user"], "Say yes to a restart.")
        self.assertNotIn("next_action", parsed)

    def test_no_action_for_invalid_or_null_metadata(self):
        for raw in ["null", "not json", '"Restart"', '{"label":"Restart all","reply":"yes"}',
                    '{"label":"<script>","reply":"yes"}', '{"label":"Restart","reply":""}',
                    '{"label":"Restart","reply":"line\\nbreak"}',
                    json.dumps({"label":"Restart", "reply":"x" * 401}), "[" * 2500]:
            with self.subTest(raw=raw[:60]):
                self.assertNotIn("next_action", self.parse(raw))

    def test_action_requires_a_pending_user_step(self):
        action = json.dumps({"label": "Restart", "reply": "Yes, restart both."})
        for next_step in ["None.", "nothing", "N/A", "No action is needed", ""]:
            with self.subTest(next_step=next_step):
                self.assertNotIn("next_action", self.parse(action, next_step))

    def test_last_footer_wins_without_leaking_previous_action(self):
        first = '<session-state>\nNEXT_STEP_USER: Approve restart.\nNEXT_ACTION: {"label":"Restart","reply":"Restart both."}\n</session-state>'
        last = '<session-state>\nNEXT_STEP_USER: None.\n</session-state>'
        self.assertNotIn("next_action", self.server._parse_session_state(first + last))

    def test_empty_metadata_does_not_consume_the_following_field(self):
        parsed = self.server._parse_session_state(
            '<session-state>\nNEXT_ACTION: \nNEXT_STEP_USER: Approve restart.\n</session-state>')
        self.assertNotIn("next_action", parsed)
        self.assertEqual(parsed["next_step_user"], "Approve restart.")

    def test_originating_model_instructions_request_metadata(self):
        prompt = self.server._CCC_SESSION_STATE_SYSTEM_PROMPT
        self.assertIn("NEXT_ACTION:", prompt)
        self.assertIn("without another model call", prompt)
        self.assertIn('"label":"Restart"', prompt)
        self.assertIn("Otherwise use null", prompt)


if __name__ == "__main__":
    unittest.main()
