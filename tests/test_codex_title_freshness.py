"""Codex titles arrive after the row does, and must not need a cache bust.

Codex names a thread asynchronously: `thread/name/set` lands seconds after the
spawn returns, and the name is written to ~/.codex/state_*.sqlite. That DB is
deliberately excluded from the archive corpus signature, because it is WAL and
its mtime moves on every write from any live Codex session -- gating an
O(all-rows) rebuild on that would pin the CPU.

The cost of leaving it out was a visible bug: a freshly spawned Codex session
rendered as "(untitled)" until some unrelated change happened to invalidate the
archive cache. The fix treats the title as live state and re-layers it at serve
time. These pin that, and pin the cheap spawn probe that replaced a full
10 MB list refetch on every chase tick.
"""

import importlib
import unittest
from unittest import mock


class CodexTitleRehydrateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def snapshot(self, sid, title, first_message=""):
        return {
            sid: {
                "title": title,
                "first_user_message": first_message,
                "agent_nickname": None,
                "agent_role": None,
                "agent_path": None,
            }
        }

    def test_a_late_codex_title_reaches_a_cached_row(self):
        server = self.server
        sid = "019f-cached-thread"
        cached = {"session_id": sid, "engine": "codex", "display_name": "(untitled)"}
        with mock.patch.object(
            server, "_codex_titles_snapshot",
            return_value=self.snapshot(sid, "fix the spawn stall"),
        ), mock.patch.object(server, "_load_session_name_overrides", return_value={}):
            out = server._rehydrate_archive_cached_rows([cached])[0]
        self.assertEqual(out["display_name"], "fix the spawn stall")

    def test_a_ccc_rename_still_beats_the_engine_title(self):
        server = self.server
        sid = "019f-cached-thread"
        cached = {"session_id": sid, "engine": "codex", "display_name": "(untitled)"}
        with mock.patch.object(
            server, "_codex_titles_snapshot",
            return_value=self.snapshot(sid, "engine picked this"),
        ), mock.patch.object(
            server, "_load_session_name_overrides", return_value={sid: "my name"}
        ):
            out = server._rehydrate_archive_cached_rows([cached])[0]
        self.assertEqual(out["display_name"], "my name")
        self.assertTrue(out["name_overridden"])

    def test_a_copied_prompt_is_not_reported_as_an_ai_title(self):
        """Codex stores the raw ask as `title` when it did not summarize."""
        server = self.server
        sid = "019f-cached-thread"
        cached = {"session_id": sid, "engine": "codex", "display_name": "x"}
        with mock.patch.object(
            server, "_codex_titles_snapshot",
            return_value=self.snapshot(sid, "same text", first_message="same text"),
        ), mock.patch.object(server, "_load_session_name_overrides", return_value={}):
            out = server._rehydrate_archive_cached_rows([cached])[0]
        self.assertIsNone(out["ai_title"])

    def test_a_corpus_with_no_codex_rows_never_opens_the_db(self):
        """Perf gate: the snapshot is lazy, not loaded on every serve."""
        server = self.server
        rows = [{"session_id": "abc", "engine": "claude", "display_name": "n"}]
        with mock.patch.object(server, "_codex_titles_snapshot") as snap, \
             mock.patch.object(server, "_load_session_name_overrides", return_value={}):
            server._rehydrate_archive_cached_rows(rows)
        snap.assert_not_called()

    def test_many_codex_rows_read_the_db_once(self):
        """Perf gate: batched, never per-row."""
        server = self.server
        rows = [
            {"session_id": f"sid-{i}", "engine": "codex", "display_name": "(untitled)"}
            for i in range(50)
        ]
        with mock.patch.object(
            server, "_codex_titles_snapshot", return_value={}
        ) as snap, mock.patch.object(
            server, "_load_session_name_overrides", return_value={}
        ):
            server._rehydrate_archive_cached_rows(rows)
        self.assertEqual(snap.call_count, 1)


class SessionLandedProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_a_known_codex_thread_reports_landed_with_its_name(self):
        server = self.server
        sid = "019f-thread"
        snap = {
            sid: {
                "title": "spawn stats",
                "first_user_message": "spawn stats",
                "agent_nickname": None,
                "agent_role": None,
                "agent_path": None,
            }
        }
        with mock.patch.object(server, "_codex_titles_snapshot", return_value=snap):
            out = server._session_landed(sid)
        self.assertTrue(out["landed"])
        self.assertEqual(out["engine"], "codex")
        self.assertEqual(out["display_name"], "spawn stats")

    def test_an_unknown_session_reports_not_landed_rather_than_erroring(self):
        server = self.server
        with mock.patch.object(server, "_codex_titles_snapshot", return_value={}):
            out = server._session_landed("nothing-here")
        self.assertTrue(out["ok"])
        self.assertFalse(out["landed"])

    def test_an_empty_id_is_rejected(self):
        self.assertFalse(self.server._session_landed("")["ok"])

    def test_the_probe_never_builds_the_archive_list(self):
        """The whole point: this must not cost a full corpus serialization."""
        server = self.server
        with mock.patch.object(server, "_codex_titles_snapshot", return_value={}), \
             mock.patch.object(server, "find_conversations") as build:
            server._session_landed("some-id")
        build.assert_not_called()


class CodexSignatureExtraKeysTests(unittest.TestCase):
    """The isolated-refresh keys must match what the signature actually folds in.

    _archive_signature_delta routes a codex-only extras change to an
    engine-scoped rebuild by matching against _archive_codex_extra_keys(). If
    that set drifts from _archive_corpus_signature_parts, the match silently
    fails and every new Codex session goes back to forcing a full rebuild --
    the 49.6s-to-appear bug, reintroduced quietly.
    """

    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_every_codex_key_is_one_the_signature_tracks(self):
        server = self.server
        _sig, _files, extras = server._archive_corpus_signature_parts()
        keys = server._archive_codex_extra_keys()
        self.assertTrue(keys, "no codex extra keys at all")
        present = [k for k in keys if k in extras]
        self.assertTrue(
            present,
            "none of _archive_codex_extra_keys() appear in the signature extras; "
            "the isolated codex refresh can never fire",
        )

    def test_the_rollout_day_dirs_are_included(self):
        """Codex nests rollouts by date, so today's dir is the one that moves."""
        server = self.server
        keys = server._archive_codex_extra_keys()
        for day_dir in server._codex_rollout_day_dirs():
            self.assertIn(str(day_dir), keys)


if __name__ == "__main__":
    unittest.main()
