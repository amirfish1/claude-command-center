"""Lane W6-1: GitHub GraphQL quota meter + the shared caches that spend it.

These are call-count tests in the spirit of tests/test_perf_budget.py. The
regression they guard against is not latency, it is *points*: GraphQL charges
~1 point per 100 nodes against a 5,000/hr per-user budget, and every incident
so far has been the same shape -- a cached-looking path that still forks one
`gh` per caller because the cache had a TTL but no single-flight.

No network, no real `gh`: every test drives the module's own subprocess seam.
"""

import json
import os
import threading
import time
import unittest
from unittest import mock

from ccc_server import github_quota


QUOTA_JSON = json.dumps({
    "data": {"rateLimit": {
        "limit": 5000, "cost": 1, "remaining": 3975, "used": 1025,
        "resetAt": "2026-09-04T04:55:09Z",
    }}
})


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _slow_proc(stdout, delay=0.15):
    def _run(*_a, **_kw):
        time.sleep(delay)
        return _Proc(stdout)
    return _run


class QuotaReadTests(unittest.TestCase):
    def setUp(self):
        github_quota.reset_quota_cache()
        self.addCleanup(github_quota.reset_quota_cache)

    def test_reads_the_in_band_ratelimit_block_not_gh_api_rate_limit(self):
        """`gh api rate_limit` reports a different bucket and can read
        used=0 while the real one is 20% left (OPS-929). Assert on argv."""
        with mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc(QUOTA_JSON)) as run:
            out = github_quota.read_graphql_quota()
        argv = run.call_args[0][0]
        self.assertEqual(argv[:3], ["api", "graphql", "-f"])
        self.assertIn("rateLimit", argv[3])
        self.assertNotIn("rate_limit", " ".join(argv))
        self.assertTrue(out["ok"])
        self.assertEqual(out["used"], 1025)
        self.assertEqual(out["remaining"], 3975)
        self.assertEqual(out["limit"], 5000)
        self.assertEqual(out["used_pct"], 20.5)
        self.assertEqual(out["reset_at"], "2026-09-04T04:55:09Z")
        self.assertEqual(out["source"], "graphql-in-band")

    def test_ttl_cached_so_polling_the_endpoint_costs_one_fork(self):
        with mock.patch.dict(os.environ, {"CCC_GH_QUOTA_TTL_S": "3600"}), \
             mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc(QUOTA_JSON)) as run:
            for _ in range(25):
                github_quota.read_graphql_quota()
        self.assertEqual(run.call_count, 1, "quota read must be TTL-cached")

    def test_force_bypasses_the_ttl(self):
        with mock.patch.dict(os.environ, {"CCC_GH_QUOTA_TTL_S": "3600"}), \
             mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc(QUOTA_JSON)) as run:
            github_quota.read_graphql_quota()
            github_quota.read_graphql_quota(force=True)
        self.assertEqual(run.call_count, 2)

    def test_concurrent_cold_reads_collapse_to_one_fork(self):
        with mock.patch.dict(os.environ, {"CCC_GH_QUOTA_TTL_S": "3600"}), \
             mock.patch.object(github_quota, "_run_gh",
                               side_effect=_slow_proc(QUOTA_JSON)) as run:
            results = []
            threads = [threading.Thread(
                target=lambda: results.append(github_quota.read_graphql_quota()))
                for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(run.call_count, 1, "cold quota read must single-flight")
        self.assertEqual(len(results), 8)
        self.assertTrue(all(r["ok"] and r["used"] == 1025 for r in results))

    def test_failure_never_raises_and_keeps_the_last_good_reading(self):
        with mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc(QUOTA_JSON)):
            github_quota.read_graphql_quota()
        with mock.patch.dict(os.environ, {"CCC_GH_QUOTA_TTL_S": "0"}), \
             mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc("", returncode=1, stderr="boom")):
            out = github_quota.read_graphql_quota()
        self.assertTrue(out["ok"], "a transient gh failure must not blank the meter")
        self.assertEqual(out["used"], 1025)

    def test_cold_failure_reports_the_error_instead_of_raising(self):
        with mock.patch.object(github_quota, "_run_gh",
                               side_effect=OSError("gh not found")):
            out = github_quota.read_graphql_quota()
        self.assertFalse(out["ok"])
        self.assertIn("gh not found", out["error"])


class OpenPrCacheTests(unittest.TestCase):
    """`gh pr list` with statusCheckRollup measured at 2.9 points -- the most
    expensive call CCC makes. It used to be cached 30s in TWO places with no
    single-flight."""

    PRS = json.dumps([{"number": 7, "title": "t", "headRefName": "b",
                       "statusCheckRollup": []}])

    def setUp(self):
        github_quota.bust_pr_cache()
        self.addCleanup(github_quota.bust_pr_cache)
        # These cases exercise the cache, not the candidacy gate (which has
        # its own class below) -- so the synthetic paths are declared eligible.
        gate = mock.patch.object(github_quota, "has_github_remote", return_value=True)
        gate.start()
        self.addCleanup(gate.stop)

    def test_repeated_callers_share_one_fetch(self):
        with mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc(self.PRS)) as run:
            for _ in range(20):
                prs, err = github_quota.open_prs("/repo/a")
        self.assertEqual(run.call_count, 1)
        self.assertIsNone(err)
        self.assertEqual(prs[0]["number"], 7)

    def test_concurrent_callers_collapse_to_one_fetch(self):
        with mock.patch.object(github_quota, "_run_gh",
                               side_effect=_slow_proc(self.PRS)) as run:
            seen = []
            threads = [threading.Thread(
                target=lambda: seen.append(github_quota.open_prs("/repo/a")))
                for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(run.call_count, 1,
                         "concurrent PR fetches for one repo must single-flight")
        self.assertTrue(all(p[0][0]["number"] == 7 for p in seen))

    def test_checks_variant_is_cached_separately(self):
        """A caller that needs statusCheckRollup must never be served the
        cheap payload that lacks it."""
        with mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc(self.PRS)) as run:
            github_quota.open_prs("/repo/a", checks=True)
            github_quota.open_prs("/repo/a", checks=False)
            github_quota.open_prs("/repo/a", checks=True)
            github_quota.open_prs("/repo/a", checks=False)
        self.assertEqual(run.call_count, 2)
        light = [c for c in run.call_args_list
                 if "statusCheckRollup" not in c[0][0][-1]]
        self.assertEqual(len(light), 1)

    def test_limit_is_env_tunable_and_passed_to_gh(self):
        with mock.patch.dict(os.environ, {"CCC_GH_PR_LIMIT": "25"}), \
             mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc(self.PRS)) as run:
            github_quota.open_prs("/repo/limit")
        argv = run.call_args[0][0]
        self.assertIn("--limit", argv)
        self.assertEqual(argv[argv.index("--limit") + 1], "25")

    def test_default_ttl_is_far_longer_than_the_old_30s(self):
        self.assertGreaterEqual(
            github_quota.pr_ttl_s(), 300,
            "the 2.9-point PR call must not go back to a 30s TTL")

    def test_gh_failure_keeps_serving_the_last_good_list_and_does_not_retry(self):
        with mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc(self.PRS)):
            github_quota.open_prs("/repo/a", ttl=0)
        with mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc("", returncode=1, stderr="rate limited")) as run:
            prs, err = github_quota.open_prs("/repo/a", ttl=0)
            # A failure refreshes the timestamp, so the next call inside the
            # TTL must NOT fork again -- retrying a rate-limited `gh` per
            # request is how a limit window becomes a storm.
            prs2, _ = github_quota.open_prs("/repo/a")
        self.assertEqual(run.call_count, 1)
        self.assertIn("rate limited", err)
        self.assertEqual(prs[0]["number"], 7, "stale-but-true beats a blank panel")
        self.assertEqual(prs2[0]["number"], 7)

    def test_bust_is_scoped_to_one_repo(self):
        with mock.patch.object(github_quota, "_run_gh",
                               return_value=_Proc(self.PRS)) as run:
            github_quota.open_prs("/repo/a")
            github_quota.open_prs("/repo/b")
            github_quota.bust_pr_cache("/repo/a")
            github_quota.open_prs("/repo/a")
            github_quota.open_prs("/repo/b")
        self.assertEqual(run.call_count, 3)

    def test_empty_repo_path_never_forks(self):
        with mock.patch.object(github_quota, "_run_gh") as run:
            self.assertEqual(github_quota.open_prs(""), ([], None))
        run.assert_not_called()


class LimitBudgetTests(unittest.TestCase):
    """GraphQL costs 1 point per 100 nodes REQUESTED, so `--limit` is the
    only real lever. Guard the defaults against creeping back up."""

    def test_defaults_are_sized_to_what_the_ui_renders(self):
        self.assertLessEqual(github_quota.pr_limit(), 100)
        self.assertLessEqual(github_quota.issue_limit_open(), 100)
        self.assertLessEqual(github_quota.issue_limit_closed(), 100)
        self.assertLessEqual(
            github_quota.issue_limit_states(), 200,
            "issue-state map was --limit 500 = up to 5 points on a big repo")
        self.assertLessEqual(github_quota.issue_limit_titles(), 200)

    def test_env_overrides_are_clamped_not_trusted(self):
        with mock.patch.dict(os.environ, {"CCC_GH_PR_LIMIT": "100000"}):
            self.assertEqual(github_quota.pr_limit(), 100)
        with mock.patch.dict(os.environ, {"CCC_GH_PR_LIMIT": "-5"}):
            self.assertEqual(github_quota.pr_limit(), 1)
        with mock.patch.dict(os.environ, {"CCC_GH_PR_LIMIT": "not-a-number"}):
            self.assertEqual(github_quota.pr_limit(), 60)


class SingleFlightTests(unittest.TestCase):
    def test_leader_runs_once_and_followers_do_not(self):
        calls = []

        def _produce():
            calls.append(1)
            time.sleep(0.15)
            return "value"

        results = []
        threads = [threading.Thread(
            target=lambda: results.append(
                github_quota.single_flight("k", _produce)))
            for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(calls), 1)
        leaders = [r for r in results if r[1]]
        self.assertEqual(len(leaders), 1)
        self.assertEqual(leaders[0][0], "value")
        self.assertTrue(all(r[0] is None for r in results if not r[1]))

    def test_key_is_released_even_when_produce_raises(self):
        with self.assertRaises(ValueError):
            github_quota.single_flight("boom", lambda: (_ for _ in ()).throw(ValueError()))
        # A leaked key would deadlock every later caller behind a timeout.
        value, leader = github_quota.single_flight("boom", lambda: 42)
        self.assertTrue(leader)
        self.assertEqual(value, 42)

    def test_different_keys_do_not_block_each_other(self):
        order = []

        def _slow():
            time.sleep(0.2)
            order.append("slow")
            return 1

        t = threading.Thread(target=lambda: github_quota.single_flight("a", _slow))
        t.start()
        time.sleep(0.02)
        github_quota.single_flight("b", lambda: order.append("fast"))
        t.join()
        self.assertEqual(order, ["fast", "slow"])


class CallSiteWiringTests(unittest.TestCase):
    """The two PR call sites must actually route through the shared cache --
    a duplicate cache is exactly the bug this lane fixed."""

    def test_no_module_keeps_its_own_30s_pr_ttl(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        for name in ("fleet.py", "morning_launch.py"):
            text = (root / "ccc_server" / name).read_text()
            self.assertNotIn("_FLEET_PRS_TTL = 30.0", text)
            self.assertNotIn("_OPEN_PRS_TTL = 30.0", text)
            self.assertIn("_github_quota.open_prs", text,
                          f"{name} must share the PR cache, not re-fetch")

    def test_no_call_site_hardcodes_an_issue_list_limit(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        bad = []
        for name in ("cross_repo_issues.py", "session_graph.py"):
            text = (root / "ccc_server" / name).read_text()
            for m in re.finditer(r'"--limit",\s*"(\d+)"', text):
                bad.append(f"{name}:{m.group(1)}")
        self.assertEqual(
            bad, [],
            "issue-list --limit must come from github_quota (the cost lever)")


class GithubRemoteGateTests(unittest.TestCase):
    """The cross-repo PR sweep fans out over every known repo, which on a real
    machine includes /opt/homebrew, index caches and agent scratch dirs. Those
    must never reach a `gh` fork -- that is the subprocess-per-row shape the
    repo's perf gates exist to stop."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        github_quota.bust_pr_cache()
        self.addCleanup(github_quota.bust_pr_cache)
        with github_quota._REMOTE_LOCK:
            github_quota._REMOTE_CACHE.clear()

    def _repo(self, name, config_text):
        import os as _os
        root = _os.path.join(self.tmp, name)
        _os.makedirs(_os.path.join(root, ".git"))
        if config_text is not None:
            with open(_os.path.join(root, ".git", "config"), "w") as fh:
                fh.write(config_text)
        return root

    def test_github_remote_detected(self):
        root = self._repo("gh", '[remote "origin"]\n\turl = git@github.com:o/r.git\n')
        self.assertTrue(github_quota.has_github_remote(root))

    def test_non_github_repo_never_forks_gh(self):
        root = self._repo("brew", '[remote "origin"]\n\turl = https://gitlab.com/o/r.git\n')
        with mock.patch.object(github_quota, "_run_gh") as run:
            self.assertEqual(github_quota.open_prs(root), ([], None))
        run.assert_not_called()

    def test_repo_without_git_dir_never_forks_gh(self):
        import os as _os
        root = _os.path.join(self.tmp, "plain")
        _os.makedirs(root)
        with mock.patch.object(github_quota, "_run_gh") as run:
            self.assertEqual(github_quota.open_prs(root), ([], None))
        run.assert_not_called()

    def test_verdict_is_memoised_and_reads_no_subprocess(self):
        root = self._repo("gh2", '[remote "origin"]\n\turl = https://github.com/o/r\n')
        with mock.patch.object(github_quota, "_run_gh") as run:
            for _ in range(50):
                github_quota.has_github_remote(root)
        run.assert_not_called()
        self.assertEqual(len(github_quota._REMOTE_CACHE), 1)

    def test_added_remote_is_picked_up_without_restart(self):
        root = self._repo("late", '[core]\n\tbare = false\n')
        self.assertFalse(github_quota.has_github_remote(root))
        import os as _os, time as _time
        cfg = _os.path.join(root, ".git", "config")
        with open(cfg, "a") as fh:
            fh.write('[remote "origin"]\n\turl = git@github.com:o/r.git\n')
        _os.utime(cfg, (_time.time() + 5, _time.time() + 5))
        self.assertTrue(github_quota.has_github_remote(root))

    def test_worktree_git_file_is_not_skipped(self):
        """A linked worktree keeps a `.git` FILE, not a dir -- it must still be
        allowed through rather than silently losing its PRs."""
        import os as _os
        root = _os.path.join(self.tmp, "wt")
        _os.makedirs(root)
        with open(_os.path.join(root, ".git"), "w") as fh:
            fh.write("gitdir: /somewhere/.git/worktrees/wt\n")
        self.assertTrue(github_quota.has_github_remote(root))


if __name__ == "__main__":
    unittest.main()
