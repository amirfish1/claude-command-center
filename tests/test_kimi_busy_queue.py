"""Regression coverage for Kimi follow-ups while an ACP turn is active."""

import importlib
import inspect
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock


class KimiBusyQueueTests(unittest.TestCase):
    def test_acp_turn_text_keeps_a_complete_long_reply(self):
        """Long ACP replies must remain complete when finalized for replay."""
        server = importlib.import_module("server")
        reply = "x" * 7501
        turn = {"text": ""}

        server._acp_append_turn_text(turn, "text", reply)

        self.assertEqual(turn["text"], reply)

    def test_bare_kimi_uuid_routes_to_the_canonical_acp_session_id(self):
        """Group-chat sidecars may retain Kimi's UUID without ``session_``.

        Kimi's index uses the prefixed id, so routing the bare value as a
        Claude session falls through to repo context resolution and produces
        an unrelated ``repo_required`` error.
        """
        server = importlib.import_module("server")
        bare_sid = "71247a48-6db9-4221-975b-a6bf31f20d9b"
        canonical_sid = f"session_{bare_sid}"
        with mock.patch.object(server, "_kimi_session_index", return_value={
            canonical_sid: {"session_dir": "/tmp/kimi", "work_dir": "/tmp"},
        }), \
             mock.patch.object(server, "_is_codex_session", return_value=False), \
             mock.patch.object(server, "find_session_cwd", return_value="/tmp"), \
             mock.patch.object(server, "session_live_status", return_value={
                 "live": False, "status": "idle", "kind": "acp",
                 "tty": None, "terminal_app": None,
             }), \
             mock.patch.object(server, "_acp_prompt", return_value={"ok": True}) as prompt, \
             mock.patch.object(server, "_try_wt_send_for_headless_delivery", return_value={
                 "ok": False, "error": "should not route through WatchTower",
             }):
            result = server._inject_text_into_session(bare_sid, "follow up")

        self.assertTrue(result["ok"])
        prompt.assert_called_once_with(
            "kimi", canonical_sid, "follow up", mode="send", from_queue=False,
        )

    def test_busy_kimi_follow_up_is_preserved_in_the_durable_input_queue(self):
        server = importlib.import_module("server")
        sid = "kimi-busy-queue-session"
        with server._pending_terminal_input_lock:
            original_queue = dict(server._pending_terminal_input_queue)
            server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(server, "_is_codex_session", return_value=False), \
                 mock.patch.object(server, "_is_kimi_session", return_value=True), \
                 mock.patch.object(server, "find_session_cwd", return_value="/tmp"), \
                 mock.patch.object(server, "session_live_status", return_value={
                     "live": True, "status": "running", "kind": "acp",
                     "tty": None, "terminal_app": None,
                 }), \
                 mock.patch.object(server, "_acp_prompt", return_value={
                     "ok": False, "code": "busy", "error": "turn already in progress",
                 }), \
                 mock.patch.object(server, "_save_pending_inputs"):
                result = server._inject_text_into_session(sid, "follow up")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["via"], "terminal-queued")
            self.assertEqual(
                result["queued_reason"],
                "the current turn is still running; your message will send next",
            )
            with server._pending_terminal_input_lock:
                self.assertEqual(server._pending_terminal_input_queue[sid], ["follow up"])
        finally:
            with server._pending_terminal_input_lock:
                server._pending_terminal_input_queue.clear()
                server._pending_terminal_input_queue.update(original_queue)

    def test_busy_kimi_queued_steer_waits_in_the_durable_input_queue(self):
        """A queued-row Steer must not send a second ACP turn while Kimi is busy."""
        server = importlib.import_module("server")
        sid = "kimi-busy-steer-session"
        with server._pending_terminal_input_lock:
            original_queue = dict(server._pending_terminal_input_queue)
            server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(server, "_is_codex_session", return_value=False), \
                 mock.patch.object(server, "_is_kimi_session", return_value=True), \
                 mock.patch.object(server, "find_session_cwd", return_value="/tmp"), \
                 mock.patch.object(server, "session_live_status", return_value={
                     "live": True, "status": "running", "kind": "acp",
                     "tty": None, "terminal_app": None,
                 }), \
                 mock.patch.object(server, "_acp_prompt", return_value={
                     "ok": False, "code": "busy", "error": "turn already in progress",
                 }), \
                 mock.patch.object(server, "_save_pending_inputs"):
                result = server._inject_text_into_session(sid, "follow up", mode="steer")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["via"], "terminal-queued")
            with server._pending_terminal_input_lock:
                self.assertEqual(server._pending_terminal_input_queue[sid], ["follow up"])
        finally:
            with server._pending_terminal_input_lock:
                server._pending_terminal_input_queue.clear()
                server._pending_terminal_input_queue.update(original_queue)


    def test_available_kimi_acp_session_counts_as_live_for_pending_input_drain(self):
        server = importlib.import_module("server")
        with mock.patch.object(server, "_is_kimi_session", return_value=True), \
             mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}):
            self.assertTrue(server._archive_session_is_live_uncached("kimi-queue-session"))

    def test_active_acp_turn_holds_queued_input_until_it_is_idle(self):
        server = importlib.import_module("server")
        self.assertTrue(
            server._terminal_queue_waits_for_active_acp({
                "kind": "acp", "status": "running",
            })
        )
        self.assertFalse(
            server._terminal_queue_waits_for_active_acp({
                "kind": "acp", "status": "idle",
            })
        )


class KimiSteerCancelResendTests(unittest.TestCase):
    """Steer on an ACP session: cancel the active turn, then actually resend."""

    def _run_steer(self, prompt_side_effect, idempotency_key):
        server = importlib.import_module("server")
        sid = "kimi-steer-resend-session"
        calls = []

        def _prompt(harness, session_id, text, **kwargs):
            calls.append(kwargs)
            return prompt_side_effect(len(calls))

        with server._pending_terminal_input_lock:
            original_queue = dict(server._pending_terminal_input_queue)
            server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(server, "_is_codex_session", return_value=False), \
                 mock.patch.object(server, "_is_kimi_session", return_value=True), \
                 mock.patch.object(server, "find_session_cwd", return_value="/tmp"), \
                 mock.patch.object(server, "session_live_status", return_value={
                     "live": True, "status": "running", "kind": "acp",
                     "tty": None, "terminal_app": None,
                 }), \
                 mock.patch.object(server, "_acp_prompt", side_effect=_prompt), \
                 mock.patch.object(server, "_acp_cancel", return_value={"ok": True}), \
                 mock.patch.object(server, "_acp_session_snapshot", return_value={
                     "status": "idle",
                 }), \
                 mock.patch.object(server, "_save_pending_inputs"):
                result = server._inject_text_into_session(
                    sid, "actually, do it the other way",
                    mode="steer", idempotency_key=idempotency_key,
                )
        finally:
            with server._pending_terminal_input_lock:
                server._pending_terminal_input_queue.clear()
                server._pending_terminal_input_queue.update(original_queue)
        return result, calls

    def test_post_cancel_resend_uses_a_fresh_idempotency_key(self):
        """The pre-cancel attempt burns the caller's key.

        Reusing it made the worker's WorkLedger dedupe the resend back to the
        already-failed "turn already in progress" record, so Steer cancelled
        Kimi's turn and then silently queued the message instead of sending
        it.
        """
        result, calls = self._run_steer(
            lambda n: (
                {"ok": False, "code": "busy", "error": "turn already in progress"}
                if n == 1 else {"ok": True, "via": "acp"}
            ),
            "inject:3278baff",
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["idempotency_key"], "inject:3278baff")
        self.assertNotEqual(
            calls[1]["idempotency_key"], calls[0]["idempotency_key"],
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result.get("queued"))

    def test_retry_key_is_stable_across_replays_of_the_same_inject(self):
        _, first = self._run_steer(
            lambda n: (
                {"ok": False, "code": "busy", "error": "turn already in progress"}
                if n == 1 else {"ok": True}
            ),
            "inject:abc",
        )
        _, second = self._run_steer(
            lambda n: (
                {"ok": False, "code": "busy", "error": "turn already in progress"}
                if n == 1 else {"ok": True}
            ),
            "inject:abc",
        )
        self.assertEqual(
            first[1]["idempotency_key"], second[1]["idempotency_key"],
        )

    def test_still_busy_after_cancel_falls_back_to_the_durable_queue(self):
        result, calls = self._run_steer(
            lambda n: {
                "ok": False, "code": "busy", "error": "turn already in progress",
            },
            "inject:xyz",
        )

        self.assertEqual(len(calls), 2)
        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])


class KimiQueuedSteerConsumptionTests(unittest.TestCase):
    """A Kimi queued-row Steer must withdraw the copy it just delivered.

    ``_finalize_queued_steer_result`` used to match only ``codex-steer``. ACP
    harnesses steer by cancel-then-resend and report ``acp-prompt``, so a
    successful Kimi steer left the durable copy queued: the endpoint answered
    ``queued_preserved`` over a message it had in fact just sent, the tray card
    stayed pinned above the composer, and the queue pump delivered the same
    text a second time later.
    """

    def setUp(self):
        self.server = importlib.import_module("server")
        # The queue helpers persist through PENDING_INPUTS_FILE; point it at a
        # scratch path so these tests never touch the real pending queue.
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-kimi-steer-")
        self._prev_pending_file = self.server.PENDING_INPUTS_FILE
        self.server.PENDING_INPUTS_FILE = pathlib.Path(self.tmp_dir) / "pending-inputs.json"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def tearDown(self):
        self.server.PENDING_INPUTS_FILE = self._prev_pending_file
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def _queue(self, sid, items):
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = list(items)
        # The consume path reloads from the on-disk authority, so an
        # in-memory-only queue reads back as empty. Persist the seed.
        self.server._save_pending_inputs([sid])

    def _remaining(self, sid):
        with self.server._pending_resume_lock:
            return list(self.server._pending_resume_queue.get(sid, []))

    def test_acp_steer_consumes_its_queued_copy(self):
        sid = "session_kimi-steer"
        self._queue(sid, ["target", "later"])

        result = self.server._finalize_queued_steer_result(
            sid, "target", {"ok": True, "via": "acp-prompt"},
        )

        self.assertEqual(result["queued_consumed"], 1)
        self.assertNotIn("queued_preserved", result)
        self.assertEqual(self._remaining(sid), ["later"])

    def test_a_failed_acp_steer_still_preserves_the_queue(self):
        sid = "session_kimi-failed"
        self._queue(sid, ["target"])

        result = self.server._finalize_queued_steer_result(
            sid, "target", {"ok": False, "error": "ACP kimi send failed"},
        )

        self.assertTrue(result["queued_preserved"])
        self.assertEqual(self._remaining(sid), ["target"])

    def test_steer_all_consumes_every_message_it_delivered(self):
        """The batch is delivered as one prompt, so the queue is matched by
        the individual texts rather than by the concatenation that was sent."""
        sid = "session_kimi-batch"
        self._queue(sid, ["first", "second", "third"])

        result = self.server._finalize_queued_steer_batch_result(
            sid, ["first", "second"], {"ok": True, "via": "acp-prompt"},
        )

        self.assertEqual(result["queued_consumed"], 2)
        self.assertEqual(self._remaining(sid), ["third"])

    def test_steer_all_that_did_not_land_leaves_every_message_queued(self):
        sid = "session_kimi-batch-failed"
        self._queue(sid, ["first", "second"])

        result = self.server._finalize_queued_steer_batch_result(
            sid, ["first", "second"], {"ok": False, "code": "codex_steer_unavailable"},
        )

        self.assertTrue(result["queued_preserved"])
        self.assertEqual(self._remaining(sid), ["first", "second"])

    def test_batch_endpoint_accepts_replace_queued_texts(self):
        source = inspect.getsource(self.server.CommandCenterHandler.do_POST)
        branch = source[
            source.index('elif path == "/api/inject-input"'):
            source.index('elif path == "/api/session/compact"')
        ]
        self.assertIn('payload.get("replace_queued_texts")', branch)
        self.assertIn(
            "_finalize_queued_steer_batch_result(", branch,
        )
