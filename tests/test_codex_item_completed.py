import unittest

import server


class CodexItemCompletedTests(unittest.TestCase):
    """Newer Codex threads (e.g. multi-agent mode) stop emitting the classic
    event_msg "user_message"/"agent_message" pair and wrap text in a nested
    `item` under event_msg/item_completed instead. Regression coverage for
    CCC-showed-nothing-from-the-transcript on these threads.
    """

    def test_user_message_item_renders_as_user_text(self):
        parsed = server._parse_codex_event({
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "UserMessage",
                    "id": "01a05379-df26",
                    "content": [{"type": "text", "text": "hello there", "text_elements": []}],
                },
            },
        }, 1)

        self.assertEqual(parsed["type"], "user_text")
        self.assertEqual(parsed["text"], "hello there")

    def test_agent_message_item_renders_as_assistant_text(self):
        parsed = server._parse_codex_event({
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "AgentMessage",
                    "id": "msg_1",
                    "content": [{"type": "Text", "text": "hi back"}],
                    "phase": "commentary",
                },
            },
        }, 2)

        self.assertEqual(parsed["type"], "assistant")
        self.assertEqual(parsed["blocks"], [{"kind": "text", "text": "hi back"}])

    def test_command_execution_item_is_ignored_to_avoid_duplicate_tool_cards(self):
        # CommandExecution/McpToolCall/FileChange items are dual-emitted as
        # response_item records (custom_tool_call, ...) that already render
        # via the branches below in _parse_codex_event; a branch here would
        # show every tool call twice.
        parsed = server._parse_codex_event({
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "CommandExecution", "id": "exec-1", "command": ["ls"]},
            },
        }, 3)

        self.assertIsNone(parsed)

    def test_empty_agent_message_item_yields_no_event(self):
        parsed = server._parse_codex_event({
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "AgentMessage", "id": "msg_2", "content": []},
            },
        }, 4)

        self.assertIsNone(parsed)

    def test_turn_meta_still_applies_to_item_completed_messages(self):
        turn_meta = {"model": "gpt-5.4", "reasoning_effort": "medium"}

        assistant = server._parse_codex_event({
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "AgentMessage", "id": "msg_3", "content": [{"type": "Text", "text": "hi"}]},
            },
        }, 5, codex_turn_meta=turn_meta)

        self.assertEqual(assistant["model"], "gpt-5.4")
        self.assertEqual(assistant["reasoning_effort"], "medium")


if __name__ == "__main__":
    unittest.main()
