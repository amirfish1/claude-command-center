"""GitHub GraphQL quota meter + the shared caches that spend it.

Lane W6-1. Two jobs, deliberately in one module because they are the same
concern: GraphQL points are a scarce shared budget (5,000/hr, aggregated per
GitHub *user*), so the thing that spends them and the thing that measures them
should not drift apart.

**Read the quota in band.** ``gh api rate_limit``'s ``.resources.graphql``
block is not this token's GraphQL quota — measured at one instant it said
``used=0 remaining=5000`` while the authoritative in-band block said
``used=1025 remaining=3975`` (OPS-929). Only ``{rateLimit{...}}`` inside a
GraphQL query tells the truth. A ``rateLimit``-only query is itself free:
60 back-to-back queries moved ``used`` by 76 against an idle control of 83
over the same wall time, i.e. net <= 0.

**Cost is node-count based**, ~1 point per 100 nodes requested, so ``--limit``
is the lever and the JSON field list is not. Measured on this repo, drift
corrected over 10 runs each:

===================================================  ==========
call                                                 pts/call
===================================================  ==========
``pr list --limit 100`` **with** ``statusCheckRollup``  2.9
``pr list --limit 100`` without it                      1.0
``issue list --state open --limit 100`` (+body)         1.3
``issue list --state closed --limit 60`` (+body)        0.4
===================================================  ==========

The PR list is the single most expensive call CCC makes, and before this
module it sat behind a 30s memory-only cache in *two* places
(``morning_launch._open_prs_cached`` and ``fleet._fleet_prs``) with no
single-flight, so two dashboard panes meant two fetches. Both now route here.

Stdlib only, and no ``import server`` — this module must stay importable on
its own so the meter works from tests and from ``ccc doctor``.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time

# ---------------------------------------------------------------------------
# Tunables. Every one is an env var because the right value depends on how
# many repos the fleet spans, and that is a per-machine fact.
# ---------------------------------------------------------------------------


def _env_int(name, default, low, high):
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except (ValueError, AttributeError):
        return default
    return max(low, min(value, high))


def quota_ttl_s():
    """How long a quota reading is reused. The read is free, but it still
    costs a subprocess fork, so don't do it per request."""
    return _env_int("CCC_GH_QUOTA_TTL_S", 60, 5, 3600)


def pr_ttl_s():
    """Shared TTL for `gh pr list`. Was 30s in two separate caches; open PRs
    do not change every 30 seconds and this is the 2.9-point call."""
    return _env_int("CCC_GH_PR_TTL_S", 300, 10, 3600)


def pr_limit():
    """`--limit` for `gh pr list`. 1 point per 100 nodes: a repo with 12 open
    PRs pays the same as one with 100 unless you ask for less."""
    return _env_int("CCC_GH_PR_LIMIT", 60, 1, 100)


def issue_limit_open():
    return _env_int("CCC_GH_ISSUE_LIMIT_OPEN", 100, 1, 500)


def issue_limit_closed():
    return _env_int("CCC_GH_ISSUE_LIMIT_CLOSED", 60, 1, 500)


def issue_limit_states():
    """`--limit` for the number->state/labels map. Was a flat 500 (up to 5
    pages = 5 points on a repo with 1000+ issues) to back a UI that renders
    the recent slice."""
    return _env_int("CCC_GH_ISSUE_LIMIT_STATES", 200, 1, 1000)


def issue_limit_titles():
    return _env_int("CCC_GH_ISSUE_LIMIT_TITLES", 200, 1, 1000)


# ---------------------------------------------------------------------------
# Single-flight: N concurrent askers for the same key produce ONE subprocess.
# ---------------------------------------------------------------------------

_FLIGHT_LOCK = threading.Lock()
_FLIGHTS = {}  # key -> threading.Event


def single_flight(key, produce, wait_timeout=20.0):
    """Run ``produce()`` for ``key`` once; concurrent callers wait for it.

    Returns ``(value, was_leader)``. Followers get ``(None, False)`` — the
    caller is expected to re-read its own cache, which the leader has just
    filled. That keeps this helper free of any opinion about where results
    live, so both the PR cache and the issue caches can use it.

    A follower that times out also gets ``(None, False)`` rather than
    stampeding, because a leader that is stuck on a 10s `gh` timeout is
    exactly when a stampede hurts most.
    """
    with _FLIGHT_LOCK:
        event = _FLIGHTS.get(key)
        leader = event is None
        if leader:
            event = threading.Event()
            _FLIGHTS[key] = event
    if not leader:
        event.wait(wait_timeout)
        return None, False
    try:
        return produce(), True
    finally:
        with _FLIGHT_LOCK:
            _FLIGHTS.pop(key, None)
        event.set()


# ---------------------------------------------------------------------------
# The meter
# ---------------------------------------------------------------------------

_QUOTA_LOCK = threading.Lock()
_QUOTA_CACHE = {"ts": 0.0, "data": None}

_QUOTA_QUERY = "{rateLimit{limit cost remaining used resetAt}}"


def _run_gh(args, cwd=None, timeout=10):
    return subprocess.run(
        ["gh"] + list(args),
        capture_output=True, text=True, timeout=timeout,
        cwd=str(cwd) if cwd else None,
    )


def _fetch_graphql_quota():
    try:
        out = _run_gh(["api", "graphql", "-f", f"query={_QUOTA_QUERY}"])
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)[:200], "checked_at": time.time()}
    if out.returncode != 0:
        detail = (out.stderr or out.stdout or "gh exited non-zero").strip()
        return {"ok": False, "error": detail[:200], "checked_at": time.time()}
    try:
        payload = json.loads(out.stdout)
    except ValueError:
        return {"ok": False, "error": "malformed JSON from gh", "checked_at": time.time()}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "unexpected JSON shape from gh", "checked_at": time.time()}
    block = (payload.get("data") or {}).get("rateLimit") or {}
    used = block.get("used")
    limit = block.get("limit")
    remaining = block.get("remaining")
    pct = None
    if isinstance(used, int) and isinstance(limit, int) and limit > 0:
        pct = round(100.0 * used / limit, 1)
    return {
        "ok": True,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "reset_at": block.get("resetAt"),
        "used_pct": pct,
        "source": "graphql-in-band",
        "checked_at": time.time(),
    }


def read_graphql_quota(force=False):
    """Authoritative GraphQL quota, TTL-cached and single-flighted.

    Never raises: a failure comes back as ``{"ok": False, "error": ...}`` so a
    doctor line or an API response degrades to a message instead of a 500.
    """
    now = time.time()
    with _QUOTA_LOCK:
        cached = _QUOTA_CACHE["data"]
        fresh = cached is not None and (now - _QUOTA_CACHE["ts"]) < quota_ttl_s()
    if fresh and not force:
        return dict(cached, cached=True)

    def _produce():
        data = _fetch_graphql_quota()
        with _QUOTA_LOCK:
            # Keep the last good reading on failure so the UI can still show
            # a stale-but-true number rather than nothing.
            if data.get("ok") or _QUOTA_CACHE["data"] is None:
                _QUOTA_CACHE["data"] = data
                _QUOTA_CACHE["ts"] = time.time()
        return data

    value, was_leader = single_flight("graphql-quota", _produce)
    if was_leader and value is not None:
        return dict(value, cached=False)
    with _QUOTA_LOCK:
        cached = _QUOTA_CACHE["data"]
    if cached is not None:
        return dict(cached, cached=True)
    return {"ok": False, "error": "quota unavailable", "checked_at": now}


def reset_quota_cache():
    """Test seam — drop the cached reading."""
    with _QUOTA_LOCK:
        _QUOTA_CACHE["ts"] = 0.0
        _QUOTA_CACHE["data"] = None


# ---------------------------------------------------------------------------
# Shared open-PR list. One cache, one flight, for every caller in the process.
# ---------------------------------------------------------------------------

_PR_LOCK = threading.Lock()
_PR_CACHE = {}  # repo_path -> {"ts": float, "prs": list, "error": str|None}

# Everything any current caller renders. Keep this ONE list: a second field
# set means a second cache key means a second 2.9-point fetch.
PR_JSON_FIELDS = (
    "number,title,headRefName,headRefOid,isDraft,url,updatedAt,createdAt,"
    "statusCheckRollup,mergeable,mergeStateStatus,reviewDecision"
)

# Field set for callers that only need identity, not merge/CI readiness.
# Measured at 1.0 pt vs 2.9 -- statusCheckRollup is two thirds of the cost.
PR_JSON_FIELDS_LIGHT = (
    "number,title,headRefName,isDraft,url,updatedAt,createdAt"
)


# ---------------------------------------------------------------------------
# Candidacy gate: never fork `gh` for a repo that has no GitHub remote.
# ---------------------------------------------------------------------------

_REMOTE_LOCK = threading.Lock()
_REMOTE_CACHE = {}  # repo_path -> (config_mtime, bool)


def has_github_remote(repo_path):
    """True if ``repo_path`` is a git repo whose origin points at GitHub.

    The cross-repo PR sweep fans out over every known repo -- which on a real
    machine includes ``/opt/homebrew``, index caches and agent scratch dirs.
    Forking `gh` for those only to watch it fail is the "subprocess per row"
    shape this repo's perf gates exist to stop. Reads ``.git/config`` directly
    (no subprocess at all) and memoises on the file's mtime, so a newly added
    remote is picked up without a restart.

    Unreadable/missing config returns False: a repo we cannot prove is on
    GitHub is not worth a GraphQL point.
    """
    if not repo_path:
        return False
    cfg = os.path.join(str(repo_path), ".git", "config")
    try:
        mtime = os.path.getmtime(cfg)
    except OSError:
        # Worktrees keep a `.git` FILE pointing at the real admin dir; fall
        # back to letting the caller try rather than silently skipping them.
        return os.path.isfile(os.path.join(str(repo_path), ".git"))
    with _REMOTE_LOCK:
        hit = _REMOTE_CACHE.get(repo_path)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        with open(cfg, "r", errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = ""
    verdict = "github.com" in text
    with _REMOTE_LOCK:
        _REMOTE_CACHE[repo_path] = (mtime, verdict)
    return verdict


def _fetch_open_prs(repo_path, fields, timeout):
    try:
        out = _run_gh(
            ["pr", "list", "--state", "open", "--limit", str(pr_limit()),
             "--json", fields],
            cwd=repo_path, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return [], str(e)[:300]
    if out.returncode != 0:
        return [], (out.stderr or out.stdout or "gh failed").strip()[:300]
    try:
        data = json.loads(out.stdout or "[]")
    except ValueError:
        return [], "gh returned malformed JSON"
    return (data if isinstance(data, list) else []), None


def open_prs(repo_path, checks=True, timeout=12, ttl=None):
    """Open PRs for one repo, shared across every caller in this process.

    ``checks=False`` drops ``statusCheckRollup`` (1.0 pt instead of 2.9) and
    is cached separately, because a caller that wants checks must not be
    served a payload that lacks them.

    Returns ``(prs, error)``. ``error`` is None on success; on failure the
    last good list is returned alongside the error so a transient `gh` blip
    does not blank a panel.
    """
    if not repo_path:
        return [], None
    if not has_github_remote(repo_path):
        return [], None
    key = f"{repo_path}|{'full' if checks else 'light'}"
    ttl = pr_ttl_s() if ttl is None else ttl
    now = time.time()
    with _PR_LOCK:
        hit = _PR_CACHE.get(key)
        if hit and (now - hit["ts"]) < ttl:
            return list(hit["prs"]), hit["error"]

    fields = PR_JSON_FIELDS if checks else PR_JSON_FIELDS_LIGHT

    def _produce():
        prs, error = _fetch_open_prs(repo_path, fields, timeout)
        with _PR_LOCK:
            prior = _PR_CACHE.get(key)
            if error and prior:
                # Refresh the timestamp anyway: retrying a failing `gh` on
                # every request is how a rate-limit window becomes a storm.
                _PR_CACHE[key] = {"ts": time.time(), "prs": prior["prs"], "error": error}
                return list(prior["prs"]), error
            _PR_CACHE[key] = {"ts": time.time(), "prs": prs, "error": error}
        return prs, error

    value, was_leader = single_flight(key, _produce)
    if was_leader and value is not None:
        return list(value[0]), value[1]
    with _PR_LOCK:
        hit = _PR_CACHE.get(key)
    if hit:
        return list(hit["prs"]), hit["error"]
    return [], None


def bust_pr_cache(repo_path=None):
    """Drop cached PR lists after a mutation (merge, ready-for-review)."""
    with _PR_LOCK:
        if repo_path is None:
            _PR_CACHE.clear()
            return
        for key in [k for k in _PR_CACHE if k.startswith(f"{repo_path}|")]:
            _PR_CACHE.pop(key, None)


def pr_cache_stats():
    """Test/observability seam: how many repos are cached and how old."""
    now = time.time()
    with _PR_LOCK:
        return {
            "entries": len(_PR_CACHE),
            "ttl_s": pr_ttl_s(),
            "limit": pr_limit(),
            "ages_s": {k: round(now - v["ts"], 1) for k, v in _PR_CACHE.items()},
        }
