import copy
import unittest
from unittest import mock

import server
from ccc_server import codex_client as client


class Transport:
    def __init__(self):
        self.sent = []
    def send_json(self, payload):
        self.sent.append(copy.deepcopy(payload))


class ClientProtocolTests(unittest.TestCase):
    def setUp(self):
        self.transport = Transport()
        client.codex_client_connect(self.transport)
        client._CLIENT_ACTIONS.clear()
        client._CLIENT_LIFECYCLE_PENDING.clear()
        client._CLIENT_LIFECYCLE_LAST_ERROR = None

    def test_registered_question_can_be_answered_on_original_connection(self):
        client.codex_client_observe({"id": 7, "method": "item/tool/requestUserInput", "params": {
            "threadId": "task", "turnId": "turn", "questions": [{"id": "q"}]}}, self.transport)
        pending = client.CODEX_REQUESTS.snapshot("task")["requests"][0]
        with mock.patch.object(client, "_client_scope", side_effect=lambda m, p, c: p), \
             mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", self.transport):
            result = client.codex_client_dispatch("respond", {
                "key": pending["key"], "generation": pending["generation"],
                "context": {"thread_id": "task", "repo_path": "/test-repo"},
                "result": {"answers": {"q": {"answers": ["yes"]}}}})
        self.assertTrue(result["ok"])
        self.assertEqual(self.transport.sent[0]["id"], 7)

    def test_new_answer_clears_matching_legacy_approval_only(self):
        client.codex_client_observe({"id": 7, "method": "item/commandExecution/requestApproval", "params": {
            "threadId": "task", "turnId": "turn"}}, self.transport)
        pending = client.CODEX_REQUESTS.snapshot("task")["requests"][0]
        states = {"task": {"pending_approval_request": {"request_id_raw": 7},
            "thread_needs_approval": True, "active_flags": ["waitingOnApproval"]}}
        with mock.patch.object(client, "_client_scope", side_effect=lambda m, p, c: p), \
             mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", self.transport), \
             mock.patch.object(server, "_CODEX_APP_SERVER_THREAD_STATE", states):
            result = client.codex_client_dispatch("respond", {"key": pending["key"],
                "generation": pending["generation"], "context": {"thread_id": "task"}, "result": {"decision": "decline"}})
        self.assertTrue(result["ok"])
        self.assertNotIn("pending_approval_request", states["task"])
        self.assertFalse(states["task"]["thread_needs_approval"])

    def test_unknown_host_requests_receive_error_not_silence(self):
        handled = client.codex_client_observe({"id": "unknown", "method": "future/request", "params": {}}, self.transport)
        self.assertTrue(handled)
        self.assertEqual(self.transport.sent[0]["error"]["code"], -32601)

    def test_retired_connection_cannot_cancel_current_question(self):
        old = self.transport
        self.transport = Transport()
        client.codex_client_connect(self.transport)
        client.codex_client_observe({"id": 1, "method": "item/tool/requestUserInput", "params": {
            "threadId": "task", "turnId": "turn", "questions": []}}, self.transport)
        client.codex_client_observe({"method": "turn/completed", "params": {
            "threadId": "task", "turn": {"id": "turn"}}}, old)
        self.assertEqual(len(client.CODEX_REQUESTS.snapshot("task")["requests"]), 1)

    def test_null_params_and_volatile_calls_stay_on_current_owner(self):
        with mock.patch.object(server, "_ensure_codex_app_server", return_value=self.transport), \
             mock.patch.object(server, "_codex_app_server_request", return_value={"result": {}}) as request:
            self.assertTrue(client._client_rpc("account/rateLimits/read", None)["ok"])
        request.assert_called_once_with("account/rateLimits/read", None, timeout=25, _route=False, _null_params=True,
                                        _expected_generation=client.CODEX_CONVERSATIONS.generation)

    def catalog(self, read_only=False):
        return {"ok": True, "methods": [{"method": "account/logout", "title": "Sign out", "available": True,
            "read_only": read_only, "params_type": "null", "params_schema": {"type": "object", "additionalProperties": False}}]}

    def test_same_action_receipt_does_not_repeat_a_mutation(self):
        from ccc_server import codex_capabilities
        data = {"method": "account/logout", "params": {}, "context": {}, "action_id": "action-0001", "generation": client.CODEX_CONVERSATIONS.generation}
        with mock.patch.object(codex_capabilities, "get_codex_catalog", return_value=self.catalog()), \
             mock.patch.object(client, "_client_rpc", return_value={"ok": True, "result": {}}) as rpc:
            self.assertTrue(client._client_operation(data)["ok"])
            self.assertTrue(client._client_operation(data)["ok"])
        self.assertEqual(rpc.call_count, 1)
        self.assertEqual(rpc.call_args.args[1], None)

    def test_uncertain_mutation_is_never_automatically_retried(self):
        from ccc_server import codex_capabilities
        data = {"method": "account/logout", "params": {}, "context": {}, "action_id": "action-0002", "generation": client.CODEX_CONVERSATIONS.generation}
        with mock.patch.object(codex_capabilities, "get_codex_catalog", return_value=self.catalog()), \
             mock.patch.object(client, "_client_rpc", return_value={"ok": False, "uncertain": True, "error": "Timed out"}) as rpc:
            client._client_operation(data)
            self.assertTrue(client._client_operation(data)["uncertain"])
        self.assertEqual(rpc.call_count, 1)

    def test_unknown_method_is_rejected_before_reading_task(self):
        from ccc_server import codex_capabilities
        with mock.patch.object(codex_capabilities, "get_codex_catalog", return_value=self.catalog()), \
             mock.patch.object(server, "resolve_repo_path", return_value="/test"), \
             mock.patch.object(client, "_client_read_thread", return_value={"cwd": "/test"}) as read:
            with self.assertRaises(ValueError):
                client._client_operation({"method": "unknown/action", "params": {"threadId": "task"},
                    "context": {"thread_id": "task", "repo_path": "/test"}, "action_id": "action-0003"})
        read.assert_not_called()

    def test_new_task_has_empty_history_before_first_message(self):
        with mock.patch.object(client, "_client_scope", side_effect=lambda m, p, c: p), \
             mock.patch.object(client, "_client_read_thread", return_value={"id": "fresh", "cwd": "/test"}), \
             mock.patch.object(client, "_client_rpc", return_value={"ok": False, "code": -32600,
                 "error": "thread is not materialized yet; thread/turns/list is unavailable before first user message"}):
            result = client._client_history({"context": {"thread_id": "fresh", "repo_path": "/test"}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["thread"]["turns"], [])

    def test_result_cache_eviction_does_not_allow_replaying_an_action(self):
        from ccc_server import codex_capabilities
        generation = client.CODEX_CONVERSATIONS.generation
        data = {"method": "account/logout", "params": {}, "context": {}, "generation": generation}
        with mock.patch.object(codex_capabilities, "get_codex_catalog", return_value=self.catalog()), \
             mock.patch.object(client, "_client_rpc", return_value={"ok": True, "result": {}}) as rpc:
            for number in range(130):
                client._client_operation({**data, "action_id": "receipt-%04d" % number})
            with self.assertRaises(ValueError):
                client._client_operation({**data, "action_id": "receipt-0000"})
        self.assertEqual(rpc.call_count, 130)

    def test_old_generation_cannot_submit_an_action(self):
        from ccc_server import codex_capabilities
        old_generation = client.CODEX_CONVERSATIONS.generation
        client.codex_client_connect(Transport())
        with mock.patch.object(codex_capabilities, "get_codex_catalog", return_value=self.catalog()), \
             mock.patch.object(client, "_client_rpc") as rpc:
            result = client._client_operation({"method": "account/logout", "params": {}, "context": {},
                "generation": old_generation, "action_id": "receipt-old"})
        self.assertEqual(result["code"], "stale_connection")
        rpc.assert_not_called()

    def test_falsey_non_object_parameters_are_rejected(self):
        from ccc_server import codex_capabilities
        with mock.patch.object(codex_capabilities, "get_codex_catalog", return_value=self.catalog()):
            for field in ("params", "context"):
                for value in ([], "", False, 0, None):
                    data = {"method": "account/logout", "params": {}, "context": {}, field: value}
                    with self.assertRaises(ValueError):
                        client._client_operation(data)

    def test_terminal_handle_is_bound_before_command_finishes(self):
        from ccc_server import codex_capabilities
        catalog = {"ok": True, "methods": [{"method": method, "available": True, "read_only": False,
            "params_schema": {"type": "object"}} for method in ("command/exec", "command/exec/write")]}
        common = {"generation": client.CODEX_CONVERSATIONS.generation, "context": {"repo_path": "/test"}}
        calls = []
        def rpc(method, params, **kwargs):
            calls.append(method)
            if method == "command/exec":
                nested = client._client_operation({**common, "method": "command/exec/write",
                    "params": {"processId": "running"}, "action_id": "stdin-0001"})
                self.assertTrue(nested["ok"])
            return {"ok": True, "result": {}}
        with mock.patch.object(codex_capabilities, "get_codex_catalog", return_value=catalog), \
             mock.patch.object(server, "resolve_repo_path", return_value="/test"), \
             mock.patch.object(client, "_client_rpc", side_effect=rpc):
            result = client._client_operation({**common, "method": "command/exec",
                "params": {"processId": "running", "command": ["echo"]}, "action_id": "command-0001"})
        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["command/exec", "command/exec/write"])
        self.assertNotIn("running", client._CLIENT_HANDLES)

    def test_mutation_authorization_reads_fresh_thread_context(self):
        with mock.patch.object(server, "resolve_repo_path", return_value="/test"), \
             mock.patch.object(client, "_client_read_thread", return_value={"cwd": "/test"}) as read:
            client._client_scope("thread/name/set", {"threadId": "task", "name": "new"},
                                 {"thread_id": "task", "repo_path": "/test"})
        read.assert_called_once_with("task", fresh=True)

    def test_read_crossing_reconnect_is_not_labeled_with_new_generation(self):
        def request(*args, **kwargs):
            client.codex_client_connect(Transport())
            return {"result": {"old": True}}
        with mock.patch.object(server, "_ensure_codex_app_server", return_value=self.transport), \
             mock.patch.object(server, "_codex_app_server_request", side_effect=request):
            result = client._client_rpc("account/read", {})
        self.assertFalse(result["ok"])
        self.assertTrue(result["resync_required"])
        self.assertNotIn("result", result)

    def test_malformed_response_context_returns_error_envelope(self):
        for context in ([1], "bad", 42, True):
            result = client.codex_client_dispatch("respond", {"context": context})
            self.assertFalse(result["ok"])

    def test_explicit_native_creation_updates_sidebar_registry(self):
        import os
        with mock.patch.dict(os.environ, {"CCC_EPHEMERAL": ""}), \
             mock.patch.object(server, "_codex_thread_registry_upsert") as registry, \
             mock.patch.object(server, "_codex_app_server_transport_kind", return_value="stdio"):
            client._client_sync_lifecycle("thread/start", {}, {"result": {"thread": {"id": "new-task", "cwd": "/test", "name": "New task"}}}, "/test")
        self.assertEqual(registry.call_args.args[0], "new-task")
        self.assertEqual(registry.call_args.kwargs["visibility"], "user-visible")

    def test_fork_registration_falls_back_to_requested_parent(self):
        import os
        with mock.patch.dict(os.environ, {"CCC_EPHEMERAL": ""}), \
             mock.patch.object(server, "_codex_thread_registry_upsert") as registry, \
             mock.patch.object(server, "_codex_app_server_transport_kind", return_value="stdio"):
            client._client_sync_lifecycle("thread/fork", {"threadId": "parent"},
                {"result": {"thread": {"id": "child", "cwd": "/test"}}}, "/test")
        self.assertEqual(registry.call_args.kwargs["parent_session_id"], "parent")

    def test_preview_never_writes_real_sidebar_registry(self):
        import os
        with mock.patch.dict(os.environ, {"CCC_EPHEMERAL": "1"}), \
             mock.patch.object(server, "_codex_thread_registry_upsert") as registry:
            client._client_sync_lifecycle("thread/start", {}, {"result": {"thread": {"id": "preview", "cwd": "/test"}}}, "/test")
        registry.assert_not_called()

    def test_native_delete_tombstone_can_finish_scoped_ui_polling(self):
        client.CODEX_CONVERSATIONS.hydrate({"id": "deleted", "cwd": "/test", "turns": []}, 0)
        client.CODEX_CONVERSATIONS.record("thread/deleted", {"threadId": "deleted"})
        with mock.patch.object(server, "resolve_repo_path", return_value="/test"), \
             mock.patch.object(client, "_client_rpc") as rpc:
            result = client.codex_client_dispatch("state", {"context": {"thread_id": "deleted", "repo_path": "/test"}})
        self.assertTrue(result["thread"]["deleted"])
        rpc.assert_not_called()

    def test_native_delete_purges_requests_and_skips_legacy_recreation(self):
        client.CODEX_CONVERSATIONS.record("item/completed", {"threadId": "deleted", "turnId": "turn",
            "item": {"id": "reply", "type": "agentMessage", "text": "deleted secret"}})
        client.codex_client_observe({"id": 91, "method": "item/tool/requestUserInput", "params": {
            "threadId": "deleted", "turnId": "turn", "questions": [{"id": "q"}]}}, self.transport)
        states = {"deleted": {"pending_approval_request": {"request_id_raw": 91},
            "compaction_recovery": {"message": "deleted recovery"}}}
        turns = {"turn": "deleted"}
        with mock.patch.object(server, "_CODEX_APP_SERVER_THREAD_STATE", states), \
             mock.patch.object(server, "_CODEX_APP_SERVER_TURN_THREAD", turns), \
             mock.patch.object(server, "_save_codex_app_server_state_unlocked"), \
             mock.patch.object(client, "_client_enqueue_lifecycle", return_value=True):
            handled = client.codex_client_observe({"method": "thread/deleted",
                "params": {"threadId": "deleted"}}, self.transport)
        self.assertTrue(handled)
        self.assertEqual(client.CODEX_REQUESTS.snapshot("deleted")["requests"], [])
        self.assertNotIn("deleted", states)
        self.assertNotIn("turn", turns)
        self.assertNotIn("deleted secret", str(client.CODEX_CONVERSATIONS.events_since(
            0, client.CODEX_CONVERSATIONS.generation, "deleted")))

    def test_late_deleted_thread_messages_cannot_recreate_state_or_questions(self):
        client.CODEX_CONVERSATIONS.record("thread/deleted", {"threadId": "deleted"})
        states = {}
        with mock.patch.object(server, "_CODEX_APP_SERVER_THREAD_STATE", states):
            handled_event = client.codex_client_observe({"method": "item/completed", "params": {
                "threadId": "deleted", "turnId": "late", "item": {
                    "id": "late", "type": "agentMessage", "text": "late secret"}}}, self.transport)
            handled_request = client.codex_client_observe({"id": 101,
                "method": "item/tool/requestUserInput", "params": {
                    "threadId": "deleted", "turnId": "late", "questions": [{"id": "q"}]}}, self.transport)
        self.assertTrue(handled_event)
        self.assertTrue(handled_request)
        self.assertEqual(states, {})
        self.assertEqual(client.CODEX_REQUESTS.snapshot("deleted")["requests"], [])
        self.assertNotIn("late secret", str(client.CODEX_CONVERSATIONS.snapshot("deleted")))

    def test_late_thread_read_response_does_not_recreate_deleted_legacy_state(self):
        client.CODEX_CONVERSATIONS.record("thread/deleted", {"threadId": "deleted"})
        states = {}
        responses = {}
        with mock.patch.object(server, "_CODEX_APP_SERVER_THREAD_STATE", states), \
             mock.patch.object(server, "_CODEX_APP_SERVER_RESPONSES", responses), \
             mock.patch.object(server, "_CODEX_APP_SERVER_ORPHANED_WAITERS", {}):
            server._codex_app_server_handle_message({"id": 404, "result": {
                "thread": {"id": "deleted", "status": {"type": "idle"}, "turns": []}}})
        self.assertEqual(states, {})
        self.assertIn(404, responses)

    def test_native_lifecycle_notifications_enqueue_only_the_exact_id(self):
        for method in ("thread/archived", "thread/unarchived", "thread/deleted"):
            with self.subTest(method=method), \
                 mock.patch.object(client, "_client_enqueue_lifecycle", return_value=True) as enqueue, \
                 mock.patch.object(server, "_codex_retire_deleted_thread_state"), \
                 mock.patch.object(server, "_find_descendant_sessions") as descendants:
                client.codex_client_observe({"method": method,
                    "params": {"threadId": "exact-native-id"}}, self.transport)
            enqueue.assert_called_once_with(method, {"exact-native-id"})
            descendants.assert_not_called()

    def test_failed_sync_does_not_overwrite_a_newer_lifecycle_state(self):
        client._CLIENT_LIFECYCLE_PENDING["task"] = "thread/unarchived"

        client._client_requeue_lifecycle_failure(
            "task", "thread/archived", RuntimeError("sidecar unavailable"))

        self.assertEqual(client._CLIENT_LIFECYCLE_PENDING["task"], "thread/unarchived")
        self.assertEqual(client._CLIENT_LIFECYCLE_LAST_ERROR["thread_id"], "task")

    def test_lifecycle_queue_overflow_is_explicit(self):
        class RunningWorker:
            @staticmethod
            def is_alive():
                return True

        with mock.patch.dict("os.environ", {"CCC_EPHEMERAL": ""}), \
             mock.patch.object(client, "_CLIENT_LIFECYCLE_LIMIT", 1), \
             mock.patch.object(client, "_CLIENT_LIFECYCLE_WORKER", RunningWorker()), \
             mock.patch.object(server, "_log_activity"):
            client._CLIENT_LIFECYCLE_PENDING["existing"] = "thread/archived"
            accepted = client._client_enqueue_lifecycle("thread/archived", {"overflow"})
        self.assertFalse(accepted)
        self.assertEqual(client._CLIENT_LIFECYCLE_LAST_ERROR["error"],
                         "lifecycle sync queue is full")

    def test_native_success_survives_sidebar_sync_failure_without_replay(self):
        from ccc_server import codex_capabilities
        catalog = {"ok": True, "methods": [{"method": "thread/archive", "available": True,
            "read_only": False, "params_schema": {"type": "object"}}]}
        data = {"method": "thread/archive", "params": {"threadId": "task"},
            "context": {"thread_id": "task", "repo_path": "/test"},
            "generation": client.CODEX_CONVERSATIONS.generation, "action_id": "archive-failure"}
        with mock.patch.dict("os.environ", {"CCC_EPHEMERAL": ""}), \
             mock.patch.object(codex_capabilities, "get_codex_catalog", return_value=catalog), \
             mock.patch.object(server, "resolve_repo_path", return_value="/test"), \
             mock.patch.object(client, "_client_read_thread", return_value={"id": "task", "cwd": "/test"}), \
             mock.patch.object(client, "_client_rpc", return_value={"ok": True, "result": {}}) as rpc, \
             mock.patch.object(server, "_codex_sync_native_lifecycle", side_effect=OSError("disk")), \
             mock.patch.object(client, "_client_enqueue_lifecycle", return_value=True) as enqueue:
            first = client._client_operation(data)
            second = client._client_operation(data)
        self.assertTrue(first["ok"])
        self.assertTrue(first["sidebar_sync_pending"])
        self.assertEqual(second, first)
        rpc.assert_called_once()
        enqueue.assert_called_once_with("thread/archived", {"task"})

    def test_deferred_command_response_has_a_matching_bounded_deadline(self):
        self.assertEqual(client._client_rpc_timeout("command/exec", {"timeoutMs": 60000}), 65)
        self.assertEqual(client._client_rpc_timeout("command/exec", {"timeoutMs": 10**100}), 1805)
        self.assertEqual(client._client_rpc_timeout("command/exec", {"timeoutMs": 1800000}), 1805)
        self.assertEqual(client._client_rpc_timeout("command/exec", {}), 1805)
        self.assertEqual(client._client_rpc_timeout("command/exec", {"timeoutMs": False}), 25)
        self.assertEqual(client._client_rpc_timeout("account/read", {}), 25)

    def test_preview_preference_survives_worker_reload_without_secrets(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(server, "COMMAND_CENTER_STATE_DIR", Path(directory)), \
             mock.patch.dict(os.environ, {"CCC_EPHEMERAL": ""}), \
             mock.patch.object(client, "_CLIENT_PREFERENCES_LOADED", False):
            original = os.environ.pop("CCC_CODEX_EXPERIMENTAL", None)
            try:
                client._client_save_preferences(True)
                self.assertEqual(json.loads(client._client_preferences_path().read_text()), {"experimental": True})
                os.environ.pop("CCC_CODEX_EXPERIMENTAL", None)
                client._client_load_preferences()
                self.assertEqual(os.environ.get("CCC_CODEX_EXPERIMENTAL"), "1")
            finally:
                if original is None:
                    os.environ.pop("CCC_CODEX_EXPERIMENTAL", None)
                else:
                    os.environ["CCC_CODEX_EXPERIMENTAL"] = original
