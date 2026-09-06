"""Codex app-server must fail over fast instead of stalling a resume.

Both invariants here come from the 2026-09-06 incident where a resumed Codex
session sat on "Thinking..." for over ten minutes: the managed daemon accepted
the socket but never answered ``initialize``, and the stdio fallback was
refused because CCC's own dashboard held a read handle on the shared state DB.
"""

import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
