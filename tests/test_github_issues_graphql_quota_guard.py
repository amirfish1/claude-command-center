"""CCC's rate-limit brake must read the in-band GraphQL meter, not REST.

`gh api rate_limit`'s `.resources.graphql` block is not this token's GraphQL
quota. Measured at one instant it reported remaining=5000/used=0 while
`gh api graphql '{rateLimit{...}}'` reported remaining=1257/used=3743 for the
same token. The guard compared that pinned 5000 against a threshold of 200, so
it never fired and CCC kept polling straight through quota exhaustion.
"""
import sys
from datetime import datetime, timedelta, timezone
from unittest import mock

import server  # noqa: F401 — import order matters, registers ccc_server.github_issues

from ccc_server import github_quota

gi = sys.modules["ccc_server.github_issues"]


def _reset_state():
    gi._GH_RATE_LIMIT_STATE["last_error_ts"] = 0.0
    gi._GH_RATE_LIMIT_STATE["last_error_reset"] = 0
    gi._GH_RATE_LIMIT_STATE["last_remaining"] = None
    gi._GH_RATE_LIMIT_STATE["last_check_ts"] = 0.0


def _quota(remaining, minutes=30):
    reset_at = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"ok": True, "used": 5000 - remaining, "limit": 5000,
            "remaining": remaining, "reset_at": reset_at}


def test_low_graphql_remaining_trips_the_backoff():
    _reset_state()
    with mock.patch.object(github_quota, "read_graphql_quota", return_value=_quota(50)):
        gi._check_gh_rate_limit()
    assert gi.github_rate_limited()["rate_limited"] is True


def test_backoff_runs_until_the_graphql_reset_time():
    _reset_state()
    with mock.patch.object(github_quota, "read_graphql_quota", return_value=_quota(50, minutes=30)):
        gi._check_gh_rate_limit()
    assert gi.github_rate_limited()["backoff_seconds"] > 1500


def test_healthy_graphql_remaining_leaves_the_brake_off():
    _reset_state()
    with mock.patch.object(github_quota, "read_graphql_quota", return_value=_quota(4800)):
        gi._check_gh_rate_limit()
    assert gi.github_rate_limited()["rate_limited"] is False


def test_quota_read_failure_does_not_trip_the_backoff():
    _reset_state()
    with mock.patch.object(github_quota, "read_graphql_quota",
                           return_value={"ok": False, "error": "gh exited non-zero"}):
        gi._check_gh_rate_limit()
    assert gi.github_rate_limited()["rate_limited"] is False
