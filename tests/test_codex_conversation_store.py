"""Behavior of the bounded, connection-aware Codex conversation store."""
import unittest
from ccc_server import codex_conversation as conversation


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = conversation.CodexConversationStore(event_capacity=4)
        self.store.connect("first")

    def event(self, method, **params):
        self.store.record(method, {"threadId": "task", "turnId": "turn", **params})

    def items(self):
        return self.store.snapshot("task")["thread"]["turns"][0]["items"]

    def test_completion_replaces_deltas_and_preserves_phase(self):
        self.event("item/agentMessage/delta", itemId="reply", delta="partial")
        self.event("item/completed", item={"id": "reply", "type": "agentMessage", "text": "Final", "phase": "final_answer"})
        self.event("item/agentMessage/delta", itemId="reply", delta="late")
        self.assertEqual(self.items()[0]["text"], "Final")
        self.assertEqual(self.items()[0]["phase"], "final_answer")
        self.assertEqual(len(self.items()), 1)

    def test_completed_plan_wins_and_empty_turn_items_do_not_erase(self):
        self.event("item/plan/delta", itemId="plan", delta="draft")
        self.event("item/completed", item={"id": "plan", "type": "plan", "text": "Approved plan"})
        self.event("turn/completed", turn={"id": "turn", "status": "completed", "items": []})
        self.assertEqual(self.items()[0]["text"], "Approved plan")
        self.assertEqual(self.store.snapshot("task")["thread"]["turns"][0]["status"], "completed")

    def test_snapshot_race_preserves_newer_item_and_adds_history(self):
        before = self.store.cursor
        self.event("item/completed", item={"id": "reply", "type": "agentMessage", "text": "new"})
        self.store.hydrate({"id": "task", "turns": [{"id": "old", "items": [{"id": "user", "type": "userMessage", "content": []}]}, {"id": "turn", "items": [{"id": "reply", "type": "agentMessage", "text": "stale"}]}]}, before)
        turns = self.store.snapshot("task")["thread"]["turns"]
        self.assertEqual([t["id"] for t in turns], ["old", "turn"])
        self.assertEqual(turns[-1]["items"][0]["text"], "new")

    def test_thread_isolation_and_detached_results(self):
        self.event("item/completed", item={"id": "reply", "type": "agentMessage", "text": "one"})
        self.store.record("item/completed", {"threadId": "other", "turnId": "turn", "item": {"id": "reply", "type": "agentMessage", "text": "two"}})
        snap = self.store.snapshot("task")
        snap["thread"]["turns"][0]["items"][0]["text"] = "tampered"
        self.assertEqual(self.items()[0]["text"], "one")

    def test_event_gap_and_generation_change_require_resync(self):
        for i in range(5):
            self.event("turn/plan/updated", plan=[{"step": str(i), "status": "pending"}])
        self.assertTrue(self.store.events_since(0, "first")["resync_required"])
        self.assertFalse(self.store.events_since(4, "first")["resync_required"])
        self.store.connect("second")
        self.assertTrue(self.store.events_since(5, "first")["resync_required"])

    def test_global_notifications_and_unknown_items_are_retained(self):
        self.store.record("account/rateLimits/updated", {"rateLimits": {"primary": {"usedPercent": 17}}})
        self.event("item/completed", item={"id": "future", "type": "futureWidget", "data": {"title": "Kept"}})
        self.assertEqual(self.items()[0]["type"], "futureWidget")
        events = self.store.events_since(0, "first")["events"]
        self.assertEqual(events[0]["method"], "account/rateLimits/updated")

    def test_late_command_output_does_not_reactivate_turn(self):
        self.event("turn/completed", turn={"id": "turn", "status": "completed"})
        self.event("item/commandExecution/outputDelta", itemId="cmd", delta="late log")
        turn = self.store.snapshot("task")["thread"]["turns"][0]
        self.assertEqual(turn["status"], "completed")
        self.assertEqual(turn["items"][0]["aggregatedOutput"], "late log")

    def test_bounded_threads_and_text_report_truncation(self):
        store = conversation.CodexConversationStore(max_threads=2, text_limit=32)
        store.connect("g")
        for tid in ("a", "b", "c"):
            store.record("item/completed", {"threadId": tid, "turnId": "t", "item": {"id": "i", "type": "agentMessage", "text": "x" * 200}})
        self.assertIsNone(store.snapshot("a")["thread"])
        item = store.snapshot("c")["thread"]["turns"][0]["items"][0]
        self.assertLessEqual(len(item["text"]), 32)
        self.assertTrue(store.snapshot("c")["truncated"])

    def test_hydration_capacity_preserves_inflight_turn_and_item(self):
        for limits in ({"max_turns": 1}, {"max_items": 1}, {"thread_bytes": 1024}):
            store = conversation.CodexConversationStore(**limits)
            store.connect("g")
            store.record("item/completed", {"threadId": "t", "turnId": "new", "item": {"id": "live", "type": "agentMessage", "text": "LIVE"}})
            old_turn = "new" if "max_items" in limits else "old"
            store.hydrate({"id": "t", "turns": [{"id": old_turn, "items": [{"id": "history", "type": "agentMessage", "text": "old" * 500}]}]}, 0)
            items = [item for turn in store.snapshot("t")["thread"]["turns"] for item in turn["items"]]
            self.assertIn("LIVE", [item.get("text") for item in items])

    def test_hydration_preserves_newer_thread_and_turn_metadata(self):
        self.event("thread/status/changed", status="new")
        self.event("turn/plan/updated", plan=["new"])
        self.event("turn/diff/updated", diff="new diff")
        self.store.hydrate({"id": "task", "status": "old", "turns": [{"id": "turn", "plan": ["old"], "diff": "old diff", "items": []}]}, 0)
        thread = self.store.snapshot("task")["thread"]
        self.assertEqual(thread["status"], "new")
        self.assertEqual(thread["turns"][0]["plan"], ["new"])
        self.assertEqual(thread["turns"][0]["diff"], "new diff")

    def test_metadata_is_included_in_retention_budget(self):
        import json
        store = conversation.CodexConversationStore(thread_bytes=1024)
        store.connect("g")
        for i in range(30):
            store.record("turn/started", {"threadId": "t", "turn": {"id": "r", str(i): "x" * 200}})
        snap = store.snapshot("t")
        self.assertLessEqual(len(json.dumps(snap["thread"])), 1024)
        self.assertTrue(snap["truncated"])

    def test_completed_item_cannot_downgrade_or_lose_phase(self):
        self.event("item/completed", item={"id": "i", "type": "agentMessage", "text": "done", "phase": "final_answer"})
        self.event("turn/completed", turn={"id": "turn", "items": [{"id": "i", "type": "agentMessage", "text": "done"}]})
        self.event("item/started", item={"id": "i", "type": "agentMessage", "text": ""})
        self.event("item/agentMessage/delta", itemId="i", delta="late")
        self.assertEqual(self.items()[0]["text"], "done")
        self.assertEqual(self.items()[0]["phase"], "final_answer")

    def test_completed_command_output_is_authoritative(self):
        self.event("item/completed", item={"id": "cmd", "type": "commandExecution", "aggregatedOutput": "done"})
        self.event("item/commandExecution/outputDelta", itemId="cmd", delta=" late")
        self.assertEqual(self.items()[0]["aggregatedOutput"], "done")

    def test_global_capacity_preserves_inflight_thread_during_history_load(self):
        store = conversation.CodexConversationStore(max_threads=1)
        store.connect("g")
        store.record("item/completed", {"threadId": "live", "turnId": "t", "item": {"id": "i", "type": "agentMessage", "text": "new"}})
        store.hydrate({"id": "old", "turns": []}, 0)
        self.assertIsNotNone(store.snapshot("live")["thread"])

    def test_identity_and_method_strings_are_bounded(self):
        with self.assertRaises(ValueError):
            self.store.connect("g" * 10000)
        with self.assertRaises(ValueError):
            self.store.record("m" * 10000, {})
        with self.assertRaises(ValueError):
            self.store.hydrate({"id": "x" * 10000}, 0)

    def test_revert_invalidates_history_and_delete_keeps_only_tombstone(self):
        self.store.hydrate({"id": "task", "cwd": "/repo", "turns": []}, 0)
        self.event("item/completed", item={"id": "i", "type": "agentMessage", "text": "old"})
        self.event("thread/reverted")
        self.assertIsNone(self.store.snapshot("task")["thread"])
        self.store.hydrate({"id": "task", "cwd": "/repo", "turns": []}, self.store.cursor)
        self.event("item/completed", item={"id": "i", "type": "agentMessage", "text": "old"})
        self.event("thread/deleted")
        thread = self.store.snapshot("task")["thread"]
        self.assertTrue(thread["deleted"])
        self.assertEqual(thread["turns"], [])
        self.assertEqual(thread["cwd"], "/repo")

    def test_delete_purges_prior_payload_events_and_metadata(self):
        self.store.hydrate({"id": "task", "cwd": "/repo", "name": "secret title", "turns": []}, 0)
        self.event("item/completed", item={"id": "i", "type": "agentMessage", "text": "deleted secret"})
        self.event("thread/realtime/outputAudio/delta", itemId="audio", delta="private audio")
        self.event("item/mcpToolCall/progress", itemId="tool", message="private tool data")

        self.event("thread/deleted")

        thread = self.store.snapshot("task")["thread"]
        self.assertEqual(thread, {"id": "task", "cwd": "/repo", "archived": False,
                                  "deleted": True, "turns": []})
        stream = self.store.events_since(0, "first", "task")
        self.assertFalse(stream["resync_required"])
        self.assertEqual([event["method"] for event in stream["events"]], ["thread/deleted"])
        self.assertNotIn("deleted secret", str(stream))
        self.assertNotIn("private audio", str(stream))
        self.assertNotIn("private tool data", str(stream))

    def test_deleted_tombstone_rejects_late_events_and_history(self):
        before = self.store.cursor
        self.event("thread/deleted")

        self.event("item/completed", item={"id": "late", "type": "agentMessage", "text": "late secret"})
        hydrated = self.store.hydrate({"id": "task", "turns": [{"id": "old", "items": [
            {"id": "old-item", "type": "agentMessage", "text": "old secret"}]}]}, before)

        self.assertFalse(hydrated)
        self.assertEqual(self.store.snapshot("task")["thread"]["turns"], [])
        self.assertNotIn("late secret", str(self.store.events_since(0, "first", "task")))
        self.assertNotIn("old secret", str(self.store.snapshot("task")))
