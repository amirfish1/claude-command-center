"""The archive serve path must not run a multi-second "cheap" refresh inline.

_archive_refresh_is_cheap predicts which TIER a refresh takes (rehydrate-only
or small delta) but not what that tier costs. The rehydrate is O(all rows), so
on a large corpus it runs for seconds — and it used to run synchronously on
every poll past the serve TTL, stalling roughly every other sidebar request.
These tests pin the measured-cost veto that demotes such a key to the
background refresh.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server


class TestArchiveSyncRefreshBudget(unittest.TestCase):
    def setUp(self):
        self._budget = server._ARCHIVE_SYNC_REFRESH_BUDGET
        self._reprobe = server._ARCHIVE_SYNC_REFRESH_REPROBE
        self._reprobe_max = server._ARCHIVE_SYNC_REFRESH_REPROBE_MAX
        self._block = dict(server._archive_sync_refresh_block)
        server._archive_sync_refresh_block.clear()

    def tearDown(self):
        server._ARCHIVE_SYNC_REFRESH_BUDGET = self._budget
        server._ARCHIVE_SYNC_REFRESH_REPROBE = self._reprobe
        server._ARCHIVE_SYNC_REFRESH_REPROBE_MAX = self._reprobe_max
        server._archive_sync_refresh_block.clear()
        server._archive_sync_refresh_block.update(self._block)

    def test_a_refresh_over_budget_blocks_the_next_sync_refresh(self):
        server._ARCHIVE_SYNC_REFRESH_BUDGET = 1.0
        server._ARCHIVE_SYNC_REFRESH_REPROBE = 60.0
        key = "k-over"

        self.assertTrue(server._archive_sync_refresh_allowed(key))
        server._archive_note_sync_refresh_cost(key, 6.5)
        self.assertFalse(server._archive_sync_refresh_allowed(key))

    def test_the_block_expires_so_a_cheaper_corpus_returns_to_sync(self):
        server._ARCHIVE_SYNC_REFRESH_BUDGET = 1.0
        server._ARCHIVE_SYNC_REFRESH_REPROBE = 60.0
        key = "k-expire"

        server._archive_note_sync_refresh_cost(key, 6.5)
        self.assertFalse(server._archive_sync_refresh_allowed(key))
        # Re-probe window elapsed.
        self.assertTrue(
            server._archive_sync_refresh_allowed(key, now=time.time() + 61)
        )

    def test_a_refresh_within_budget_clears_an_existing_block(self):
        server._ARCHIVE_SYNC_REFRESH_BUDGET = 1.0
        key = "k-recover"

        server._archive_note_sync_refresh_cost(key, 6.5)
        self.assertFalse(server._archive_sync_refresh_allowed(key))
        server._archive_note_sync_refresh_cost(key, 0.09)
        self.assertTrue(server._archive_sync_refresh_allowed(key))

    def test_blocked_key_is_not_reported_cheap_even_on_an_unchanged_corpus(self):
        """The veto short-circuits ahead of the signature probe, so a key whose
        last sync refresh blew the budget goes to the background path even when
        the corpus is byte-identical (the rehydrate-only tier)."""
        server._ARCHIVE_SYNC_REFRESH_BUDGET = 1.0
        server._ARCHIVE_SYNC_REFRESH_REPROBE = 60.0
        key = "k-cheap-tier"

        calls = []
        original = server._archive_response_cache_signature
        server._archive_response_cache_signature = lambda k: calls.append(k) or "sig"
        try:
            server._archive_note_sync_refresh_cost(key, 6.5)
            self.assertFalse(server._archive_refresh_is_cheap(key, {}))
            self.assertEqual(calls, [], "signature probe should be short-circuited")
        finally:
            server._archive_response_cache_signature = original

    def test_consecutive_blowouts_back_the_reprobe_window_off(self):
        """A corpus that is simply large stays expensive, so re-probing every
        60s forever just re-pays the stall. Each consecutive blowout doubles
        the window, up to the cap."""
        server._ARCHIVE_SYNC_REFRESH_BUDGET = 1.0
        server._ARCHIVE_SYNC_REFRESH_REPROBE = 60.0
        server._ARCHIVE_SYNC_REFRESH_REPROBE_MAX = 240.0
        key = "k-backoff"

        windows = []
        for _ in range(5):
            server._archive_note_sync_refresh_cost(key, 6.5)
            windows.append(server._archive_sync_refresh_block[key]["window"])

        self.assertEqual(windows, [60.0, 120.0, 240.0, 240.0, 240.0])

    def test_an_on_budget_run_resets_the_backoff(self):
        server._ARCHIVE_SYNC_REFRESH_BUDGET = 1.0
        server._ARCHIVE_SYNC_REFRESH_REPROBE = 60.0
        server._ARCHIVE_SYNC_REFRESH_REPROBE_MAX = 900.0
        key = "k-backoff-reset"

        server._archive_note_sync_refresh_cost(key, 6.5)
        server._archive_note_sync_refresh_cost(key, 6.5)
        self.assertEqual(server._archive_sync_refresh_block[key]["window"], 120.0)

        server._archive_note_sync_refresh_cost(key, 0.09)
        server._archive_note_sync_refresh_cost(key, 6.5)
        self.assertEqual(server._archive_sync_refresh_block[key]["window"], 60.0)

    def test_a_zero_budget_disables_synchronous_refresh_entirely(self):
        server._ARCHIVE_SYNC_REFRESH_BUDGET = 0.0
        self.assertFalse(server._archive_sync_refresh_allowed("k-off"))


if __name__ == "__main__":
    unittest.main()
