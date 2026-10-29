"""Star ask (Q17): the polite, hard-capped "star us on GitHub" prompt.

Rules under test, all enforced in ccc_server/star_ask.py:
  - at most one ask per 14 days, at most 3 asks ever,
  - "Don't ask again" is permanent,
  - a confirmed star (our PUT, or the once-a-day remote check) ends asks,
  - "shown" posts inside a one-hour window are the same prompt, not a new ask,
  - no `gh` / unsigned `gh` degrade to a fallback link, never a 500.

No network, no real `gh`: tests drive the module's ``_run_gh`` / ``_gh_bin``
seams, same shape as tests/test_github_quota.py.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ccc_server import star_ask


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


DAY = 86400
NOW = 1_800_000_000.0


class StarAskTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_file = Path(self._tmp.name) / "star-ask.json"
        self._p_state = mock.patch.object(star_ask, "STATE_FILE", self.state_file)
        self._p_state.start()
        self.addCleanup(self._p_state.stop)
        # Default: gh present; tests that exercise the binary override it.
        self._p_gh = mock.patch.object(star_ask, "_gh_bin", return_value="/usr/bin/gh")
        self._p_gh.start()
        self.addCleanup(self._p_gh.stop)
        # Default remote check says "not starred" (404) so status() never
        # surprises a test by marking the repo starred.
        self._run_gh = mock.patch.object(
            star_ask, "_run_gh", return_value=_Proc(stderr="HTTP 404", returncode=1))
        self.gh_mock = self._run_gh.start()
        self.addCleanup(self._run_gh.stop)

    def status(self, now=NOW):
        return star_ask.status(now=now)

    def act(self, action, now=NOW):
        return star_ask.handle_action(action, now=now)

    # -- eligibility -------------------------------------------------------

    def test_fresh_user_is_eligible(self):
        st = self.status()
        self.assertTrue(st["ok"])
        self.assertTrue(st["should_ask"])
        self.assertEqual(st["asks_used"], 0)
        self.assertEqual(st["asks_max"], 3)
        self.assertTrue(st["gh_available"])
        self.assertIn("github.com", st["repo_url"])

    def test_shown_records_one_ask_and_blocks_repeat(self):
        payload, code = self.act("shown")
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["asks_used"], 1)
        # Same instant: the 14-day interval now blocks a second ask.
        self.assertFalse(self.status()["should_ask"])

    def test_shown_dedup_within_hour(self):
        self.act("shown", now=NOW)
        payload, _ = self.act("shown", now=NOW + 30)      # second tab, same prompt
        self.assertEqual(payload["asks_used"], 1)

    def test_asks_resume_after_14_days(self):
        self.act("shown", now=NOW)
        st = self.status(now=NOW + 15 * DAY)
        self.assertTrue(st["should_ask"])
        self.assertEqual(st["asks_used"], 1)

    def test_three_asks_is_the_lifetime_cap(self):
        for i in range(3):
            self.act("shown", now=NOW + i * 20 * DAY)
        st = self.status(now=NOW + 100 * DAY)
        self.assertFalse(st["should_ask"])
        self.assertEqual(st["asks_used"], 3)

    def test_never_is_permanent(self):
        payload, code = self.act("never")
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(self.status(now=NOW + 365 * DAY)["should_ask"])

    def test_later_does_not_spend_an_extra_ask(self):
        self.act("shown", now=NOW)
        payload, _ = self.act("later", now=NOW + 5)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.status(now=NOW + 5)["asks_used"], 1)

    # -- starring ----------------------------------------------------------

    def test_star_success_marks_starred_and_stops_asks(self):
        self.gh_mock.return_value = _Proc(returncode=0)
        payload, code = self.act("star")
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["starred"])
        self.assertEqual(self.gh_mock.call_args[0][0],
                         ["api", "-X", "PUT", f"user/starred/{star_ask.REPO}"])
        st = self.status(now=NOW + 365 * DAY)
        self.assertTrue(st["starred"])
        self.assertFalse(st["should_ask"])

    def test_star_persists_across_reads(self):
        self.gh_mock.return_value = _Proc(returncode=0)
        self.act("star")
        # A "new" read path sees the same file on disk.
        data = json.loads(self.state_file.read_text())
        self.assertTrue(data["starred"])

    def test_star_without_gh_returns_fallback(self):
        self._p_gh.stop()
        with mock.patch.object(star_ask, "_gh_bin", return_value=None):
            payload, code = self.act("star")
        self.assertEqual(code, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "gh_missing")
        self.assertEqual(payload["fallback_url"], star_ask.REPO_URL)

    def test_star_auth_failure_returns_fallback(self):
        self.gh_mock.return_value = _Proc(
            stderr="gh auth login: authentication required", returncode=1)
        payload, _ = self.act("star")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "gh_auth")
        self.assertEqual(payload["fallback_url"], star_ask.REPO_URL)
        # A failed star does not lock the feature out.
        self.assertTrue(self.status()["should_ask"])

    def test_star_when_already_starred_skips_gh(self):
        self.gh_mock.return_value = _Proc(returncode=0)
        self.act("star")
        self.gh_mock.reset_mock()
        payload, _ = self.act("star", now=NOW + 10)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["already"])
        self.gh_mock.assert_not_called()

    def test_gh_exception_returns_fallback(self):
        self.gh_mock.side_effect = FileNotFoundError("no gh")
        payload, _ = self.act("star")
        self.assertFalse(payload["ok"])
        self.assertIn("fallback_url", payload)

    # -- remote star check inside status() ---------------------------------

    def test_status_confirms_remote_star_once_a_day(self):
        self.gh_mock.return_value = _Proc(returncode=0)  # 204: starred
        st = self.status()
        self.assertTrue(st["starred"])
        self.assertFalse(st["should_ask"])
        self.assertEqual(self.gh_mock.call_count, 1)

    def test_status_remote_check_is_ttl_cached(self):
        self.status(now=NOW)
        self.status(now=NOW + 60)
        self.assertEqual(self.gh_mock.call_count, 1)
        self.status(now=NOW + DAY + 1)
        self.assertEqual(self.gh_mock.call_count, 2)

    def test_status_skips_remote_check_without_gh(self):
        self._p_gh.stop()
        with mock.patch.object(star_ask, "_gh_bin", return_value=None):
            st = self.status()
        self.assertFalse(st["gh_available"])
        self.assertTrue(st["should_ask"])
        self.assertFalse(st["starred"])
        self.gh_mock.assert_not_called()

    def test_status_remote_error_stays_unknown(self):
        self.gh_mock.return_value = _Proc(stderr="HTTP 502", returncode=1)
        st = self.status()
        self.assertFalse(st["starred"])
        self.assertTrue(st["should_ask"])

    # -- hygiene -----------------------------------------------------------

    def test_unknown_action_is_400(self):
        payload, code = self.act("explode")
        self.assertEqual(code, 400)
        self.assertFalse(payload["ok"])
        payload, code = self.act(None)
        self.assertEqual(code, 400)

    def test_corrupt_state_file_recovers(self):
        self.state_file.write_text("{not json")
        st = self.status()
        self.assertTrue(st["ok"])
        self.assertTrue(st["should_ask"])

    def test_state_file_round_trip(self):
        self.act("shown", now=NOW)
        self.act("never", now=NOW + 10)
        data = json.loads(self.state_file.read_text())
        self.assertTrue(data["never"])
        self.assertEqual(data["asks"], [NOW])


if __name__ == "__main__":
    unittest.main()
