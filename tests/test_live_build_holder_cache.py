"""CCC-168: live-activity builds must not fork lsof or re-shlex argv per build."""
import threading
import time
import unittest
from unittest import mock

import server
from ccc_server import codex, session_graph


class StaleOkHolderCacheTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(codex._CODEX_SHARED_STATE_HOLDER_CACHE)
        codex._CODEX_SHARED_STATE_HOLDER_REFRESH_INFLIGHT = False

    def tearDown(self):
        codex._CODEX_SHARED_STATE_HOLDER_CACHE.clear()
        codex._CODEX_SHARED_STATE_HOLDER_CACHE.update(self._saved)
        codex._CODEX_SHARED_STATE_HOLDER_REFRESH_INFLIGHT = False

    def test_expired_cache_is_served_and_refreshed_off_thread(self):
        stale = [{"pid": 1, "command": "codex", "file": "x"}]
        codex._CODEX_SHARED_STATE_HOLDER_CACHE.update({"ts": time.time() - 60, "holders": stale})
        refreshed = threading.Event()
        callers = []

        def fake_holders(now=None, force=False):
            callers.append(threading.current_thread().name)
            refreshed.set()
            return []

        with mock.patch.object(server, "_codex_shared_state_db_holders", side_effect=fake_holders):
            got = codex._codex_shared_state_db_holders_stale_ok()
            self.assertEqual(got, stale)
            self.assertTrue(refreshed.wait(5))
        self.assertEqual(callers, ["codex-holder-refresh"])

    def test_fresh_cache_does_not_refresh(self):
        codex._CODEX_SHARED_STATE_HOLDER_CACHE.update({"ts": time.time(), "holders": []})
        with mock.patch.object(server, "_codex_shared_state_db_holders") as holders:
            self.assertEqual(codex._codex_shared_state_db_holders_stale_ok(), [])
        holders.assert_not_called()

    def test_first_call_samples_inline(self):
        codex._CODEX_SHARED_STATE_HOLDER_CACHE.update({"ts": 0.0, "holders": None})
        with mock.patch.object(server, "_codex_shared_state_db_holders", return_value=[{"pid": 2}]) as holders:
            self.assertEqual(codex._codex_shared_state_db_holders_stale_ok(), [{"pid": 2}])
        holders.assert_called_once()


class CommandTokenTest(unittest.TestCase):
    SID = "019a0000-1111-2222-3333-444455556666"

    def test_unrelated_command_skips_shlex(self):
        with mock.patch.object(session_graph.shlex, "split") as split:
            self.assertFalse(session_graph._command_targets_engine_session(
                "codex exec -- 'a long prompt'", self.SID, "codex"))
        split.assert_not_called()

    def test_matching_resume_still_detected(self):
        cmd = f"codex exec resume {self.SID} -- 'prompt'"
        self.assertTrue(session_graph._command_targets_engine_session(cmd, self.SID, "codex"))
        self.assertFalse(session_graph._command_targets_engine_session(
            f"codex exec -- 'mentions {self.SID}'", self.SID, "codex"))


if __name__ == "__main__":
    unittest.main()
