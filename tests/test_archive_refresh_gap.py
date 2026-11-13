"""CCC-142 (Q980): bursts of archive/trash mutations must not start a detached
archive rebuild back to back. Each mutation ages the snapshot and clears the
in-flight slot, so a bulk burst used to queue rebuilds that were discarded."""
import unittest

import server


class ArchiveRefreshGapTest(unittest.TestCase):
    def setUp(self):
        self._saved = (dict(server._archive_serve_refreshing),
                       dict(server._archive_refresh_last_start),
                       server._ARCHIVE_REFRESH_GATE_BYPASS_UNTIL[0])
        self.addCleanup(self._restore)
        server._archive_serve_refreshing.clear()
        server._archive_refresh_last_start.clear()
        server._ARCHIVE_REFRESH_GATE_BYPASS_UNTIL[0] = 0.0

    def _restore(self):
        server._archive_serve_refreshing.clear()
        server._archive_serve_refreshing.update(self._saved[0])
        server._archive_refresh_last_start.clear()
        server._archive_refresh_last_start.update(self._saved[1])
        server._ARCHIVE_REFRESH_GATE_BYPASS_UNTIL[0] = self._saved[2]

    def test_first_refresh_is_allowed(self):
        self.assertTrue(server._archive_refresh_slot_available("k", 1000.0))

    def test_restart_inside_gap_is_refused_even_after_slot_cleared(self):
        gap = server._ARCHIVE_REFRESH_MIN_GAP_S
        server._archive_refresh_mark_started("k", 1000.0)
        server._archive_serve_refreshing.clear()  # what a mutation restamp does
        self.assertFalse(server._archive_refresh_slot_available("k", 1000.0 + gap - 1))
        self.assertTrue(server._archive_refresh_slot_available("k", 1000.0 + gap + 1))

    def test_new_transcript_bypass_skips_the_gap(self):
        server._archive_refresh_mark_started("k", 1000.0)
        server._archive_serve_refreshing.clear()
        server._ARCHIVE_REFRESH_GATE_BYPASS_UNTIL[0] = 1100.0
        self.assertTrue(server._archive_refresh_slot_available("k", 1010.0))

    def test_other_keys_are_independent(self):
        server._archive_refresh_mark_started("a", 1000.0)
        server._archive_serve_refreshing.clear()
        self.assertTrue(server._archive_refresh_slot_available("b", 1001.0))


if __name__ == "__main__":
    unittest.main()
