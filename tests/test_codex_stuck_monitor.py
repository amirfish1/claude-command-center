import types
import unittest
from pathlib import Path
from unittest import mock

import server


ROOT = Path(__file__).resolve().parents[1]


class CodexStuckSummaryTests(unittest.TestCase):
    def test_summary_prefilters_stale_tails_then_confirms_authoritative_state(self):
        now = 10_000.0
        rows = [
            {"id": "confirmed-stuck"},
            {"id": "active-writer"},
            {"id": "fresh-mid-turn"},
            {"id": "old-mid-turn"},
        ]
        paths = {
            "confirmed-stuck": mock.Mock(stat=lambda: types.SimpleNamespace(st_mtime=8_000.0)),
            "active-writer": mock.Mock(stat=lambda: types.SimpleNamespace(st_mtime=8_100.0)),
            "fresh-mid-turn": mock.Mock(stat=lambda: types.SimpleNamespace(st_mtime=9_950.0)),
            "old-mid-turn": mock.Mock(stat=lambda: types.SimpleNamespace(st_mtime=1.0)),
        }

        def rollout_path(row):
            return paths[row["id"]]

        def tail_meta(path):
            return {"last_event_type": "assistant", "pending_tool": None}

        def authoritative_state(
            sid,
            _now,
            note_writer_transition=True,
            rollout_path=None,
            rollout_stat=None,
            rollout_tail=None,
        ):
            self.assertFalse(note_writer_transition)
            self.assertIs(rollout_path, paths[sid])
            self.assertEqual(rollout_stat.st_mtime, paths[sid].stat().st_mtime)
            self.assertEqual(rollout_tail["last_event_type"], "assistant")
            return {
                "codex_state": "working" if sid == "active-writer" else "stuck",
            }

        with (
            mock.patch.object(server, "_codex_fetch_threads", return_value=rows),
            mock.patch.object(server, "_codex_rollout_path_from_row", side_effect=rollout_path),
            mock.patch.object(server, "_extract_codex_tail_meta", side_effect=tail_meta),
            mock.patch.object(server, "_codex_pool_alive", return_value=True),
            mock.patch.object(server, "_live_engine_session_ids", return_value=set()),
            mock.patch.object(server, "_codex_recent_window_s", return_value=3_600.0),
            mock.patch.object(server, "_codex_stale_tool_threshold_s", return_value=900.0),
            mock.patch.object(server, "_codex_state_fields", side_effect=authoritative_state),
        ):
            summary = server.build_codex_stuck_summary(now=now, force=True)

        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["session_ids"], ["confirmed-stuck"])
        self.assertEqual(summary["candidates"], 2)
        self.assertEqual(summary["recent_sessions"], 3)
        self.assertEqual(summary["threshold_s"], 900)


class CodexStuckMonitorUiTests(unittest.TestCase):
    def test_footer_polls_cached_stuck_summary_and_labels_the_heuristic(self):
        app_js = (ROOT / "static" / "app.js").read_text()
        self.assertIn("cccStuckPill", app_js)
        self.assertIn("/api/codex/stuck-summary", app_js)
        self.assertIn("sessions currently labeled Stuck", app_js)
        self.assertIn("stuck -", app_js)


if __name__ == "__main__":
    unittest.main()
