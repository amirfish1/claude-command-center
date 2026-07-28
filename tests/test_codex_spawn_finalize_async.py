"""The Codex spawn finalizer: what runs off the critical path, and in what order.

turn/start is accepted in 1-2ms and the turn then runs inside Codex. Two things
used to sit between that and the spawn response:

  * thread/name/set  -- measured ~1.7s, a cosmetic label
  * the durability confirmation -- p75 ~5.0s, and no caller branches on it

Both now run in a background thread, taking a warm spawn from ~7s to ~0.4s.

ORDER IS LOad-BEARING and was gotten wrong once: Codex resolves thread/name/set
against the thread's rollout file, so renaming before that file has content
fails with "rollout ... is empty". The confirmation is precisely the signal
that the rollout exists, so it must run FIRST. Measured on a live app-server:
the user_message row lands 14-25s after turn/start, which is also why the
confirm budget here is 30s rather than the inject path's 5s.
"""

import importlib
import unittest
from unittest import mock


class SpawnFinalizeAsyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def _run_finalizer(self, server, requests, confirmation):
        calls = []

        def fake_request(method, params=None, timeout=20):
            calls.append(method)
            requests.append((method, params or {}))
            return {"result": {"ok": True}}

        def fake_confirm(*args, **kwargs):
            calls.append("confirm")
            return confirmation

        with mock.patch.object(server, "_codex_app_server_request", fake_request), \
             mock.patch.object(server, "_codex_wait_for_turn_activity", fake_confirm), \
             mock.patch.object(server, "_codex_telemetry_append"):
            thread = server._codex_finalize_spawn_async(
                "thread-1", "turn-1",
                session_name="my session",
                prompt="do the thing",
                log_path="/dev/null",
                baseline_state={},
                baseline_rollout=None,
            )
            thread.join(10)
            self.assertFalse(thread.is_alive())
        return calls

    def test_confirmation_runs_before_the_rename(self):
        """Renaming first fails with 'rollout is empty' -- confirm gates it."""
        server = self.server
        requests = []
        calls = self._run_finalizer(
            server, requests,
            {"confirmed": True, "source": "rollout-user-message"},
        )
        self.assertIn("confirm", calls)
        self.assertIn("thread/name/set", calls)
        self.assertLess(
            calls.index("confirm"), calls.index("thread/name/set"),
            "thread/name/set ran before the confirmation; that races Codex's "
            "first rollout write and the rename fails outright",
        )
        self.assertEqual(requests[0][1], {"threadId": "thread-1", "name": "my session"})

    def test_rename_is_still_attempted_when_confirmation_times_out(self):
        """An unconfirmed turn does not prove the rollout is missing."""
        server = self.server
        requests = []
        calls = self._run_finalizer(
            server, requests,
            {"confirmed": False, "source": None, "warning": "no events"},
        )
        self.assertIn("thread/name/set", calls)

    def test_rename_retries_when_codex_loses_the_rollout_race(self):
        server = self.server
        attempts = []

        def flaky_request(method, params=None, timeout=20):
            attempts.append(method)
            if len([m for m in attempts if m == "thread/name/set"]) < 2:
                return {"error": {"message": "rollout at /x.jsonl is empty"}}
            return {"result": {"ok": True}}

        with mock.patch.object(server, "_codex_app_server_request", flaky_request), \
             mock.patch.object(server, "_codex_wait_for_turn_activity",
                               return_value={"confirmed": True, "source": "rollout-user-message"}), \
             mock.patch.object(server, "_codex_telemetry_append"), \
             mock.patch.object(server, "_CODEX_NAME_SET_RETRY_DELAY", 0.01):
            thread = server._codex_finalize_spawn_async(
                "thread-1", "turn-1",
                session_name="retry me",
                prompt="p",
                log_path="/dev/null",
                baseline_state={},
                baseline_rollout=None,
            )
            thread.join(10)

        self.assertEqual(
            attempts.count("thread/name/set"), 2,
            "a lost rollout race must be retried, not left permanently unnamed",
        )

    def test_no_rename_when_the_session_has_no_name(self):
        server = self.server
        requests = []
        calls = self._run_finalizer(
            server, requests, {"confirmed": True, "source": "x"},
        )
        del calls
        with mock.patch.object(server, "_codex_app_server_request") as req, \
             mock.patch.object(server, "_codex_wait_for_turn_activity",
                               return_value={"confirmed": True, "source": "x"}), \
             mock.patch.object(server, "_codex_telemetry_append"):
            thread = server._codex_finalize_spawn_async(
                "thread-1", "turn-1",
                session_name="",
                prompt="p",
                log_path="/dev/null",
                baseline_state={},
                baseline_rollout=None,
            )
            thread.join(10)
        req.assert_not_called()


if __name__ == "__main__":
    unittest.main()
