import unittest
from unittest import mock

from ccc_server import usage_stats

TITLER = "Produce a concise 4-8 word title summarizing what the user is trying to do"


def _turn(sid, folder, preview, tin=30000, tout=300, end="2026-10-04T21:00:00Z"):
    return {"session_id": sid, "folder_path": folder, "trigger_preview": preview,
            "assistant_preview": "A title", "tokens_in": tin, "tokens_out": tout,
            "t_end": end, "dur_sec": 3.0, "model": "claude-haiku-4-5"}


class TitlerTurnsPayload(unittest.TestCase):
    def setUp(self):
        usage_stats._TITLER_TURNS_CACHE.update(ts=0.0, payload=None)

    def test_only_titler_turns_in_scratch_are_counted(self):
        turns = [
            _turn("a", "-x--claude-command-center-scratch", TITLER + " below"),
            _turn("b", "-x--claude-command-center-scratch", "some Ask question"),
            _turn("c", "-x-Apps-repo", TITLER),
        ]
        with mock.patch.object(usage_stats, "_throughput_window_turns", return_value=turns) as w:
            p = usage_stats._titler_turns_payload()
            usage_stats._titler_turns_payload()  # TTL cache: no second scan
        self.assertEqual(w.call_count, 1)
        self.assertEqual(p["turn_count"], 1)
        self.assertEqual(p["total_tokens"], 30300)
        self.assertEqual(p["turns"][0]["session_id"], "a")


class WorkerPromptSkip(unittest.TestCase):
    def test_worker_prompt_is_skipped_but_normal_prompt_is_not(self):
        import server  # noqa: F401  (populates the shared core)
        from ccc_server import session_graph as g
        self.assertTrue(g._is_wt_worker_session("x", "Drain the CCC WatchTower queue and keep it empty."))
        self.assertFalse(g._is_wt_worker_session("x", "Fix the login bug"))


class CodexTitleAgeCap(unittest.TestCase):
    def test_old_codex_session_is_not_queued(self):
        import time
        import server  # noqa: F401
        from ccc_server import session_graph as g
        with mock.patch.object(g, "_auto_title_marker_path") as marker:
            g._request_codex_auto_title("abc", fresh={}, mtime=time.time() - 30 * 86400)
        marker.assert_not_called()  # returned before any marker/claim work


if __name__ == "__main__":
    unittest.main()
