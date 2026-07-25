import unittest

import server


class MessageModelMetadataTests(unittest.TestCase):
    def test_claude_user_and_assistant_events_preserve_model_metadata(self):
        user = server._parse_conversation_event({
            "type": "user",
            "timestamp": "2026-07-11T10:00:00Z",
            "message": {
                "role": "user",
                "model": "claude-sonnet-4-6",
                "content": "hello",
            },
        }, 1)
        assistant = server._parse_conversation_event({
            "type": "assistant",
            "timestamp": "2026-07-11T10:00:01Z",
            "message": {
                "id": "msg_1",
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "hi"}],
            },
        }, 2)

        self.assertEqual(user["model"], "claude-sonnet-4-6")
        self.assertEqual(assistant["model"], "claude-opus-4-8")

    def test_codex_parser_applies_turn_context_model_to_messages(self):
        turn_meta = {"model": "gpt-5.4", "reasoning_effort": "medium"}

        user = server._parse_codex_event({
            "type": "event_msg",
            "timestamp": "2026-07-11T10:00:00Z",
            "payload": {"type": "user_message", "message": "hello"},
        }, 1, codex_turn_meta=turn_meta)
        assistant = server._parse_codex_event({
            "type": "event_msg",
            "timestamp": "2026-07-11T10:00:01Z",
            "payload": {"type": "agent_message", "message": "hi"},
        }, 2, codex_turn_meta=turn_meta)

        self.assertEqual(user["model"], "gpt-5.4")
        self.assertEqual(user["reasoning_effort"], "medium")
        self.assertEqual(assistant["model"], "gpt-5.4")
        self.assertEqual(assistant["reasoning_effort"], "medium")


if __name__ == "__main__":
    unittest.main()
