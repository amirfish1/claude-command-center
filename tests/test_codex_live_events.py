import unittest
from unittest import mock

import server  # noqa: F401 - adopts ccc_server.codex/server-level helpers _core proxies to
from ccc_server import codex_live_events as live_events


class MapItemTests(unittest.TestCase):
    """Pure mapping: one native app-server item -> zero or more rollout-shaped
    events, each carrying live_key/provisional/turn_id and no `line`."""

    def _assert_live_fields(self, event, turn_id, item_id):
        self.assertEqual(event["turn_id"], turn_id)
        self.assertEqual(event["live_key"], f"{turn_id}:{item_id}")
        self.assertIs(event["provisional"], True)
        self.assertNotIn("line", event)

    def test_user_message_item_maps_to_user_text(self):
        events = live_events._map_item("turn-1", {
            "type": "userMessage", "id": "um-1", "text": "hello codex",
        })
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "user_text")
        self.assertEqual(events[0]["text"], "hello codex")
        self._assert_live_fields(events[0], "turn-1", "um-1")

    def test_agent_message_item_maps_to_assistant_text_block(self):
        events = live_events._map_item("turn-1", {
            "type": "agentMessage", "id": "msg-1", "text": "hi back",
        })
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "assistant")
        self.assertEqual(events[0]["blocks"], [{"kind": "text", "text": "hi back"}])
        self._assert_live_fields(events[0], "turn-1", "msg-1")

    def test_empty_agent_message_produces_no_event(self):
        self.assertEqual(live_events._map_item("turn-1", {"type": "agentMessage", "id": "msg-2", "text": "  "}), [])

    def test_reasoning_item_maps_to_thinking_block(self):
        events = live_events._map_item("turn-1", {
            "type": "reasoning", "id": "rs-1", "summary": ["thinking about it", "more"],
        })
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["blocks"], [{"kind": "thinking", "text": "thinking about it\n\nmore"}])
        self._assert_live_fields(events[0], "turn-1", "rs-1")

    def test_command_execution_item_maps_to_tool_use_and_result_when_complete(self):
        events = live_events._map_item("turn-1", {
            "type": "commandExecution", "id": "exec-abc", "command": ["/bin/zsh", "-lc", "ls -la"],
            "status": "completed", "aggregatedOutput": "file1\nfile2",
        })
        self.assertEqual(len(events), 2)
        tool_use, tool_result = events
        self.assertEqual(tool_use["type"], "assistant")
        block = tool_use["blocks"][0]
        self.assertEqual(block["kind"], "tool_use")
        self.assertEqual(block["name"], "Bash")
        self.assertEqual(block["id"], "exec-abc")
        self.assertIn("ls -la", block["command"])
        self._assert_live_fields(tool_use, "turn-1", "exec-abc")
        self.assertEqual(tool_result["type"], "tool_result")
        self.assertEqual(tool_result["tool_use_id"], "exec-abc")
        self.assertEqual(tool_result["text"], "file1\nfile2")
        self.assertFalse(tool_result["is_error"])
        self.assertEqual(tool_result["live_key"], "turn-1:exec-abc:result")

    def test_command_execution_item_in_progress_has_no_result_yet(self):
        events = live_events._map_item("turn-1", {
            "type": "commandExecution", "id": "exec-def", "command": ["ls"], "status": "inProgress",
        })
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["blocks"][0]["kind"], "tool_use")

    def test_command_execution_failed_marks_result_as_error(self):
        events = live_events._map_item("turn-1", {
            "type": "commandExecution", "id": "exec-ghi", "command": ["false"],
            "status": "failed", "aggregatedOutput": "boom",
        })
        self.assertTrue(events[1]["is_error"])

    def test_mcp_tool_call_item_maps_to_tool_use(self):
        events = live_events._map_item("turn-1", {
            "type": "mcpToolCall", "id": "exec-mcp1", "server": "hunch", "tool": "hunch_context",
            "status": "completed", "result": "some result text",
        })
        self.assertEqual(len(events), 2)
        block = events[0]["blocks"][0]
        self.assertEqual(block["name"], "hunch_context")
        self.assertEqual(block["detail"], "hunch.hunch_context")
        self.assertEqual(events[1]["tool_use_id"], "exec-mcp1")

    def test_web_search_item_maps_to_tool_use(self):
        events = live_events._map_item("turn-1", {
            "type": "webSearch", "id": "exec-web1", "query": "codex app-server protocol",
        })
        self.assertEqual(len(events), 1)
        block = events[0]["blocks"][0]
        self.assertEqual(block["name"], "web_search")
        self.assertEqual(block["detail"], "codex app-server protocol")

    def test_file_change_item_maps_to_apply_patch_tool_use(self):
        events = live_events._map_item("turn-1", {
            "type": "fileChange", "id": "exec-fc1",
            "changes": [{"path": "a.py"}, {"path": "b.py"}],
            "status": "completed", "aggregatedOutput": "applied",
        })
        block = events[0]["blocks"][0]
        self.assertEqual(block["name"], "apply_patch")
        self.assertEqual(block["detail"], "a.py (+1 more)")

    def test_unknown_item_type_is_ignored(self):
        self.assertEqual(live_events._map_item("turn-1", {"type": "SomethingNew", "id": "x"}), [])

    def test_item_without_id_is_ignored(self):
        self.assertEqual(live_events._map_item("turn-1", {"type": "agentMessage", "text": "hi"}), [])


class LiveTurnsFromSnapshotTests(unittest.TestCase):
    def _snapshot(self, turns):
        return {"ok": True, "generation": "gen-1", "cursor": 42, "connected": True,
                "thread": {"id": "thread-1", "turns": turns}, "truncated": False}

    def test_maps_turn_with_items_and_plan(self):
        snapshot = self._snapshot([{
            "id": "turn-1", "status": "in_progress",
            "plan": [{"step": "Read the file", "status": "completed"},
                     {"step": "Write the fix", "status": "in_progress"}],
            "items": [
                {"type": "userMessage", "id": "um-1", "text": "please fix it"},
                {"type": "agentMessage", "id": "msg-1", "text": "on it"},
            ],
        }])
        turns = live_events.live_turns_from_snapshot(snapshot)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["turn_id"], "turn-1")
        self.assertEqual(turns[0]["status"], "in_progress")
        types = [e["type"] for e in turns[0]["events"]]
        self.assertEqual(types, ["user_text", "assistant", "assistant"])
        plan_event = turns[0]["events"][-1]
        self.assertEqual(plan_event["blocks"][0]["kind"], "plan")
        self.assertEqual(plan_event["blocks"][0]["entries"], [
            {"content": "Read the file", "status": "completed"},
            {"content": "Write the fix", "status": "in_progress"},
        ])
        self.assertEqual(plan_event["live_key"], "turn-1:plan")

    def test_turn_without_id_is_skipped(self):
        snapshot = self._snapshot([{"status": "in_progress", "items": []}])
        self.assertEqual(live_events.live_turns_from_snapshot(snapshot), [])

    def test_missing_thread_returns_empty_list(self):
        self.assertEqual(live_events.live_turns_from_snapshot({"ok": True, "thread": None}), [])
        self.assertEqual(live_events.live_turns_from_snapshot({}), [])

    def test_output_is_bounded_by_max_turns_and_max_items(self):
        many_turns = [
            {"id": f"turn-{i}", "status": "completed",
             "items": [{"type": "agentMessage", "id": f"msg-{i}-{j}", "text": f"chunk {j}"} for j in range(10)]}
            for i in range(10)
        ]
        snapshot = self._snapshot(many_turns)
        turns = live_events.live_turns_from_snapshot(snapshot, max_turns=2, max_items=3)
        self.assertEqual(len(turns), 2)
        # The last two turns (closest to "now") should survive, oldest first.
        self.assertEqual([t["turn_id"] for t in turns], ["turn-8", "turn-9"])
        for turn in turns:
            self.assertLessEqual(len(turn["events"]), 3)


class DispatchLiveTranscriptTests(unittest.TestCase):
    """Shape of the `live-transcript` action added to codex_client_dispatch."""

    def test_dispatch_returns_generation_cursor_turns_and_requests(self):
        from ccc_server import codex_client as client

        tid = "thread-live-1"
        client.CODEX_CONVERSATIONS.record("item/completed", {
            "threadId": tid, "turnId": "turn-1",
            "item": {"type": "agentMessage", "id": "msg-1", "text": "hi"},
        })
        with mock.patch.object(client, "_client_scope", side_effect=lambda m, p, c: p):
            result = client.codex_client_dispatch("live-transcript", {
                "context": {"thread_id": tid, "repo_path": "/test-repo"},
            })
        self.assertTrue(result["ok"])
        self.assertIn("generation", result)
        self.assertIn("cursor", result)
        self.assertIn("requests", result)
        self.assertEqual(len(result["turns"]), 1)
        self.assertEqual(result["turns"][0]["turn_id"], "turn-1")
        self.assertEqual(result["turns"][0]["events"][0]["live_key"], "turn-1:msg-1")


if __name__ == "__main__":
    unittest.main()
