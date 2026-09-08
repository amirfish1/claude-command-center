import unittest
from ccc_server import codex_requests


class RequestRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = codex_requests.CodexRequestRegistry()
        self.registry.connect("one")
        self.sent = []

    def question(self, request_id=1, thread="task"):
        return self.registry.register(request_id, "item/tool/requestUserInput", {
            "threadId": thread, "turnId": "turn", "itemId": "item",
            "questions": [{"id": "choice", "question": "Choose", "options": []}]})

    def reply(self, key, answer=None, generation="one", thread="task", send=None):
        return self.registry.respond(key, answer or {"answers": {"choice": {"answers": ["Go"]}}},
                                     generation=generation, thread_id=thread,
                                     send=send or self.sent.append)

    def test_multiple_requests_and_id_types_are_preserved(self):
        a = self.question(1)
        b = self.question("1", "other")
        self.reply(b, thread="other")
        self.reply(a)
        self.assertEqual([r["id"] for r in self.sent], ["1", 1])
        self.assertEqual(self.registry.snapshot()["requests"], [])

    def test_reply_to_wrong_thread_or_old_generation_is_rejected(self):
        key = self.question()
        with self.assertRaises(ValueError):
            self.reply(key, thread="other")
        self.registry.connect("two")
        self.question()
        with self.assertRaises(ValueError):
            self.reply(key)
        self.assertEqual(self.sent, [])

    def test_unknown_question_answer_is_rejected_without_echoing_value(self):
        key = self.question()
        with self.assertRaises(ValueError) as caught:
            self.reply(key, {"answers": {"injected": {"answers": ["private-value"]}}})
        self.assertNotIn("private-value", str(caught.exception))
        self.assertEqual(self.sent, [])

    def test_uncertain_send_cannot_be_retried(self):
        key = self.question()
        def failed_send(_):
            raise OSError("connection ended")
        with self.assertRaises(ValueError):
            self.reply(key, send=failed_send)
        with self.assertRaises(ValueError):
            self.reply(key)
        self.assertEqual(self.registry.snapshot()["requests"][0]["state"], "uncertain")

    def test_server_resolution_is_scoped(self):
        self.question(1)
        self.question("1")
        self.registry.resolve(1, "task", "one")
        self.assertEqual(len(self.registry.snapshot()["requests"]), 1)
        self.registry.cancel_turn("task", "turn", "one")
        self.assertEqual(self.registry.snapshot()["requests"], [])

    def test_permissions_cannot_expand_requested_grants(self):
        key = self.registry.register(3, "item/permissions/requestApproval", {
            "threadId": "task", "turnId": "turn", "permissions": {"network": {"enabled": True}}})
        with self.assertRaises(ValueError):
            self.reply(key, {"permissions": {"filesystem": {"write": ["/outside"]}}, "scope": "turn"})
        self.reply(key, {"permissions": {}, "scope": "turn"})
        self.assertEqual(self.sent[0]["result"]["permissions"], {})

    def test_offered_approval_choices_are_enforced(self):
        key = self.registry.register(9, "item/commandExecution/requestApproval", {
            "threadId": "task", "turnId": "turn", "availableDecisions": ["accept", "decline"]})
        with self.assertRaises(ValueError):
            self.reply(key, {"decision": "acceptForSession"})
        self.reply(key, {"decision": "decline"})

    def test_unknown_requests_are_not_silently_enqueued(self):
        self.assertIsNone(self.registry.register(8, "future/request", {"threadId": "task"}))
        self.assertEqual(self.registry.snapshot()["requests"], [])

    def test_snapshot_is_detached_and_duplicate_request_is_single_card(self):
        first = self.question()
        self.assertEqual(self.question(), first)
        snapshot = self.registry.snapshot()
        snapshot["requests"][0]["params"]["questions"].clear()
        self.assertEqual(len(self.registry.snapshot()["requests"][0]["params"]["questions"]), 1)

    def test_resolved_request_cannot_be_registered_again(self):
        key = self.question()
        self.reply(key)
        with self.assertRaises(ValueError):
            self.question()
        self.assertEqual(len(self.sent), 1)

    def test_stale_cancellation_cannot_remove_new_generation_request(self):
        self.registry.connect("two")
        self.question()
        self.registry.cancel_turn("task", "turn", "one")
        self.assertEqual(len(self.registry.snapshot()["requests"]), 1)

    def test_delete_cancels_thread_requests_and_terminalizes_ids(self):
        self.question(17, "deleted")
        self.question(18, "other")

        self.registry.cancel_thread("deleted", "one")

        self.assertEqual([row["thread_id"] for row in self.registry.snapshot()["requests"]], ["other"])
        with self.assertRaisesRegex(ValueError, "already been resolved"):
            self.question(17, "deleted")

    def test_provider_specific_forms_accept_protocol_valid_content(self):
        for mode in ("openai/form", "openaiForm"):
            key = self.registry.register(mode, "mcpServer/elicitation/request", {
                "threadId": "task", "mode": mode, "requestedSchema": True})
            self.reply(key, {"action": "accept", "content": {"answer": "yes"}})

    def test_terminal_capacity_never_forgets_an_answered_id(self):
        registry = codex_requests.CodexRequestRegistry(capacity=1)
        registry.connect("g")
        params = {"threadId": "task", "turnId": "turn", "questions": []}
        for request_id in range(256):
            key = registry.register(request_id, "item/tool/requestUserInput", params)
            registry.respond(key, {"answers": {}}, generation="g", thread_id="task", send=self.sent.append)
        with self.assertRaises(ValueError):
            registry.register(256, "item/tool/requestUserInput", params)
        with self.assertRaises(ValueError):
            registry.register(0, "item/tool/requestUserInput", params)
        self.assertEqual(len(self.sent), 256)

    def test_standard_form_is_validated_in_bounded_child(self):
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
        key = self.registry.register("form", "mcpServer/elicitation/request", {
            "threadId": "task", "mode": "form", "requestedSchema": schema})
        self.reply(key, {"action": "accept", "content": {"answer": "yes"}})
        self.assertEqual(len(self.sent), 1)

    def test_untrusted_regex_cannot_stall_the_host(self):
        schema = {"type": "string", "pattern": "^(a+)+$"}
        with self.assertRaisesRegex(ValueError, "time limit"):
            codex_requests._validate_mcp_form("a" * 35 + "!", schema)
