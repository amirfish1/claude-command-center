"""Regression coverage for the `thread/list` fan-out that stalls Codex spawns.

`_codex_app_server_refresh_thread_status(sid)` issues a **global** RPC --
one `thread/list` returns every thread the app-server knows -- but it used
to throttle that call **per session id**. `/api/session-status` is polled
once per visible Codex session, so N open Codex sessions produced N
identical `thread/list` requests every `max_age` seconds against the single
serialized app-server stdio channel.

The channel then backs up: activity.log shows `thread/list` replies
arriving 3-22s after their 3s timeout, plus PROBEFAIL/BUSY. A new session's
`thread/start` (timeout=20) queued behind that backlog times out, so
`_codex_spawn_via_app_server` gives up -- the user-visible symptom is
"starting a Codex session fails".

The fix throttles and coalesces `thread/list` globally: at most one call in
flight, and pollers inside the freshness window reuse it instead of
queueing a duplicate.
"""

import importlib
import threading
import time
import unittest
from unittest import mock


class ThreadListCoalescingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def setUp(self):
        server = self.server
        # Each test starts from a cold global throttle.
        server._CODEX_THREAD_LIST_LAST_AT = 0.0
        server._CODEX_THREAD_LIST_INFLIGHT = False
        self._state_patch = mock.patch.dict(
            server._CODEX_APP_SERVER_THREAD_STATE, {}, clear=True,
        )
        self._state_patch.start()
        self.addCleanup(self._state_patch.stop)

    def _reply(self, thread_ids):
        return {"result": {"data": [{"id": tid} for tid in thread_ids]}}

    def test_many_sessions_share_one_thread_list_call(self):
        """N pollers inside the freshness window must issue ONE RPC, not N."""
        server = self.server
        sids = [f"thread-{i}" for i in range(12)]
        calls = []

        def fake_request(method, params, timeout=None, **kwargs):
            calls.append(method)
            return self._reply(sids)

        with mock.patch.object(server, "_codex_app_server_request", fake_request), \
             mock.patch.object(server, "_save_codex_app_server_state_unlocked"):
            for sid in sids:
                server._codex_app_server_refresh_thread_status(sid)

        self.assertEqual(
            calls.count("thread/list"), 1,
            f"expected one coalesced thread/list, got {calls.count('thread/list')} "
            "-- per-session throttling of a global RPC is what floods the "
            "serialized app-server channel",
        )

    def test_concurrent_pollers_do_not_queue_duplicate_requests(self):
        """A caller arriving while a call is on the wire waits for it."""
        server = self.server
        released = threading.Event()
        calls = []

        def slow_request(method, params, timeout=None, **kwargs):
            calls.append(method)
            released.wait(5)
            return self._reply(["thread-a", "thread-b"])

        with mock.patch.object(server, "_codex_app_server_request", slow_request), \
             mock.patch.object(server, "_save_codex_app_server_state_unlocked"):
            first = threading.Thread(
                target=server._codex_app_server_refresh_thread_status,
                args=("thread-a",),
            )
            first.start()
            # Let the first caller get as far as the (blocked) RPC.
            deadline = time.monotonic() + 5
            while not calls and time.monotonic() < deadline:
                time.sleep(0.01)
            second = threading.Thread(
                target=server._codex_app_server_refresh_thread_status,
                args=("thread-b",),
            )
            second.start()
            time.sleep(0.2)
            released.set()
            first.join(10)
            second.join(10)

        self.assertEqual(
            calls.count("thread/list"), 1,
            "the second poller queued its own thread/list behind the first "
            "instead of reusing the in-flight call",
        )

    def test_stale_window_still_refreshes(self):
        """Coalescing must not freeze the data -- past max_age we call again."""
        server = self.server
        calls = []

        def fake_request(method, params, timeout=None, **kwargs):
            calls.append(method)
            return self._reply(["thread-a"])

        with mock.patch.object(server, "_codex_app_server_request", fake_request), \
             mock.patch.object(server, "_save_codex_app_server_state_unlocked"):
            server._codex_app_server_refresh_thread_status("thread-a", max_age=2.0)
            server._CODEX_THREAD_LIST_LAST_AT = time.time() - 30
            server._codex_app_server_refresh_thread_status("thread-a", max_age=2.0)

        self.assertEqual(calls.count("thread/list"), 2)

    def test_forced_refresh_bypasses_the_throttle(self):
        """max_age=0 callers (grab-back, bridge modal) still get fresh data."""
        server = self.server
        calls = []

        def fake_request(method, params, timeout=None, **kwargs):
            calls.append(method)
            return self._reply(["thread-a"])

        with mock.patch.object(server, "_codex_app_server_request", fake_request), \
             mock.patch.object(server, "_save_codex_app_server_state_unlocked"):
            server._codex_app_server_refresh_thread_status("thread-a", max_age=2.0)
            server._codex_app_server_refresh_thread_status("thread-a", max_age=0)

        self.assertEqual(calls.count("thread/list"), 2)

    def test_requested_thread_presence_is_still_reported(self):
        server = self.server
        calls = []

        def fake_request(method, params, timeout=None, **kwargs):
            calls.append(method)
            return self._reply(["thread-a", "thread-b"])

        with mock.patch.object(server, "_codex_app_server_request", fake_request), \
             mock.patch.object(server, "_save_codex_app_server_state_unlocked"):
            self.assertTrue(
                server._codex_app_server_refresh_thread_status("thread-a", max_age=0)
            )
            self.assertFalse(
                server._codex_app_server_refresh_thread_status("thread-zzz", max_age=0)
            )


if __name__ == "__main__":
    unittest.main()
