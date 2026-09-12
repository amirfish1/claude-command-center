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

    def test_codex_turn_context_event_yields_turn_id(self):
        # Verified against real rollouts (2026-09-12): turn_context.payload
        # carries turn_id on every turn, so the live app-server overlay
        # (ccc_server/codex_live_events.py) can dedupe its provisional
        # "<turn_id>:<item_id>" rows against the matching rollout row.
        meta = server._codex_turn_meta_from_event({
            "type": "turn_context",
            "payload": {"turn_id": "01a096b1-2fd9-7983-abf4-06d155120491", "model": "gpt-5.6-sol"},
        })
        self.assertEqual(meta["turn_id"], "01a096b1-2fd9-7983-abf4-06d155120491")
        self.assertEqual(meta["model"], "gpt-5.6-sol")

    def test_codex_parser_applies_turn_context_turn_id_to_events(self):
        turn_meta = {"turn_id": "01a096b1-2fd9-7983-abf4-06d155120491"}

        user = server._parse_codex_event({
            "type": "event_msg",
            "timestamp": "2026-07-11T10:00:00Z",
            "payload": {"type": "user_message", "message": "hello"},
        }, 1, codex_turn_meta=turn_meta)
        tool = server._parse_codex_event({
            "type": "response_item",
            "timestamp": "2026-07-11T10:00:01Z",
            "payload": {"type": "function_call", "name": "exec_command", "call_id": "call_1",
                        "arguments": "{\"cmd\": \"ls\"}"},
        }, 2, codex_turn_meta=turn_meta)
        result = server._parse_codex_event({
            "type": "response_item",
            "timestamp": "2026-07-11T10:00:02Z",
            "payload": {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        }, 3, codex_turn_meta=turn_meta)

        self.assertEqual(user["turn_id"], "01a096b1-2fd9-7983-abf4-06d155120491")
        self.assertEqual(tool["turn_id"], "01a096b1-2fd9-7983-abf4-06d155120491")
        self.assertEqual(result["turn_id"], "01a096b1-2fd9-7983-abf4-06d155120491")


if __name__ == "__main__":
    unittest.main()
