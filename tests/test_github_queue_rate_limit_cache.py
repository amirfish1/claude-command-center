"""A sustained GitHub API rate-limit window must not empty out a GitHub-backed
queue's cached issue list.

list_issues() serves its cache for _LIST_ISSUES_RATE_LIMIT_TTL (300s) while
rate-limited. Once that window lapses but the backoff hasn't, the old code
fell through to a live `gh` call anyway — which fails during a rate limit,
and `_gh(...) or []` can't tell "confirmed zero issues" from "couldn't
check", so the empty result got written back into the cache, permanently
losing the queue's tickets until the next successful fetch (CCC-1021).
"""
import sys
import time
from unittest import mock

import server  # noqa: F401 — import order matters, registers ccc_server.github_issues

from ccc_server import github_quota

gi = sys.modules["ccc_server.github_issues"]


def _reset_state():
    github_quota.reset_quota_cache()
    gi._LIST_ISSUES_CACHE["by_repo"].clear()
    gi._GH_RATE_LIMIT_STATE["last_error_ts"] = 0.0
    gi._GH_RATE_LIMIT_STATE["last_error_reset"] = 0
    gi._GH_RATE_LIMIT_STATE["last_check_ts"] = 0.0


def _open_issues_response(n):
    return mock.Mock(
        returncode=0,
        stdout='[{"number": %d, "title": "t", "labels": [], "state": "OPEN", "updatedAt": ""}]' % n,
        stderr="",
    )


_EMPTY_RESPONSE = mock.Mock(returncode=0, stdout="[]", stderr="")
_RATE_LIMIT_ERROR_RESPONSE = mock.Mock(returncode=1, stdout="", stderr="API rate limit exceeded")


_HEALTHY_QUOTA_RESPONSE = mock.Mock(
    returncode=0,
    stdout='{"data":{"rateLimit":{"limit":5000,"cost":1,"remaining":4900,'
           '"used":100,"resetAt":"2099-01-01T00:00:00Z"}}}',
    stderr="",
)


def _run_stub(open_response, closed_response=_EMPTY_RESPONSE, rate_limit_response=_EMPTY_RESPONSE,
              quota_response=_HEALTHY_QUOTA_RESPONSE):
    def fake_run(args, **kwargs):
        if "graphql" in args:
            return quota_response
        if "rate_limit" in args:
            return rate_limit_response
        if "--state" in args and "closed" in args:
            return closed_response
        return open_response
    return fake_run


def test_stale_rate_limit_window_keeps_serving_last_known_issues():
    _reset_state()
    repo = "/tmp/demo-repo"

    with mock.patch.object(gi, "_core") as core, \
         mock.patch.object(gi.subprocess, "run", side_effect=_run_stub(_open_issues_response(1))):
        core.resolve_repo_path.return_value = repo
        core._strip_title_prefix.side_effect = lambda t: t
        first = gi.list_issues(repo)
    assert len(first) == 1, "sanity: a real fetch populates the cache"

    # Age the cache entry past the rate-limit stale-serving window, and mark
    # the client as still rate-limited — the exact state a >5min GitHub
    # outage leaves behind.
    entry = gi._LIST_ISSUES_CACHE["by_repo"][str(repo)]
    entry["ts"] -= gi._LIST_ISSUES_RATE_LIMIT_TTL + 1
    gi._GH_RATE_LIMIT_STATE["last_error_ts"] = time.time()

    with mock.patch.object(gi, "_core") as core, \
         mock.patch.object(gi.subprocess, "run", side_effect=_run_stub(_RATE_LIMIT_ERROR_RESPONSE)) as run:
        core.resolve_repo_path.return_value = repo
        second = gi.list_issues(repo)

    assert len(second) == 1, "must keep serving the last known-good issues, not []"
    assert second[0]["number"] == 1
    # Still rate-limited: no gh call should even be attempted.
    run.assert_not_called()


def test_non_rate_limited_empty_result_still_overwrites_cache():
    """A genuine zero-issues fetch (no rate limit in play) must still clear
    a previously non-empty cache — only the rate-limited path should hold."""
    _reset_state()
    repo = "/tmp/demo-repo-2"

    with mock.patch.object(gi, "_core") as core, \
         mock.patch.object(gi.subprocess, "run", side_effect=_run_stub(_open_issues_response(1))):
        core.resolve_repo_path.return_value = repo
        core._strip_title_prefix.side_effect = lambda t: t
        gi.list_issues(repo)

    entry = gi._LIST_ISSUES_CACHE["by_repo"][str(repo)]
    entry["ts"] -= gi._LIST_ISSUES_TTL + 1  # past the normal TTL, not rate-limited

    with mock.patch.object(gi, "_core") as core, \
         mock.patch.object(gi.subprocess, "run", side_effect=_run_stub(_EMPTY_RESPONSE)):
        core.resolve_repo_path.return_value = repo
        result = gi.list_issues(repo)

    assert result == [], "a genuinely confirmed empty fetch is not held back"
