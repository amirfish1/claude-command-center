"""The live Codex SSE tail stamps turn_id like the snapshot readers (CCC-260).

The Codex live overlay reconciles its provisional rows against rollout rows by
turn_id. Rows streamed over /api/conversations/<id>/stream used to arrive
without it, so a finished reply rendered twice: the saved row plus a live copy
nothing could match or sweep.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server

TURN = "turn-abc"


def _rows():
    return [
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": TURN}},
        {"type": "turn_context", "payload": {"turn_id": TURN, "model": "example-model"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": TURN,
                                          "item": {"type": "AgentMessage", "id": "msg_1",
                                                   "content": [{"type": "Text", "text": "Done."}]}}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": TURN}},
    ]


class CodexStreamTurnIdTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name, "rollout.jsonl")
        self.path.write_text("\n".join(json.dumps(r) for r in _rows()) + "\n")
        patches = [
            mock.patch.object(server, "_resolve_conversation_reader",
                              return_value=(self.path, server._parse_codex_event)),
            mock.patch.object(server, "_resolve_codex_rollout_path", return_value=self.path),
            mock.patch.object(server, "_codex_rollout_is_stub", return_value=False),
            mock.patch.object(server, "_get_queued_events_for_session", return_value=[]),
            mock.patch.object(server.time, "sleep", side_effect=BrokenPipeError()),
        ]
        for name in ("_is_hermes_session", "_is_devin_session", "_is_devin_cli_session",
                     "_is_gemini_session", "_is_antigravity_session", "_is_grok_session",
                     "_session_acp_harness"):
            patches.append(mock.patch.object(server, name, return_value=False))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _stream(self, after_line):
        handler = server.CommandCenterHandler.__new__(server.CommandCenterHandler)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda *args: None
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler._stream_conversation("thread-1", after_line)
        chunks = [c for c in handler.wfile.getvalue().decode().split("\n\n") if c.startswith("data: ")]
        return [e for c in chunks for e in json.loads(c[len("data: "):])["events"]]

    def test_streamed_rows_carry_turn_id(self):
        events = self._stream(0)
        kinds = {e["type"]: e for e in events}
        self.assertEqual(kinds["assistant"]["turn_id"], TURN)
        self.assertEqual(kinds["result"]["turn_id"], TURN)

    def test_turn_context_before_the_cursor_still_applies(self):
        events = self._stream(2)
        self.assertTrue(events)
        self.assertTrue(all(e.get("turn_id") == TURN for e in events if isinstance(e.get("line"), int)))


if __name__ == "__main__":
    unittest.main()
