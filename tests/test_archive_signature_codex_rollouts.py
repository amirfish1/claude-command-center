"""Regression coverage for Codex sessions missing from the session list.

Reported 2026-07-28: new Codex sessions sat in "spawning" forever and then
vanished, and existing Codex sessions appeared only after a UI reload --
while Codex desktop showed all of them correctly.

Root cause: `_archive_corpus_signature_parts` gated the Codex corpus on the
store ROOT, ~/.codex/sessions. Codex nests rollouts by date
(~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl), so the root's mtime only
moves at year rollover -- on the reporting machine it was 50 days stale.
Creating a Codex session therefore left the signature byte-identical, the
cached list response kept being served, and the session never appeared.
It surfaced only when unrelated Claude transcript activity happened to flip
the signature, which reads as "appears and then disappears".

The day directory is the correct granularity: its mtime changes when a
thread is ADDED (a new rollout file) but not when an existing transcript
grows -- unlike ~/.codex/state_*.sqlite, whose mtime flips every turn and
would pin the CPU with full rebuilds (the trap already documented for
kimi's session_index.jsonl).
"""

import importlib
import time
import unittest
from pathlib import Path
from unittest import mock


class CodexRolloutDayDirsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_day_dirs_cover_today_and_yesterday(self):
        """Yesterday is needed so a session started before midnight counts."""
        server = self.server
        dirs = [str(p) for p in server._codex_rollout_day_dirs()]
        today = time.strftime("%Y/%m/%d", time.localtime())
        yday = time.strftime("%Y/%m/%d", time.localtime(time.time() - 86400))
        self.assertTrue(any(d.endswith(today) for d in dirs), dirs)
        self.assertTrue(any(d.endswith(yday) for d in dirs), dirs)
        for d in dirs:
            self.assertIn(".codex", d)
            self.assertIn("sessions", d)

    def test_new_rollout_file_changes_the_corpus_signature(self):
        """A new Codex thread must invalidate the cached list response."""
        server = self.server
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026" / "07" / "28"
            day.mkdir(parents=True)
            with mock.patch.object(
                server, "_codex_rollout_day_dirs", return_value=[day]
            ):
                before = server._archive_corpus_signature_uncached()
                # A new Codex session = a new rollout file in the day dir.
                (day / "rollout-2026-07-28T09-00-00-019fa000.jsonl").write_text("{}\n")
                after = server._archive_corpus_signature_uncached()

        self.assertNotEqual(
            before, after,
            "creating a Codex rollout left the corpus signature unchanged, so "
            "the cached session list keeps being served and the new session "
            "stays invisible",
        )

    def test_appending_to_an_existing_rollout_does_not_bust_the_cache(self):
        """Per-turn growth must NOT force an O(all-sessions) rebuild."""
        server = self.server
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026" / "07" / "28"
            day.mkdir(parents=True)
            rollout = day / "rollout-2026-07-28T09-00-00-019fa000.jsonl"
            rollout.write_text("{}\n")
            with mock.patch.object(
                server, "_codex_rollout_day_dirs", return_value=[day]
            ):
                before = server._archive_corpus_signature_uncached()
                with rollout.open("a") as fh:
                    fh.write('{"turn": 2}\n')
                after = server._archive_corpus_signature_uncached()

        self.assertEqual(
            before, after,
            "an existing Codex transcript growing changed the signature; that "
            "would rebuild the whole archive on every turn",
        )

    def test_missing_day_dir_is_tolerated(self):
        """A machine that has never run Codex must not error."""
        server = self.server
        with mock.patch.object(
            server, "_codex_rollout_day_dirs",
            return_value=[Path("/nonexistent/.codex/sessions/2026/07/28")],
        ):
            self.assertTrue(server._archive_corpus_signature_uncached())


if __name__ == "__main__":
    unittest.main()
