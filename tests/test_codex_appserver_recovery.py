"""Codex app-server must fail over fast instead of stalling a resume.

Both invariants here come from the 2026-09-06 incident where a resumed Codex
session sat on "Thinking..." for over ten minutes: the managed daemon accepted
the socket but never answered ``initialize``, and the stdio fallback was
refused because CCC's own dashboard held a read handle on the shared state DB.
"""

import os
import pathlib
import threading
import unittest
from unittest import mock

from ccc_server import codex


class OwnProcessHolderFilterTest(unittest.TestCase):
    """CCC's own processes are readers, never a second Codex writer."""

    def test_drops_own_pid(self):
        holders = [{"pid": os.getpid(), "command": "Python", "file": "state_5.sqlite"}]
        self.assertEqual(codex._codex_filter_own_ccc_holders(holders), [])

    def test_keeps_foreign_codex_process(self):
        # pid 1 is launchd: alive, not ours, so the guard must still fire.
        holders = [{"pid": 1, "command": "codex", "file": "state_5.sqlite"}]
        self.assertEqual(
            [h["pid"] for h in codex._codex_filter_own_ccc_holders(holders)], [1],
        )

    def test_empty_in_empty_out(self):
        self.assertEqual(codex._codex_filter_own_ccc_holders([]), [])

    def test_matches_our_entrypoints(self):
        root = str(pathlib.Path(codex.__file__).resolve().parent.parent)
        self.assertTrue(codex._codex_cmd_is_own_ccc(f"/usr/bin/python3 {root}/server.py"))
        self.assertTrue(codex._codex_cmd_is_own_ccc(f"/usr/bin/python3 {root}/ccc_worker.py"))

    def test_foreign_codex_quoting_our_path_is_not_ours(self):
        # A `codex exec` whose PROMPT names this repo is still a foreign
        # writer. A bare substring match filtered it out and silently disarmed
        # the guard -- caught in live verification, 2026-09-06.
        root = str(pathlib.Path(codex.__file__).resolve().parent.parent)
        cmd = f"/opt/codex/bin/codex exec --model gpt-5.6-terra Drain the queue in {root} now"
        self.assertFalse(codex._codex_cmd_is_own_ccc(cmd))


class ManagedInitCooldownTest(unittest.TestCase):
    """A managed daemon that fails initialize must be skipped, not retried."""

    def setUp(self):
        self._reset()
        self.addCleanup(self._reset)

    @staticmethod
    def _reset():
        codex._CODEX_MANAGED_INIT_FAILS = 0
        codex._CODEX_MANAGED_COOLDOWN_UNTIL = 0.0

    def test_single_failure_does_not_arm_cooldown(self):
        self.assertFalse(codex._codex_note_managed_init_result(False, now=1000.0))
        self.assertFalse(codex._codex_managed_in_cooldown(now=1000.0))

    def test_second_consecutive_failure_arms_cooldown(self):
        codex._codex_note_managed_init_result(False, now=1000.0)
        self.assertTrue(codex._codex_note_managed_init_result(False, now=1000.0))
        self.assertTrue(codex._codex_managed_in_cooldown(now=1000.0))
        # Bounded: the daemon is retried once the cooldown lapses.
        self.assertFalse(
            codex._codex_managed_in_cooldown(
                now=1000.0 + codex._codex_managed_cooldown_s() + 1,
            ),
        )

    def test_success_clears_the_streak(self):
        codex._codex_note_managed_init_result(False, now=1000.0)
        codex._codex_note_managed_init_result(True, now=1000.0)
        self.assertFalse(codex._codex_note_managed_init_result(False, now=1000.0))
        self.assertFalse(codex._codex_managed_in_cooldown(now=1000.0))

    def test_cooldown_is_at_most_a_minute_by_default(self):
        self.assertLessEqual(codex._codex_managed_cooldown_s(), 60.0)


class TestSharedStateConflictCooldownTest(unittest.TestCase):
    def setUp(self):
        import server  # noqa: F401  (registers the _core namespace)
        self.previous = codex._CODEX_SHARED_STATE_BLOCK_RETRY_UNTIL
        codex._CODEX_SHARED_STATE_BLOCK_RETRY_UNTIL = 0.0
        self.addCleanup(setattr, codex, "_CODEX_SHARED_STATE_BLOCK_RETRY_UNTIL", self.previous)

    def test_conflict_suppresses_stdio_retry_until_cooldown_expires(self):
        # A real conflict is retried for a short budget (busy_timeout-style,
        # see _codex_wait_for_shared_state_clear) before it counts as durable
        # -- shrink that budget so this still runs in well under a second.
        conflict = {"summary": "pids=9 commands=codex"}
        with mock.patch.object(codex._core, "_CODEX_APP_SERVER_TRANSPORT", None), \
             mock.patch.object(codex._core, "_CODEX_APP_SERVER_INITIALIZED", False), \
             mock.patch.object(codex._core, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(codex._core, "_codex_managed_app_server_enabled", return_value=False), \
             mock.patch.object(codex._core, "_codex_shared_state_conflict", return_value=conflict) as check, \
             mock.patch.object(codex._core, "_log_activity") as log_activity, \
             mock.patch.object(codex, "_CODEX_SHARED_STATE_CONFLICT_RETRY_BUDGET_S", 0.03), \
             mock.patch.object(codex, "_CODEX_SHARED_STATE_CONFLICT_POLL_S", 0.01), \
            mock.patch.object(codex.time, "time", return_value=1000.0):
            self.assertIsNone(codex._ensure_codex_app_server())
            first_call_count = check.call_count
            self.assertIsNone(codex._ensure_codex_app_server())
            self.assertFalse(codex._core._CODEX_APP_SERVER_INITIALIZING)

        # First call retries within its budget (busy_timeout-style) before
        # giving up; the second call is short-circuited entirely by the
        # cooldown gate and must not retry (or even re-check) at all.
        self.assertGreaterEqual(first_call_count, 1)
        self.assertEqual(check.call_count, first_call_count)
        self.assertEqual(log_activity.call_count, 1)

    def test_cooldown_return_wakes_initialization_waiter(self):
        """A cooldown must release callers that arrived behind its flag."""
        paused_at_cooldown = threading.Event()
        release_cooldown = threading.Event()
        waiter_blocked = threading.Event()
        waiter_released = threading.Event()
        results = []
        lock = codex._core._CODEX_APP_SERVER_LOCK
        original_wait = lock.wait
        original_notify_all = lock.notify_all

        def controlled_socket_path():
            paused_at_cooldown.set()
            release_cooldown.wait(2)
            return mock.Mock()

        def mark_wait(timeout=None):
            waiter_blocked.set()
            result = original_wait(10)
            waiter_released.set()
            return result

        with mock.patch.object(codex._core, "_CODEX_APP_SERVER_TRANSPORT", None), \
             mock.patch.object(codex._core, "_CODEX_APP_SERVER_INITIALIZED", False), \
             mock.patch.object(codex._core, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(codex._core, "_codex_managed_app_server_socket_path", side_effect=controlled_socket_path), \
             mock.patch.object(codex._core, "_codex_managed_app_server_enabled", return_value=False), \
             mock.patch.object(codex.time, "time", return_value=1000.0), \
             mock.patch.object(lock, "wait", side_effect=mark_wait), \
             mock.patch.object(lock, "notify_all", wraps=original_notify_all) as notify_all:
            codex._CODEX_SHARED_STATE_BLOCK_RETRY_UNTIL = 1030.0
            initializer = threading.Thread(
                target=lambda: results.append(codex._ensure_codex_app_server()),
            )
            initializer.start()
            self.assertTrue(paused_at_cooldown.wait(1))

            waiter = threading.Thread(
                target=lambda: results.append(codex._ensure_codex_app_server()),
            )
            waiter.start()
            self.assertTrue(waiter_blocked.wait(1))

            try:
                release_cooldown.set()
                self.assertTrue(waiter_released.wait(1))
                self.assertGreaterEqual(notify_all.call_count, 1)
            finally:
                with lock:
                    original_notify_all()
            initializer.join(2)
            waiter.join(2)

        self.assertFalse(initializer.is_alive())
        self.assertFalse(waiter.is_alive())
        self.assertEqual(results, [None, None])
        self.assertFalse(codex._core._CODEX_APP_SERVER_INITIALIZING)


class StaleAppServerInitializationTest(unittest.TestCase):
    """An initializer that loses its transport must not strand all callers."""

    def test_stale_initialization_returns_unavailable_instead_of_waiting_forever(self):
        import server  # noqa: F401  (registers the _core namespace)
        transport = mock.Mock()
        transport.alive.return_value = False

        with mock.patch.object(codex._core, "_CODEX_APP_SERVER_TRANSPORT", transport), \
             mock.patch.object(codex._core, "_CODEX_APP_SERVER_PROC", mock.Mock()), \
             mock.patch.object(codex._core, "_CODEX_APP_SERVER_INITIALIZED", False), \
             mock.patch.object(codex._core, "_CODEX_APP_SERVER_INITIALIZING", True), \
             mock.patch.object(codex, "_CODEX_APP_SERVER_INITIALIZING_WAIT_S", 0), \
             mock.patch.object(codex, "_codex_managed_app_server_enabled", return_value=False), \
             mock.patch.object(codex._core, "_log_activity") as log_activity:
            self.assertIsNone(codex._ensure_codex_app_server(allow_stdio=False))
            self.assertTrue(codex._core._CODEX_APP_SERVER_INITIALIZING)
            self.assertFalse(codex._core._CODEX_APP_SERVER_INITIALIZED)

        transport.close.assert_not_called()
        self.assertTrue(any(call.args[1] == "INIT_STALE" for call in log_activity.call_args_list))



class WakeStallOutcomeTest(unittest.TestCase):
    """A resume that never reaches `running` must report a cause, not spin."""

    @classmethod
    def setUpClass(cls):
        import server  # noqa: F401  (registers the _core namespace)
        from ccc_server import core, queue_events
        cls.core = core
        cls.q = queue_events

    def _seed(self, sid, events):
        import collections
        with self.core._RESUME_LEDGER_LOCK:
            self.core._CODEX_WAKE_EVENTS[sid] = collections.deque(events)
        self.addCleanup(self._clear, sid)

    def _clear(self, sid):
        with self.core._RESUME_LEDGER_LOCK:
            self.core._CODEX_WAKE_EVENTS.pop(sid, None)

    def test_silent_pre_running_wake_becomes_an_error(self):
        sid = "test-stall-sid"
        now = __import__("time").time()
        self._seed(sid, [{"event": "codex_wake_attempt", "epoch": now - 300}])
        out = self.q.build_codex_wake_status(sid)
        self.assertEqual(out["outcome"], "error")
        self.assertTrue(out["outcome_detail"])
        self.assertGreaterEqual(out["stalled_s"], 60)
        self.assertFalse(out["active"])

    def test_fresh_wake_is_still_active(self):
        sid = "test-fresh-sid"
        now = __import__("time").time()
        self._seed(sid, [{"event": "codex_wake_attempt", "epoch": now - 2}])
        out = self.q.build_codex_wake_status(sid)
        self.assertIsNone(out["outcome"])
        self.assertIsNone(out["stalled_s"])
        self.assertTrue(out["active"])

    def test_running_turn_is_never_called_stalled(self):
        # Silence after the turn starts is the model thinking; the separate
        # stuck heuristic owns that window, not the wake breakdown.
        sid = "test-running-sid"
        now = __import__("time").time()
        self._seed(sid, [
            {"event": "codex_wake_attempt", "epoch": now - 900},
            {"event": "codex_wake_ok", "epoch": now - 880},
        ])
        out = self.q.build_codex_wake_status(sid)
        self.assertIsNone(out["stalled_s"])

if __name__ == "__main__":
    unittest.main()
