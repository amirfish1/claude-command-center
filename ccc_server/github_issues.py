# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 45282-48047).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# GitHub issues
# ---------------------------------------------------------------------------

# Rate-limit awareness: when GitHub's GraphQL quota is exhausted, back off
# aggressively and serve cached data rather than hammering the API.
_GH_RATE_LIMIT_LOCK = threading.Lock()
_GH_RATE_LIMIT_STATE = {
    "last_error_ts": 0.0,      # epoch of the most recent rate-limit error
    "last_error_reset": 0,     # GitHub's reset timestamp (seconds since epoch)
    "last_remaining": None,    # last known remaining quota
    "last_check_ts": 0.0,      # epoch of last gh api rate_limit check
}
_GH_RATE_LIMIT_ERROR_TTL = 300.0   # seconds to treat a rate-limit hit as active
_GH_RATE_LIMIT_CHECK_TTL = 30.0    # seconds to cache gh api rate_limit responses
_GH_RATE_LIMIT_LOW_THRESHOLD = 200   # GraphQL remaining below this is "low"


def _is_rate_limit_error(stderr):
    """True when gh stderr indicates a GitHub API rate-limit rejection."""
    if not stderr:
        return False
    text = str(stderr).lower()
    return "rate limit" in text or "api rate limit" in text


def _record_rate_limit_error(reset_epoch=None):
    """Mark that we just hit a rate limit. Optional reset_epoch from headers."""
    with _GH_RATE_LIMIT_LOCK:
        _GH_RATE_LIMIT_STATE["last_error_ts"] = time.time()
        if reset_epoch:
            try:
                _GH_RATE_LIMIT_STATE["last_error_reset"] = int(reset_epoch)
            except (TypeError, ValueError):
                pass


def github_rate_limited(refresh=False):
    """Public query: are we currently in a rate-limit backoff window?

    Returns a dict with enough detail for callers to decide whether to skip
    optional GitHub API work. The backoff is conservative: it lasts until the
    recorded reset time (if known) or a fixed TTL after the error.

    ``refresh=True`` updates the snapshot from ``gh api rate_limit`` (cached for
    30s) before returning the status. Optional polling loops can use this to
    back off before they spend quota.
    """
    if refresh:
        _check_gh_rate_limit()
    with _GH_RATE_LIMIT_LOCK:
        state = dict(_GH_RATE_LIMIT_STATE)
    now = time.time()
    if state["last_error_ts"] == 0.0:
        return {"rate_limited": False, "backoff_seconds": 0, "last_remaining": state["last_remaining"]}

    # Prefer GitHub's reset timestamp when we know it.
    reset_at = state["last_error_reset"]
    if reset_at and reset_at > now:
        backoff = int(reset_at - now) + 1
    else:
        backoff = max(0, int(_GH_RATE_LIMIT_ERROR_TTL - (now - state["last_error_ts"])))

    return {
        "rate_limited": backoff > 0,
        "backoff_seconds": backoff,
        "last_remaining": state["last_remaining"],
    }


def _check_gh_rate_limit():
    """Fetch GitHub's current rate-limit status via the REST API.

    This uses the REST quota (not GraphQL) and is cached for
    _GH_RATE_LIMIT_CHECK_TTL seconds. Updates the shared state so other
    modules can avoid optional GitHub API calls when quota is low.
    """
    now = time.time()
    with _GH_RATE_LIMIT_LOCK:
        if now - _GH_RATE_LIMIT_STATE["last_check_ts"] < _GH_RATE_LIMIT_CHECK_TTL:
            return
        _GH_RATE_LIMIT_STATE["last_check_ts"] = now

    try:
        result = subprocess.run(
            ["gh", "api", "rate_limit", "--jq", ".resources.graphql"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            remaining = data.get("remaining")
            reset = data.get("reset")
            with _GH_RATE_LIMIT_LOCK:
                _GH_RATE_LIMIT_STATE["last_remaining"] = remaining
                if isinstance(reset, (int, float)) and reset > time.time():
                    _GH_RATE_LIMIT_STATE["last_error_reset"] = int(reset)
            # Pre-emptively treat low remaining as a soft rate-limit window so
            # optional polling backs off before we actually hit the hard limit.
            if isinstance(remaining, int) and remaining < _GH_RATE_LIMIT_LOW_THRESHOLD:
                _record_rate_limit_error(reset)
    except Exception:
        pass


def _gh(repo_path, *args, timeout=10):
    """Run a gh CLI command and return parsed JSON or None."""
    repo_path = _core.resolve_repo_path(repo_path)
    try:
        result = subprocess.run(
            ["gh"] + list(args),
            capture_output=True, text=True, timeout=timeout, cwd=str(repo_path),
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        if _is_rate_limit_error(result.stderr):
            _record_rate_limit_error()
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return None


# Small TTL cache for list_issues() so dashboard polling doesn't multiply
# gh CLI calls. Stale data is returned during rate-limit backoff. Keyed by
# resolved repo path so switching repos doesn't serve the wrong cache.
_LIST_ISSUES_CACHE = {"by_repo": {}, "lock": threading.Lock()}
_LIST_ISSUES_TTL = 30.0
_LIST_ISSUES_RATE_LIMIT_TTL = 300.0


def github_issue_title(url):
    """Return the title for a canonical GitHub Issue URL, if it can be read."""
    match = re.match(r"^https?://github\.com/([^/\s]+)/([^/\s]+)/issues/(\d+)(?:[/?#].*)?$", str(url or "").strip())
    if not match:
        return ""
    # Skip optional API work when we know we are rate-limited.
    if github_rate_limited()["rate_limited"]:
        return ""
    owner, repo, number = match.groups()
    try:
        result = subprocess.run(
            ["gh", "issue", "view", number, "--repo", f"{owner}/{repo}", "--json", "title"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return str((json.loads(result.stdout or "{}")).get("title") or "").strip()
        if _is_rate_limit_error(result.stderr):
            _record_rate_limit_error()
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return ""


def list_issues(repo_path):
    """Return open issues + recently closed issues (last 24h).

    Results are cached for a short TTL so the dashboard's polling doesn't
    generate a storm of gh CLI calls. During a GitHub API rate-limit backoff
    the cached/stale result is returned (or an empty list if nothing cached).
    """
    repo_path = _core.resolve_repo_path(repo_path)
    cache_key = str(repo_path)
    now = time.time()
    with _LIST_ISSUES_CACHE["lock"]:
        rate_limited = github_rate_limited()["rate_limited"]
        ttl = _LIST_ISSUES_RATE_LIMIT_TTL if rate_limited else _LIST_ISSUES_TTL
        ent = _LIST_ISSUES_CACHE["by_repo"].get(cache_key)
        if ent and now - ent["ts"] < ttl:
            return list(ent["data"])

    # Opportunistically refresh the shared rate-limit snapshot before spending
    # GraphQL quota; if it shows we are low, the TTL check above will short-
    # circuit on the next call, and we will keep serving the cached copy.
    _check_gh_rate_limit()

    if github_rate_limited()["rate_limited"]:
        # Still rate-limited after the refresh above (the _LIST_ISSUES_RATE_
        # LIMIT_TTL stale window has lapsed but the backoff hasn't). Calling
        # gh here would almost certainly fail too, and _gh()'s `or []`
        # fallback can't tell "confirmed zero issues" from "couldn't check" —
        # treating a failed call as zero would silently empty out (and
        # cache-poison, since the write below is unconditional) a GitHub-
        # backed queue for good until the next successful fetch. Keep serving
        # whatever was last known, however stale, instead.
        with _LIST_ISSUES_CACHE["lock"]:
            ent = _LIST_ISSUES_CACHE["by_repo"].get(cache_key)
        return list(ent["data"]) if ent else []

    open_issues = _gh(
        repo_path,
        "issue", "list", "--state", "open", "--limit", "50",
        "--json", "number,title,labels,createdAt,updatedAt,state",
    ) or []

    # Recently closed (last day)
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    closed_issues = _gh(
        repo_path,
        "issue", "list", "--state", "closed", "--limit", "20",
        "--search", f"closed:>{since[:10]}",
        "--json", "number,title,labels,createdAt,updatedAt,closedAt,state",
    ) or []

    all_issues = []
    for issue in open_issues + closed_issues:
        labels = [l["name"] for l in issue.get("labels", [])]
        # Determine claude status
        if "claude-in-progress" in labels:
            claude_status = "in_progress"
        elif "claude-fix" in labels:
            claude_status = "queued"
        elif "claude-failed" in labels:
            claude_status = "failed"
        elif issue["state"] == "CLOSED":
            claude_status = "closed"
        else:
            claude_status = "open"
        all_issues.append({
            "number": issue["number"],
            "title": _core._strip_title_prefix(issue["title"]),
            "labels": labels,
            "state": issue["state"].lower(),
            "claude_status": claude_status,
            "has_log": False,
            "updated_at": issue.get("updatedAt", ""),
            "closed_at": issue.get("closedAt", ""),
        })

    # Sort: in_progress first, then queued, then open, then closed
    order = {"in_progress": 0, "queued": 1, "failed": 2, "open": 3, "closed": 4}
    all_issues.sort(key=lambda x: (order.get(x["claude_status"], 9), -x["number"]))

    with _LIST_ISSUES_CACHE["lock"]:
        _LIST_ISSUES_CACHE["by_repo"][cache_key] = {"ts": time.time(), "data": all_issues}
    return all_issues


def add_claude_fix_label(issue_number, repo_path):
    """Add 'claude-fix' label to an issue."""
    repo_path = _core.resolve_repo_path(repo_path)
    try:
        result = subprocess.run(
            ["gh", "issue", "edit", str(issue_number), "--add-label", "claude-fix"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        if result.returncode == 0:
            return {"ok": True}
        return {"error": result.stderr.strip() or "Failed to add label"}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"error": str(e)}


def spawn_issue_fix(issue_number, repo_path):
    """Spawn a headless Claude session to fix an issue directly (no worktree)."""
    repo_path = _core.resolve_repo_path(repo_path)
    issue_number = str(issue_number)
    try:
        result = subprocess.run(
            ["gh", "issue", "view", issue_number, "--json", "title,body"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        if result.returncode != 0:
            return {"error": f"Failed to fetch issue #{issue_number}: {result.stderr.strip()}"}
        issue_data = json.loads(result.stdout)
        title = issue_data.get("title", "")
        body = issue_data.get("body", "")
    except Exception as e:
        return {"error": f"Failed to fetch issue: {e}"}

    claude_bin = _core._resolve_claude_bin()
    if not claude_bin.get("available"):
        return {
            "ok": False,
            "error": claude_bin.get("reason") or "Claude Code CLI not found",
            "code": claude_bin.get("code", "claude_unavailable"),
        }

    # Mark as in-progress
    subprocess.run(
        ["gh", "issue", "edit", issue_number, "--add-label", "claude-in-progress", "--remove-label", "claude-fix"],
        capture_output=True, text=True, timeout=10, cwd=str(repo_path),
    )

    prompt = f"""You are fixing GitHub issue #{issue_number}.

**Title:** {title}

**Description:**
{body}

Instructions:
- Read and follow the project CLAUDE.md for coding standards.
- Make the minimal changes needed to fix this issue.
- Commit your changes with a descriptive message referencing the issue (e.g. Fix #{issue_number}: ...).
- Push the branch and create a PR that closes #{issue_number}.
- You are working directly in the repo root — NOT in a worktree."""

    session_name = f"issue-{issue_number}"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-issue-{issue_number}-{timestamp}.log"
    log_dir = _core.repo_log_dir(repo_path)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    cmd = [
        claude_bin["bin"], "-p", "--verbose",
        "--output-format", "stream-json",
        "--model", "claude-opus-4-6",
        "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash",
        "--dangerously-skip-permissions",
        "--name", session_name,
        *_core._claude_session_state_args(),
        prompt,
    ]

    # The log file is kept (not consumed by the UI any more — Claude writes
    # its own jsonl under ~/.claude/projects/, which surfaces as the
    # interactive session card) but `_reattach_spawned_orphans` still reads
    # it via `extract_session_id` to backfill the session id when the
    # in-memory spawn registry is wiped on a restart.
    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(repo_path),
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {
            "ok": False,
            "error": f"Claude Code CLI failed to start: {e}",
            "code": "claude_unavailable",
        }

    entry = {
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=str(repo_path),
        spawned_at=timestamp,
        command_summary=prompt[:200],
    )

    return {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path)}


_VERCEL_PROJECT_ENV = os.environ.get("VERCEL_PROJECT", "")


def _detect_vercel_project(repo_path):
    """Read <repo>/.vercel/project.json (created by `vercel link`) and
    return its `projectName`. Returns "" when the file is absent or
    malformed."""
    candidate = Path(repo_path) / ".vercel" / "project.json"
    try:
        with open(candidate, "r") as f:
            data = json.load(f)
        name = (data or {}).get("projectName") or ""
        return name if isinstance(name, str) else ""
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return ""


def _resolve_vercel_project(repo_path=None):
    """env > .vercel/project.json > "". Env still wins so CI overrides keep
    working; the autodetect is just a friendlier default for the common case
    of a `vercel link`-ed local checkout."""
    return _VERCEL_PROJECT_ENV or (_detect_vercel_project(repo_path) if repo_path else "")


def vercel_deploy_status(repo_path):
    """Return latest production deployment status from Vercel CLI.

    No-op when no project name is resolvable — Vercel integration is opt-in.
    """
    repo_path = _core.resolve_repo_path(repo_path)
    project = _core._resolve_vercel_project(repo_path)
    if not project:
        return {"error": "VERCEL_PROJECT not configured (no env, no .vercel/project.json)", "disabled": True}
    try:
        result = subprocess.run(
            ["vercel", "ls", project, "--environment", "production", "-F", "json"],
            capture_output=True, text=True, timeout=15, cwd=str(repo_path),
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or "vercel ls failed"}

        data = json.loads(result.stdout)
        deployments = data.get("deployments", [])
        if not deployments:
            return {"error": "No deployments found"}

        d = deployments[0]
        created = d.get("createdAt", 0)
        ready = d.get("ready", 0)
        meta = d.get("meta", {})

        return {
            "state": d.get("state", "UNKNOWN"),
            "url": d.get("url", ""),
            "created_at": created,
            "ready_at": ready,
            "duration_s": round((ready - created) / 1000) if ready and created else None,
            "commit_sha": meta.get("githubCommitSha", "")[:7],
            "commit_msg": (meta.get("githubCommitMessage", "") or "").split("\n")[0][:80],
            "commit_ref": meta.get("githubCommitRef", ""),
            "project": project,
        }
    except subprocess.TimeoutExpired:
        return {"error": "vercel CLI timed out"}
    except (json.JSONDecodeError, FileNotFoundError) as e:
        return {"error": str(e)}


def _load_fix_deploy_spawned():
    if not _core.FIX_DEPLOY_SPAWNED_FILE.exists():
        return {}
    try:
        return json.loads(_core.FIX_DEPLOY_SPAWNED_FILE.read_text())
    except Exception:
        return {}


def _save_fix_deploy_spawned(data):
    _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _core.FIX_DEPLOY_SPAWNED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, _core.FIX_DEPLOY_SPAWNED_FILE)


def vercel_deploy_status_with_autofix(repo_path):
    """Return deploy status; auto-spawn /fix-deploy session on new ERROR."""
    repo_path = _core.resolve_repo_path(repo_path)
    status = vercel_deploy_status(repo_path)
    if status.get("state") == "ERROR":
        sha = status.get("commit_sha") or ""
        if sha:
            spawned = _load_fix_deploy_spawned()
            if sha not in spawned:
                try:
                    info = _core.spawn_session("/fix-deploy", name=f"fix-deploy-{sha}", repo_path=repo_path)
                    spawned[sha] = {
                        "pid": info.get("pid"),
                        "name": info.get("name"),
                        "spawned_at": time.time(),
                        "commit_msg": status.get("commit_msg", ""),
                    }
                    _save_fix_deploy_spawned(spawned)
                    status["auto_fix_spawned"] = spawned[sha]
                except Exception as e:
                    status["auto_fix_error"] = str(e)
            else:
                status["auto_fix_spawned"] = spawned[sha]
    return status


# ── Repo "Push all" ship flow ────────────────────────────────────────────────
# The conversation-list folder header exposes a "Push all" control that
# orchestrates a repo-wide ship for that repo's main checkout:
#
#   nudging → waiting_commits → pulling → pushing → pushed → deploying → deployed
#
# Step 1 asks the sessions that likely own uncommitted work to commit (Tier-A
# nudge, via the same fall-through as /api/inject-input — works on live AND
# dormant Claude/Codex/Gemini/Cursor). Step 2 polls `git status --porcelain`
# until the worktree goes clean (capped). Then pull --rebase + push, persist
# last_ship_at/sha, and — when the repo is a Vercel project — poll the
# production deploy until it goes READY.
#
# The flow runs in a daemon thread per repo; the UI starts it via
# POST /api/repo/ship and polls GET /api/repo/ship/status for the live phase.
# Best-effort by design: it never force-pushes and it nudges sessions rather
# than committing on their behalf (hunk-level attribution is out of scope).

REPO_SHIP_STATE_FILE = _core.COMMAND_CENTER_STATE_DIR / "repo-ship-state.json"
SHIP_JOBS_FILE = _core.COMMAND_CENTER_STATE_DIR / "ship-jobs.json"  # last job+log per repo (survives refresh/restart)

SHIP_COMMIT_WAIT_CAP = 180   # max seconds to wait for sessions to commit
SHIP_POLL_INTERVAL = 4       # seconds between porcelain polls
SHIP_DEPLOY_WAIT_CAP = 180   # max seconds to wait for Vercel production READY
SHIP_DEPLOY_POLL_INTERVAL = 6
SHIP_ACK_TTL = 86400         # 24h safety backstop; real skip key is the dirt fingerprint
SHIP_ACKS_FILE = _core.COMMAND_CENTER_STATE_DIR / "ship-acks.json"


def _ship_session_write_sig(sid):
    """Per-session 'have you written anything' signature: mtime of the session's
    `<sid>_writes` sidecar marker (bumped on every Edit/Write). A session that
    said 'nothing of mine' is only re-asked when ITS OWN signature advances —
    so another session's edits never re-trigger a nudge to this one."""
    try:
        return str(int((_core.SIDECAR_STATE_DIR / f"{sid}_writes").stat().st_mtime))
    except (OSError, ValueError):
        return "0"


def _load_ship_acks():
    """{session_id: {ts, sig}} of recent replies, keyed by the session's own
    write signature. Persisted so 'Push all' skips already-answered sessions
    across refresh/restart."""
    try:
        data = json.loads(SHIP_ACKS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    now, out = time.time(), {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, dict) and (now - v.get("ts", 0)) < SHIP_ACK_TTL:
            out[k] = {"ts": float(v.get("ts", 0)), "sig": str(v.get("sig", ""))}
    return out


def _save_ship_acks(acks):
    try:
        SHIP_ACKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SHIP_ACKS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(acks, indent=2))
        os.replace(tmp, SHIP_ACKS_FILE)
    except OSError:
        pass


# session_id -> {ts, sig} of last reply (see above). Loaded from disk.
_ship_recent_acks = _load_ship_acks()


def _record_ship_ack(sid):
    _ship_recent_acks[sid] = {"ts": time.time(), "sig": _ship_session_write_sig(sid)}
    _save_ship_acks(_ship_recent_acks)


def _ship_already_answered(sid):
    """True if this session already answered AND hasn't written anything since."""
    rec = _ship_recent_acks.get(sid)
    if not rec or (time.time() - rec.get("ts", 0)) >= SHIP_ACK_TTL:
        return False
    return rec.get("sig") == _ship_session_write_sig(sid)


def _ship_session_label(sid, names):
    """Human name for a session: user override → JSONL-derived title → short id."""
    n = (names.get(sid) or "").strip()
    if n:
        return n
    try:
        p = _core._find_session_jsonl(sid)
        if p:
            meta = _core._conv_meta_cache.get(str(p)) or {}
            for k in ("custom_title", "ai_title", "last_prompt"):
                v = (meta.get(k) or "").strip()
                if v:
                    return v[:60]
    except Exception:
        pass
    return sid[:8]

TIER_A_COMMIT_NUDGE = (
    "Push-all in progress for this repo. Commit ONLY the paths you changed: "
    "`git commit --only <paths> -m 'type(area): subject'` "
    "(never `git add -A` / `git add .` / `git commit -a` — that sweeps in "
    "sibling sessions' work). When you're done — committed, or nothing of "
    "yours to commit — reply DONE."
)

_ship_jobs = {}                       # repo_path -> live job dict
_ship_jobs_lock = threading.Lock()


def _load_repo_ship_state():
    try:
        data = json.loads(REPO_SHIP_STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_repo_ship_state(state):
    REPO_SHIP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPO_SHIP_STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, REPO_SHIP_STATE_FILE)


def _record_repo_ship(repo_path, sha, branch):
    state = _core._load_repo_ship_state()
    state[repo_path] = {
        "last_ship_at": time.time(),
        "last_ship_sha": (sha or "")[:12],
        "last_ship_branch": branch,
    }
    _save_repo_ship_state(state)


def _persist_ship_job(repo_path, job):
    """Save a finished job (+log) to disk so the UI can reopen it after a page
    refresh or a server restart. Log capped to keep the file small."""
    try:
        data = json.loads(SHIP_JOBS_FILE.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    j = dict(job)
    if isinstance(j.get("log"), list) and len(j["log"]) > 120:
        j["log"] = j["log"][-120:]
    data[repo_path] = j
    SHIP_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SHIP_JOBS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, SHIP_JOBS_FILE)


def _load_ship_job(repo_path):
    """Last persisted job for a repo, or None."""
    try:
        data = json.loads(SHIP_JOBS_FILE.read_text())
        return data.get(repo_path) if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _ship_candidate_sessions(repo_path):
    """Live session ids whose cwd is inside this repo.

    Deliberately built from the CHEAP live-id set (`_discover_live_session_ids`
    — Claude registry + engine resume ids + sidecar `*_writes` markers), NOT
    the full `find_all_sessions` archive scan. That scan does git/gh probes per
    session and, run from this background thread, took >60s on a big monorepo
    and held the sessions singleflight, hanging the sidebar too. Live sessions
    in a dirty repo are exactly who owns the uncommitted work; asking one that
    has nothing to commit is harmless (it just replies DONE).

    Sessions living in a NESTED git worktree (e.g. `<repo>/.claude/worktrees/*`)
    are excluded: they commit to their own feature branch and can never clean
    the main checkout, so nudging them is pure noise. Sibling worktrees
    (`<repo>-wt-*`) already fall outside repo_root and never match.
    """
    try:
        live_ids = _core._discover_live_session_ids()
    except Exception:
        live_ids = set()
    try:
        repo_root = str(Path(repo_path).resolve())
    except (OSError, ValueError):
        repo_root = repo_path
    seen, out = set(), []
    for sid in live_ids:
        if not sid or sid.startswith("backlog-") or sid in seen:
            continue
        try:
            cwd = _core.find_session_cwd(sid)
        except Exception:
            cwd = None
        if not cwd:
            continue
        try:
            cwd_res = str(Path(cwd).resolve())
        except (OSError, ValueError):
            cwd_res = cwd
        if not (cwd_res == repo_root or cwd_res.startswith(repo_root + os.sep)):
            continue
        # A cwd deeper than repo_root might be a nested worktree, not the main
        # checkout. Confirm its git top-level IS repo_root before nudging.
        if cwd_res != repo_root:
            rc, top, _ = _core._git(["rev-parse", "--show-toplevel"], cwd_res, timeout=3)
            if rc == 0:
                try:
                    top_res = str(Path(top.strip()).resolve())
                except (OSError, ValueError):
                    top_res = top.strip()
                if top_res != repo_root:
                    continue
        seen.add(sid)
        out.append(sid)
    return out


def _append_ship_log(job, text, level):
    """Append one terminal-style line to a job. Caller holds _ship_jobs_lock."""
    log = job.setdefault("log", [])
    log.append({"t": time.time(), "text": text, "level": level})
    if len(log) > 200:
        del log[: len(log) - 200]


def _ship_waiting_on_from_acks(acks):
    """Small serializable waiting list for the Push all panel."""
    out = []
    for a in acks or []:
        status = "done" if a.get("acked") else ("pending" if a.get("jsonl") is not None else "sent")
        out.append({
            "sid": a.get("sid") or "",
            "name": a.get("name") or str(a.get("sid") or "")[:8],
            "status": status,
        })
    return out


def _ship_log(repo_path, text, level="info"):
    """Append a standalone log line (no phase change)."""
    with _core._ship_jobs_lock:
        job = _core._ship_jobs.get(repo_path)
        if job is not None:
            _append_ship_log(job, text, level)


def _ship_update(repo_path, phase, message, *, running=True, log=True, level="info", **extra):
    with _core._ship_jobs_lock:
        job = _core._ship_jobs.setdefault(repo_path, {})
        job["phase"] = phase
        job["message"] = message
        job["running"] = running
        job["updated_at"] = time.time()
        if not running:
            job["finished_at"] = time.time()
        job.update(extra)
        if log and message:
            lvl = level
            if level == "info" and phase in ("error", "deploy_error"):
                lvl = "error"
            elif level == "info" and phase in ("needs_you", "diverged"):
                lvl = "warn"   # handoff, not a failure
            elif level == "info" and phase in ("pushed", "deployed"):
                lvl = "ok"
            _append_ship_log(job, message, lvl)
        snapshot = dict(job)
    # Persist terminal jobs so the log survives a page refresh / restart.
    if not running:
        try:
            _persist_ship_job(repo_path, snapshot)
        except OSError:
            pass
    return snapshot


def _ship_read_reply(jsonl_path, start_offset, since_epoch):
    """Return assistant text written to a transcript after the nudge, or None.

    Closes the loop on the otherwise one-way nudge: we snapshot the transcript
    size at inject time, then read forward for the session's assistant reply
    (e.g. "DONE — nothing of mine is uncommitted"). Claude transcripts only —
    other engines return None (their reply isn't tracked, only git status is).
    """
    if not jsonl_path:
        return None
    try:
        with open(jsonl_path, "rb") as fh:
            if start_offset:
                fh.seek(start_offset)
            data = fh.read()
    except OSError:
        return None
    out = []
    for line in data.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(ev, dict) or ev.get("type") != "assistant":
            continue
        ts = _core._iso_to_epoch(ev.get("timestamp"))
        if ts is not None and ts < since_epoch:
            continue
        msg = ev.get("message") or {}
        for block in (msg.get("content") or []):
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text") or ""
                if t.strip():
                    out.append(t)
    return ("".join(out)).strip() or None


_SHIP_JUNK_MARKERS = (
    "__pycache__/", ".claude/annotations/", ".DS_Store",
    # Annotation screenshots and pasted images captured by the CCC annotation
    # tool. These live under .claude/command-center/ and are input data for the
    # UX-fixes queue — they're never repo assets.
    ".claude/command-center/annotation-screenshots/",
    ".claude/command-center/pasted-images/",
    # Editor / tool local state — always gitignore material, never committed.
    # Matched as substrings because they live anywhere in the tree (e.g.
    # apps/x/.obsidian/). Without them, this cruft falls through to
    # _ship_classify_remaining, gets mislabeled "app/deploy review" by the
    # apps/ prefix, and parks Push all every time on pure junk.
    ".obsidian/", ".idea/",
)
# Top-level cruft dirs/files, matched by prefix so a legit src/cache/ deeper
# in the tree is never swept (e.g. repo-root cache/projects.json → junk).
# .wt-* / .wk-* are worktree-scoped temp files dropped at the repo root by
# Claude worktree workflows (snapshots, inline answers, etc.) — never assets.
_SHIP_JUNK_PREFIXES = ("cache/", ".wt-", ".wk-")

# Media extensions that are always junk when they appear under .claude/ —
# screenshots, pasted images, captures. They are not repo assets.
_SHIP_CLAUDE_MEDIA_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov")


def _ship_is_junk(path):
    """Untracked cruft that should be gitignored, never committed."""
    # Any image/media file anywhere under .claude/ is a screenshot or capture,
    # not a repo asset (CLAUDE.md, rules/*.md etc. are text and not affected).
    if path.startswith(".claude/") and path.lower().endswith(_SHIP_CLAUDE_MEDIA_EXTS):
        return True
    return (
        path.endswith((".pyc", ".DS_Store"))
        or path.startswith(_SHIP_JUNK_PREFIXES)
        or any(m in path for m in _SHIP_JUNK_MARKERS)
    )


def _ship_porcelain_paths(repo_path):
    """[(xy, path)] from `git status --porcelain`, or None on failure."""
    rc, out, _ = _core._git(["status", "--porcelain"], repo_path)
    if rc != 0:
        return None
    paths = []
    for line in out.splitlines():
        if not line.strip():
            continue
        xy, p = line[:2], line[3:]
        if " -> " in p:          # rename: keep the destination path
            p = p.split(" -> ")[-1]
        paths.append((xy, p))
    return paths


def _ship_safe_restore_ref(repo_path, path):
    """If `path`'s current working+staged content is byte-identical to a commit
    that exists on SOME ref, return a short ref label; else None.

    When this returns truthy, `git restore` is provably non-destructive — the
    exact content already lives in a committed object (origin/main, a feature
    branch, etc.), so discarding the working copy loses nothing. This is how
    hand-copied dirt ("pulled PR #336 into the shared clone") gets auto-cleared.
    """
    rc, sha, _ = _core._git(["log", "-1", "--all", "--format=%H", "--", path], repo_path, timeout=8)
    sha = (sha or "").strip()
    if rc != 0 or not sha:
        return None  # untracked-everywhere / no history → not safe to restore
    rc, diff, _ = _core._git(["diff", sha, "--", path], repo_path, timeout=8)
    if rc != 0 or diff.strip():
        return None  # differs from that commit → real divergent work, keep it
    rc, brs, _ = _core._git(["branch", "-a", "--contains", sha], repo_path, timeout=8)
    ref = None
    for b in (brs or "").splitlines():
        b = b.strip().lstrip("*+ ").strip()
        if not b or "HEAD" in b:
            continue
        if "origin/main" in b or b == "main":
            return "origin/main"
        ref = ref or b
    return ref or sha[:8]


def _ship_triage_restore(repo_path):
    """Auto-resolve dirt whose content is already preserved in git.

    Restores files byte-identical to a committed version on any ref (provably
    safe). Returns {restored: [(path, ref)], junk: [path], remaining: [path]}.
    Never deletes untracked files and never commits.
    """
    restored, junk, remaining = [], [], []
    for xy, p in (_ship_porcelain_paths(repo_path) or []):
        if _core._ship_is_junk(p):
            junk.append(p)
            continue
        ref = _ship_safe_restore_ref(repo_path, p)
        if ref:
            rc, _, _ = _core._git(["restore", "--staged", "--worktree", "--", p], repo_path, timeout=10)
            if rc == 0:
                restored.append((p, ref))
                continue
        remaining.append(p)
    return {"restored": restored, "junk": junk, "remaining": remaining}


# Loose scratch that lands at the repo root and is plainly NOT deployable code:
# media/screenshot output, archives, scratch data dumps. The motivating case is a
# Puppeteer snapshot script + its PNG (snapshot.js / snapshot.png) dropped at the
# top of the tree — those are one-off dev scratch, not prod/deploy code, yet the
# old default labeled them "review" and parked Push all on pure noise. We only
# loosen the ROOT-LEVEL default (no '/' in the path); anything under a source
# directory (apps/, packages/, …) keeps the conservative "review" verdict.
_SHIP_SCRATCH_EXTS = (
    # media / screenshot output
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".mp4", ".mov", ".webm", ".pdf",
    # scratch data dumps / archives
    ".log", ".tmp", ".bak", ".csv", ".zip", ".tar", ".gz",
)
# Obvious scratch-script name stems (a top-level `snapshot.js`, `scratch.py`,
# `tmp.ts`, etc.). These are dev one-offs when they sit loose at the root, never
# part of the build. Anything matching a real prod-config basename is excluded
# below before we get here, so a root `next.config.js` still routes to "review".
_SHIP_SCRATCH_STEMS = ("snapshot", "scratch", "tmp", "temp", "test", "debug", "scrap", "playground")


def _ship_classify_remaining(path):
    """'infra' = safe to bulk-commit (docs/config/scripts, near-zero blast
    radius). 'review' = app/deploy code that ships to prod on push — eyeball
    the diff first.

    Default for unknown paths is 'review' (conservative) — EXCEPT for loose
    repo-root scratch (see _SHIP_SCRATCH_* above): a root-level media/archive
    file or an obvious scratch script is low blast radius and shouldn't park
    Push all as if it were deploy code."""
    if path.startswith(("apps/", "packages/")):
        return "review"
    base = path.rsplit("/", 1)[-1]
    # Known prod config basenames stay "review" wherever they sit, incl. root.
    if base in ("next.config.js", "vercel.json", "package.json", "package-lock.json"):
        return "review"
    if path.startswith(("docs/", ".claude/", "scripts/")):
        return "infra"
    if base in (".gitignore", "CLAUDE.md", "AGENTS.md", "README.md") or base.endswith(".md"):
        return "infra"
    # Repo-root-only loosening: a file with no '/' in its path is at the top of
    # the tree, where deploy code rarely lives loose. If it's media/archive
    # output or an obvious scratch script, treat as 'infra' (low blast radius)
    # instead of parking Push all. e.g. snapshot.js / snapshot.png.
    if "/" not in path:
        lower = base.lower()
        stem = lower.rsplit(".", 1)[0]
        if lower.endswith(_SHIP_SCRATCH_EXTS):
            return "infra"
        if stem in _SHIP_SCRATCH_STEMS or any(
            stem.startswith(s) for s in _SHIP_SCRATCH_STEMS
        ):
            return "infra"
    return "review"


def _ship_commit_scope(path):
    """Best-effort conventional-commit scope from a path."""
    if path.startswith("apps/bym-ios"):
        return "ios"
    if path.startswith("apps/bookyourmat"):
        return "bym"
    if path.startswith("packages/"):
        parts = path.split("/")
        return parts[1] if len(parts) > 1 else "pkg"
    top = path.split("/")[0]
    return top or "repo"


def _ship_age_human(ts_unix):
    """Compact relative age ('3h', '2d', '5m', 'just now') for a unix ts.

    Used only for human-facing log/owner labels, so coarse buckets are fine —
    we never compute anything off this string."""
    try:
        delta = max(0.0, time.time() - float(ts_unix))
    except (TypeError, ValueError):
        return "?"
    if delta < 90:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"


def _ship_index_attribution(repo_path, path, since="21d", _cache=None):
    """Best-effort: who last touched this file, from the local conversation
    index (~/.claude-index/index.db via the in-process reader).

    Returns {session_id, branch, ts_unix, age, owner} for the most-recent
    non-current-session match, or None.

    PRINCIPLE — the index AUGMENTS, never overrides, git's safety call. This is
    a soft attribution/liveness signal mined from chat history, not a fact about
    the repo's object graph. It can be stale, wrong, or missing entirely (no
    index built yet). So every failure mode — missing index, query error, any
    exception — collapses silently to None and the caller falls back to
    git-only. A bad index lookup must never be load-bearing.

    Runs inside the ship daemon thread, so it must also be cheap and
    non-hanging: a single lexical (semantic=False) FTS lookup keyed on the
    file's basename, scoped to this repo's cwd, bounded to recent history.
    `_cache` (a dict) memoises per ship-run so the same path isn't re-queried.
    """
    if _cache is not None and path in _cache:
        return _cache[path]

    def _remember(val):
        if _cache is not None:
            _cache[path] = val
        return val

    try:
        base = path.rstrip("/").rsplit("/", 1)[-1]
        if not base:
            return _remember(None)
        # Repo basename is a cheaper, more-portable cwd filter than the full
        # absolute path (sessions may live in sibling worktrees / symlinks).
        try:
            repo_name = Path(repo_path).name or repo_path
        except (OSError, ValueError):
            repo_name = repo_path
        res = _core.search_conversation_history(
            query=base, limit=8, cwd_like=repo_name,
            since=since, semantic=False,
        )
        # {"error": ...} (index missing / FTS rejected the query) → no attribution.
        if not isinstance(res, dict) or res.get("error"):
            return _remember(None)
        rows = res.get("results") or []
        if not rows:
            return _remember(None)
        # The current CCC session shouldn't attribute work to itself.
        cur = globals().get("CCC_SESSION_ID") or os.environ.get("CCC_SESSION_ID") or ""
        best = None
        for r in rows:
            try:
                sid = r.get("session_id")
                if not sid or sid == cur:
                    continue
                ts = float(r.get("ts_unix") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if best is None or ts > best[0]:
                best = (ts, sid, r.get("git_branch"))
        if best is None:
            return _remember(None)
        ts, sid, branch = best
        try:
            names = _core._load_session_name_overrides()
        except Exception:
            names = {}
        try:
            owner = _ship_session_label(sid, names)
        except Exception:
            owner = (sid or "")[:8]
        return _remember({
            "session_id": sid,
            "branch": branch,
            "ts_unix": ts,
            "age": _ship_age_human(ts),
            "owner": owner,
        })
    except Exception:
        # Any unexpected failure → git-only. The index is never load-bearing.
        return _remember(None)


def _ship_review_verdict(repo_path, path, _attr_cache=None):
    """Deterministic per-file verdict from the git graph (no LLM, no spawn):

      restore — working copy is byte-identical to a committed version
      leave   — its base lives on an UNMERGED branch/PR (committing here would
                duplicate it and conflict when that PR merges)
      commit  — genuine main-only work (latest commit touching it is in main,
                or it's a brand-new file on no branch)

    The git graph is the SAFETY AUTHORITY: `verdict` is decided purely from it.
    The conversation index (_ship_index_attribution) only layers an
    attribution/liveness hint on top — it may add an optional `owner`/`owner_age`
    and enrich the human-readable `why`, but it NEVER changes `verdict`. (git
    can't see that a feature branch is dead; the index can hint it — but a wrong
    index call must not be able to flip a safety call.)
    """
    # Attribution is computed once and only consulted to ENRICH the result —
    # never to choose `verdict`. Defensive by construction: returns None on any
    # failure, so the verdict logic below is identical with or without an index.
    attr = _core._ship_index_attribution(repo_path, path, _cache=_attr_cache)

    def _with_owner(v):
        # Attach the authoring session (by name) + its age when the index knew.
        # `restore` verdicts skip this: the file is byte-identical to a commit,
        # so there's no live work to hand back to anyone.
        if attr and v.get("verdict") != "restore":
            v["owner"] = attr["owner"]
            v["owner_age"] = attr["age"]
        return v

    rc, sha, _ = _core._git(["log", "-1", "--all", "--format=%H", "--", path], repo_path, timeout=8)
    sha = (sha or "").strip()
    if rc != 0 or not sha:
        return _with_owner({"verdict": "commit", "why": "new file - not on any branch"})
    rc2, diff, _ = _core._git(["diff", sha, "--", path], repo_path, timeout=8)
    if rc2 == 0 and not diff.strip():
        ref = _ship_safe_restore_ref(repo_path, path) or sha[:8]
        return {"verdict": "restore", "why": f"identical to committed {ref} - safe to discard"}
    in_main = _core._git(["merge-base", "--is-ancestor", sha, "HEAD"], repo_path, timeout=8)[0] == 0
    if not in_main:
        ref = None
        rc3, brs, _ = _core._git(["branch", "-a", "--contains", sha], repo_path, timeout=8)
        for b in (brs or "").splitlines():
            b = b.strip().lstrip("*+ ").strip()
            if b and "HEAD" not in b and "main" not in b:
                ref = b
                break
        why = f"base on unmerged {ref or sha[:8]} - let that branch/PR land it"
        # Liveness caveat: git proves the base is on an unmerged branch, but it
        # CANNOT see whether that branch is still alive or quietly abandoned.
        # If the index shows its author hasn't touched the file recently, flag
        # it so the human verifies before trusting the PR will land it. We still
        # say `leave` — the index can only add a "verify" nudge, not flip a
        # safety verdict, because a stale/wrong index hint would otherwise risk
        # duplicating a PR's work into main.
        if attr:
            why += f" (owned by {attr['owner']}, last active {attr['age']} ago — verify before relying on PR)"
        return _with_owner({"verdict": "leave", "why": why})
    return _with_owner({"verdict": "commit", "why": f"main-only edit since {sha[:8]}"})


def _ship_post_push_deploy(repo_path, branch, sha):
    """After a successful push, record the ship and (if the repo is wired to
    Vercel) poll the production deploy to a terminal state. Ends with a terminal
    _ship_update on every path.

    Factored out of _ship_integrate so the diverged-reconcile path can reuse the
    exact same deploy-poll tail after it pushes from the isolated worktree —
    there is one source of truth for "what happens after origin/{branch} moves."
    """
    _record_repo_ship(repo_path, sha, branch)

    if not _core._resolve_vercel_project(repo_path):
        _ship_update(repo_path, "pushed", f"Pushed {sha[:7]} to {branch}.",
                     running=False, sha=sha[:12])
        return

    _ship_update(repo_path, "deploying",
                 "Pushed. Waiting for Vercel production deploy…", sha=sha[:12])
    target = sha[:7] if sha else ""
    last_state = None
    deadline = time.time() + SHIP_DEPLOY_WAIT_CAP
    while time.time() < deadline:
        status = vercel_deploy_status(repo_path)
        state = (status.get("state") or "").upper()
        is_ours = (not target) or status.get("commit_sha") == target
        if state and state != last_state:
            tag = "" if is_ours else " (prev deploy)"
            _ship_log(repo_path, f"vercel: {state}{tag}", "info")
            last_state = state
        if state == "READY" and is_ours:
            _ship_update(repo_path, "deployed", "Successfully deployed.",
                         running=False, sha=sha[:12], deploy=status)
            return
        if state == "ERROR" and is_ours:
            _ship_update(repo_path, "deploy_error",
                         "Pushed, but Vercel deploy failed.",
                         running=False, sha=sha[:12], deploy=status)
            return
        time.sleep(SHIP_DEPLOY_POLL_INTERVAL)
    _ship_update(repo_path, "pushed",
                 "Pushed. Deploy still building — check Vercel.",
                 running=False, sha=sha[:12])


def _ship_diverged_handoff(repo_path, branch, ahead, behind):
    """Terminal 'diverged' hand-off: tell the user to reconcile manually from a
    clean worktree. This is the fallback whenever auto-reconcile can't safely
    finish (real conflict, rejected push, unexpected error). Centralized so the
    auto-reconcile path and the original guard emit identical guidance."""
    _ship_update(repo_path, "diverged",
                 f"Diverged: ahead {ahead}, behind {behind}. Refusing to rebase "
                 "a shared clone in place — a mid-rebase conflict would break "
                 "every session here. Reconcile from a clean worktree off "
                 f"origin/{branch} (cherry-pick the {ahead} local commit(s)), then push.",
                 running=False)


def _ship_reconcile_diverged(repo_path, branch, ahead, behind):
    """Auto-reconcile a diverged branch WITHOUT touching the shared clone's
    working tree or rewriting its history in place.

    Strategy (every conflict-prone step happens in a disposable worktree):
      1. Snapshot the shared clone's HEAD sha NOW, before anything moves.
      2. `git worktree add --detach <tmpdir> origin/{branch}` under the system
         temp dir (NEVER inside the repo) — a throwaway checkout at the remote
         tip. The shared clone stays on `main`, untouched.
      3. Replay the local-only commits (origin/{branch}..HEAD) onto that tip via
         cherry-pick, in order.
      4. CONFLICT → abort, tear down the worktree, fall back to the manual
         hand-off. We never auto-resolve conflicts; a human reconciles those.
      5. Clean replay → push the reconciled history from the worktree to
         origin/{branch}. Rejected push (someone else pushed meanwhile) → tear
         down, fall back to hand-off and let the next run retry.
      6. Push OK → fast-forward the shared clone to the new origin/{branch}. The
         shared HEAD is now an ancestor of the reconciled tip, so the ff is safe
         and rewrites nothing. If the ff somehow fails we still report success
         (origin moved — that's what "ship" means).
      7. finally: remove the temp worktree and best-effort delete the temp ref,
         so a worktree is never leaked even on an exception.

    SAFETY: the shared clone is only ever read (rev-parse) and, at the very end,
    fast-forwarded — its working tree is never modified mid-flight and its
    history is never rewritten in place. ALL the risky work lives in the
    disposable worktree, and on ANY doubt (conflict, rejected push, error) we
    fall back to the manual hand-off rather than risk corrupting a shared clone.
    Honors this repo's CLAUDE.md git hygiene (never branch/mutate the shared
    clone; the worktree is the isolation boundary)."""
    # Snapshot the local tip BEFORE anything moves — this is the set of commits
    # we must replay. Reading HEAD never mutates the shared clone.
    rc, head_sha, _ = _core._git(["rev-parse", "HEAD"], repo_path)
    head_sha = (head_sha or "").strip()
    if rc != 0 or not head_sha:
        _ship_log(repo_path, "could not read HEAD - handing off", "warn")
        _ship_diverged_handoff(repo_path, branch, ahead, behind)
        return

    # The exact local-only commits to replay, oldest-first.
    rc, rl, _ = _core._git(["rev-list", "--reverse", f"origin/{branch}..{head_sha}"], repo_path)
    commits = [c.strip() for c in (rl or "").splitlines() if c.strip()] if rc == 0 else []
    if rc != 0 or not commits:
        # Nothing local to replay (or we couldn't compute it) — not a case we can
        # safely auto-reconcile; hand off.
        _ship_log(repo_path, "could not compute local commits to replay - handing off", "warn")
        _ship_diverged_handoff(repo_path, branch, ahead, behind)
        return

    # Disposable worktree under the SYSTEM temp dir — never inside the repo, so
    # we can't accidentally pollute or commit it.
    tmpdir = tempfile.mkdtemp(prefix="ccc-ship-reconcile-")
    tmp_branch = None
    worktree_added = False
    try:
        _ship_update(repo_path, "pulling",
                     f"Diverged (ahead {ahead}/behind {behind}) — reconciling in "
                     "isolated worktree…")
        # Detached checkout at the remote tip. --detach keeps us off any named
        # branch so we never collide with a branch checked out elsewhere.
        rc, out, err = _core._git(
            ["worktree", "add", "--detach", tmpdir, f"origin/{branch}"],
            repo_path, timeout=60)
        if rc != 0:
            _ship_log(repo_path,
                      f"worktree add failed ({err or out.strip() or rc}) — handing off", "warn")
            _ship_diverged_handoff(repo_path, branch, ahead, behind)
            return
        worktree_added = True

        # Replay local commits onto origin/{branch}, in order. -C points git at
        # the worktree; the shared clone is never the cwd of these operations.
        _ship_log(repo_path,
                  f"cherry-picking {len(commits)} local commit(s) onto origin/{branch}", "info")
        rc, out, err = _core._git(
            ["-C", tmpdir, "cherry-pick", *commits], repo_path, timeout=180)
        if rc != 0:
            # A conflict (or any cherry-pick failure) — abort cleanly and hand
            # off. We deliberately do NOT try to auto-resolve; a human owns
            # conflict resolution.
            _ship_log(repo_path,
                      "conflict during reconcile — handing off for manual worktree reconcile",
                      "warn")
            _core._git(["-C", tmpdir, "cherry-pick", "--abort"], repo_path, timeout=30)
            _ship_diverged_handoff(repo_path, branch, ahead, behind)
            return

        # Push the reconciled history. HEAD here is origin/{branch} + replayed
        # commits; pushing it updates the remote branch.
        _ship_update(repo_path, "pushing",
                     f"$ git push origin HEAD:{branch} (reconciled {len(commits)} commit(s))")
        rc, out, err = _core._git(
            ["-C", tmpdir, "push", "origin", f"HEAD:refs/heads/{branch}"],
            repo_path, timeout=120)
        if rc != 0:
            # Non-fast-forward (someone pushed again) or other push failure —
            # do NOT force. Hand off; the next run re-fetches and retries.
            _ship_log(repo_path,
                      f"reconciled push rejected ({err or out.strip() or rc}) — handing off",
                      "warn")
            _ship_diverged_handoff(repo_path, branch, ahead, behind)
            return

        rc, new_sha, _ = _core._git(["-C", tmpdir, "rev-parse", "HEAD"], repo_path)
        new_sha = (new_sha or "").strip()
        _ship_log(repo_path, f"reconciled history pushed to origin/{branch}", "ok")

        # Bring the shared clone up to date with a SAFE fast-forward. After the
        # reconciled push, the shared clone's tip is an ancestor of the new
        # origin/{branch}, so this ff rewrites nothing. If it somehow fails we
        # still report success — origin already moved, which is the ship.
        _core._git(["fetch", "origin", branch], repo_path, timeout=60)
        rc, out, err = _core._git(["merge", "--ff-only", f"origin/{branch}"], repo_path, timeout=60)
        if rc != 0:
            _ship_log(repo_path,
                      f"shared clone ff after reconcile failed ({err or out.strip() or rc}) — "
                      "origin is updated; resolve the local clone manually", "warn")
        else:
            _ship_log(repo_path, "fast-forwarded shared clone to reconciled tip", "ok")

        # Same deploy-poll tail as a normal push.
        _ship_post_push_deploy(repo_path, branch, new_sha or head_sha)
    except Exception as e:
        _ship_log(repo_path, f"reconcile crashed: {e} - handing off", "warn")
        _ship_diverged_handoff(repo_path, branch, ahead, behind)
    finally:
        # Always tear down the disposable worktree so it's never leaked, then
        # best-effort delete the temp ref and the tmpdir. force is fine: the
        # worktree is throwaway by construction.
        if worktree_added:
            _core._git(["worktree", "remove", "--force", tmpdir], repo_path, timeout=30)
        if tmp_branch:
            _core._git(["branch", "-D", tmp_branch], repo_path, timeout=15)
        # Prune any dangling worktree admin entry and remove the dir if git left
        # anything behind.
        _core._git(["worktree", "prune"], repo_path, timeout=15)
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def _ship_integrate(repo_path, branch):
    """Fetch + fast-forward + push (then optional Vercel deploy poll).

    Shared by the main ship flow AND the post-action continuation, so resolving
    the last handoff action (commit / drop / skip) carries straight through to a
    push instead of dead-ending at "needs you" — which was the "I clicked skip
    and nothing happened" gap.

    Never `rebase` a shared clone in place: a mid-rebase conflict leaves every
    session's checkout broken. Fetch, then only fast-forward. If the branch has
    diverged (local ahead AND behind), attempt an automated reconcile in an
    ISOLATED throwaway worktree (_ship_reconcile_diverged) — which falls back to
    the manual clean-worktree hand-off on any conflict/rejection/error. Runs to a
    terminal _ship_update on every path (own try/except so it's safe to launch in
    its own thread)."""
    try:
        _ship_update(repo_path, "pulling", f"$ git fetch origin {branch}")
        rc, _, err = _core._git(["fetch", "origin", branch], repo_path, timeout=60)
        if rc != 0:
            _ship_update(repo_path, "error", f"git fetch failed: {err or rc}", running=False)
            return
        rc_a, ahead_s, _ = _core._git(["rev-list", "--count", f"origin/{branch}..HEAD"], repo_path)
        rc_b, behind_s, _ = _core._git(["rev-list", "--count", f"HEAD..origin/{branch}"], repo_path)
        ahead = int((ahead_s or "0").strip() or 0) if rc_a == 0 else 0
        behind = int((behind_s or "0").strip() or 0) if rc_b == 0 else 0
        _ship_log(repo_path, f"local {branch}: ahead {ahead}, behind {behind}", "info")
        if ahead and behind:
            # Diverged: instead of bailing, try to reconcile automatically in a
            # disposable worktree. _ship_reconcile_diverged is terminal on every
            # path (it falls back to the manual hand-off on any conflict /
            # rejected push / error), so we return right after.
            _core._ship_reconcile_diverged(repo_path, branch, ahead, behind)
            return
        if behind:
            rc, out, err = _core._git(["merge", "--ff-only", f"origin/{branch}"], repo_path, timeout=60)
            if rc != 0:
                _ship_update(repo_path, "error",
                             f"fast-forward failed: {err or out.strip() or rc}", running=False)
                return
            _ship_log(repo_path, f"fast-forwarded {behind} commit(s) from origin", "ok")
        if not ahead:
            _ship_update(repo_path, "pushed", "Up to date - nothing to push.", running=False)
            return

        _ship_update(repo_path, "pushing", f"$ git push origin {branch} ({ahead} commit(s))")
        rc, out, err = _core._git(["push", "origin", branch], repo_path, timeout=120)
        if rc != 0:
            _ship_update(repo_path, "error",
                         f"git push failed: {err or out.strip() or rc}",
                         running=False)
            return

        rc, sha, _ = _core._git(["rev-parse", "HEAD"], repo_path)
        sha = (sha or "").strip()
        # Record the ship + run the shared deploy-poll tail (also reused by the
        # diverged-reconcile path).
        _ship_post_push_deploy(repo_path, branch, sha)
    except Exception as e:
        _ship_update(repo_path, "error", f"integrate crashed: {e}", running=False)


def _run_ship_flow(repo_path, branch):
    try:
        _ship_update(repo_path, "nudging", "Checking for uncommitted work…")
        rc, out, err = _core._git(["status", "--porcelain"], repo_path)
        if rc != 0:
            _ship_update(repo_path, "error", f"git status failed: {err or rc}", running=False)
            return
        dirty = bool(out.strip())
        was_dirty = dirty
        if dirty:
            n_dirt = len([l for l in out.splitlines() if l.strip()])
            _ship_log(repo_path, f"{n_dirt} uncommitted path(s) in {branch}", "warn")
            # Triage: auto-clear dirt whose content is already safe in git
            # (hand-copied PR work, behind-origin/main, etc.). Provably
            # non-destructive — we only restore byte-identical-to-a-commit files.
            _ship_update(repo_path, "triaging",
                         "Triaging — restoring files already saved in git…")
            tri = _ship_triage_restore(repo_path)
            for p, ref in tri["restored"]:
                _ship_log(repo_path, f"↩ restored {p} (already on {ref})", "ok")
            if tri["restored"]:
                _ship_log(repo_path,
                          f"auto-restored {len(tri['restored'])} file(s) already preserved in git",
                          "ok")
            for p in tri["junk"]:
                _ship_log(repo_path, f"junk (gitignore?): {p}", "warn")
            rc, out, err = _core._git(["status", "--porcelain"], repo_path)
            dirty = bool(out.strip()) if rc == 0 else dirty
            if not dirty:
                _ship_log(repo_path, "worktree clean after triage ✓", "ok")
        if dirty:
            candidates = _core._ship_candidate_sessions(repo_path)
            # Per-run attribution memo: a single ship run may consult the index
            # for the same path in both the nudge-targeting log below AND the
            # later per-file verdict pass — query it at most once each.
            attr_cache = {}
            try:
                names = _core._load_session_name_overrides()
            except Exception:
                names = {}

            def _label(sid):
                return _ship_session_label(sid, names)

            # Attribution-aware targeting (informational only). For each dirty
            # path, ask the conversation index who last touched it. When that
            # author is identifiable but NOT in the live candidate set, surface
            # it as an action rather than silently nudging everyone — this cuts
            # over-nudging. We deliberately DO NOT change who gets nudged: the
            # live candidates are still nudged exactly as before. The index only
            # adds targeting context to the log (and can be wrong/missing, so it
            # must never gate the nudge itself).
            try:
                live_set = set(candidates)
                seen_owners = set()
                for _, dp in (_ship_porcelain_paths(repo_path) or []):
                    if _core._ship_is_junk(dp):
                        continue
                    a = _core._ship_index_attribution(repo_path, dp, _cache=attr_cache)
                    if not a:
                        continue
                    osid = a.get("session_id")
                    if osid and osid not in live_set and osid not in seen_owners:
                        seen_owners.add(osid)
                        _ship_log(repo_path,
                                  f"owned by {a['owner']} (last active {a['age']} ago) — "
                                  "not live, surfacing as action",
                                  "info")
            except Exception:
                # Attribution is a nicety; never let it break the nudge flow.
                pass

            # Idempotent per-session: skip a session that already answered and
            # hasn't written anything since — another session's edits never
            # re-trigger a nudge to this one.
            fresh, skipped = [], 0
            for sid in candidates:
                if _ship_already_answered(sid):
                    skipped += 1
                else:
                    fresh.append(sid)
            if skipped:
                _ship_log(repo_path,
                          f"↩ skipped {skipped} session(s) — already answered for this state",
                          "info")
            _ship_update(repo_path, "nudging",
                         f"Asking {len(fresh)} session(s) to commit"
                         + (f" ({skipped} already answered)" if skipped else "") + "…")
            nudged = []
            acks = []   # two-way: track each nudged session's transcript reply
            for sid in fresh:
                name = _label(sid)
                jsonl = _core._find_session_jsonl(sid)
                offset = None
                if jsonl is not None:
                    try:
                        offset = jsonl.stat().st_size
                    except OSError:
                        offset = None
                try:
                    res = _core._inject_text_into_session(sid, _core.TIER_A_COMMIT_NUDGE)
                    if res.get("ok"):
                        nudged.append(sid)
                        via = res.get("via") or ("resumed" if res.get("resumed") else "sent")
                        _ship_log(repo_path, f"→ nudged {name} ({via})", "info")
                        acks.append({"sid": sid, "name": name, "jsonl": jsonl,
                                     "offset": offset, "since": time.time() - 1.0,
                                     "acked": False})
                        if jsonl is None:
                            # Non-Claude (no readable transcript): we can't hear
                            # the reply, so treat the nudge itself as the
                            # "don't re-bug until it writes again" marker.
                            _record_ship_ack(sid)
                    else:
                        _ship_log(repo_path, f"✗ {name}: {res.get('error') or 'no route'}", "warn")
                except Exception as e:
                    _ship_log(repo_path, f"✗ {name}: {e}", "warn")
            trackable = [a for a in acks if a["jsonl"] is not None]
            # No new nudges → nothing to wait for. Otherwise wait the full cap
            # only when there's a reply we can actually read; a short grace
            # suffices when every nudge went to a non-readable engine.
            wait_cap = 0 if not nudged else (SHIP_COMMIT_WAIT_CAP if trackable else 15)
            waiting_on = _ship_waiting_on_from_acks(acks)
            _ship_update(repo_path, "waiting_commits",
                         f"Waiting for {len(nudged)} session(s) to reply…",
                         nudged=nudged, waiting_on=waiting_on)
            deadline = time.time() + wait_cap
            last_remaining = None
            while time.time() < deadline:
                with _core._ship_jobs_lock:
                    continue_requested = bool((_core._ship_jobs.get(repo_path) or {}).get("continue_requested"))
                if continue_requested:
                    break
                # Listen for each session's reply (the missing back-channel).
                waiting_changed = False
                for a in trackable:
                    if a["acked"]:
                        continue
                    reply = _ship_read_reply(a["jsonl"], a["offset"], a["since"])
                    # Only count it when the session actually says DONE (what we
                    # asked for) — otherwise we'd grab an unrelated assistant turn
                    # (e.g. it answering a different question).
                    if reply and re.search(r"\bDONE\b", reply):
                        a["acked"] = True
                        waiting_changed = True
                        _record_ship_ack(a["sid"])
                        done_line = next((l.strip() for l in reply.splitlines()
                                          if "DONE" in l), reply.splitlines()[0])
                        _ship_log(repo_path, f"✓ {a['name']}: {done_line[:140]}", "ok")
                if waiting_changed:
                    pending = len([a for a in trackable if not a.get("acked")])
                    _ship_update(repo_path, "waiting_commits",
                                 f"Waiting for {pending} session(s) to reply…",
                                 log=False, nudged=nudged,
                                 waiting_on=_ship_waiting_on_from_acks(acks))
                rc, out, err = _core._git(["status", "--porcelain"], repo_path)
                if rc == 0 and not out.strip():
                    dirty = False
                    _ship_log(repo_path, "worktree clean ✓", "ok")
                    break
                remaining = len([l for l in out.splitlines() if l.strip()]) if rc == 0 else None
                if remaining is not None and remaining != last_remaining:
                    _ship_log(repo_path, f"… {remaining} path(s) still uncommitted", "info")
                    last_remaining = remaining
                # Everyone we can hear from has answered and it's still dirty →
                # the remaining dirt is unclaimed; stop waiting the full cap.
                if trackable and all(a["acked"] for a in trackable):
                    _ship_log(repo_path, "all nudged sessions replied", "info")
                    break
                time.sleep(SHIP_POLL_INTERVAL)
            # Final source-of-truth check (covers the wait_cap==0 skip path too).
            rc, out, _ = _core._git(["status", "--porcelain"], repo_path)
            dirty = bool(out.strip()) if rc == 0 else dirty
            if dirty:
                # Categorize what's left for the human. We will NOT auto-commit:
                # there's a push-to-prod behind this, and this is a shared clone.
                paths = [p for _, p in (_ship_porcelain_paths(repo_path) or [])]
                junk = [p for p in paths if _core._ship_is_junk(p)]
                rest = [p for p in paths if not _core._ship_is_junk(p)]
                infra = [p for p in rest if _core._ship_classify_remaining(p) == "infra"]
                review = [p for p in rest if _core._ship_classify_remaining(p) == "review"]
                _ship_log(repo_path,
                          f"{len(infra)} infra · {len(review)} app/deploy · {len(junk)} junk left",
                          "info")
                # Per-file git-derived verdict for each app/deploy file (no LLM,
                # no spawn) — commit / restore / leave-for-PR — surfaced as its
                # own Approve/Reject row.
                review_actions = []
                v_counts = {"commit": 0, "restore": 0, "leave": 0}
                for i, p in enumerate(review):
                    # Reuse the per-run attribution memo built during the
                    # nudge-targeting pass above so a path isn't re-queried.
                    v = _core._ship_review_verdict(repo_path, p, _attr_cache=attr_cache)
                    verdict, why = v["verdict"], v["why"]
                    v_counts[verdict] = v_counts.get(verdict, 0) + 1
                    base = p.rstrip("/").rsplit("/", 1)[-1]
                    if verdict == "commit":
                        review_actions.append({
                            "id": f"rv-{i}", "kind": "commit", "paths": [p],
                            "label": f"Commit {base}", "detail": why,
                            "message": f"chore({_ship_commit_scope(p)}): {base}",
                        })
                    elif verdict == "restore":
                        review_actions.append({
                            "id": f"rv-{i}", "kind": "restore", "paths": [p],
                            "label": f"Discard {base}", "detail": why,
                        })
                    else:  # leave
                        review_actions.append({
                            "id": f"rv-{i}", "kind": "leave", "paths": [p],
                            "label": f"Leave {base}", "detail": why,
                        })
                if review:
                    _ship_log(repo_path,
                              f"verdicts → {v_counts['commit']} commit · "
                              f"{v_counts['restore']} discard · {v_counts['leave']} leave-for-PR",
                              "info")
                # Cheap divergence pre-check so the handoff shows the whole path
                # to green (the push will also need this resolved).
                _core._git(["fetch", "origin", branch], repo_path, timeout=30)
                rc_a, ah, _ = _core._git(["rev-list", "--count", f"origin/{branch}..HEAD"], repo_path)
                rc_b, bh, _ = _core._git(["rev-list", "--count", f"HEAD..origin/{branch}"], repo_path)
                ahead = int((ah or "0").strip() or 0) if rc_a == 0 else 0
                behind = int((bh or "0").strip() or 0) if rc_b == 0 else 0
                if ahead and behind:
                    _ship_log(repo_path,
                              f"branch diverged (ahead {ahead}, behind {behind}) — once clean, "
                              f"reconcile from a worktree off origin/{branch} then push", "warn")
                # Structured actions the UI offers Approve/Reject on.
                ship_actions = []
                if infra:
                    ship_actions.append({
                        "id": "commit-infra", "kind": "commit",
                        "label": f"Commit {len(infra)} infra file(s)",
                        "detail": "docs / config / scripts - low blast radius",
                        "paths": infra,
                        "message": "chore: commit infra (docs/config/scripts)",
                    })
                if junk:
                    ship_actions.append({
                        "id": "drop-junk", "kind": "drop",
                        "label": f"Delete {len(junk)} junk file(s)",
                        "detail": "untracked cruft (__pycache__, .DS_Store, …)",
                        "paths": junk,
                    })
                ship_actions.extend(review_actions)
                acked_n = sum(1 for a in acks if a["acked"])
                _ship_update(repo_path, "needs_you",
                             f"Needs you: {len(infra)} infra (safe to commit), "
                             f"{len(review)} app/deploy (review - ships to prod), "
                             f"{len(junk)} junk"
                             + (f"; branch diverged (ahead {ahead}/behind {behind})" if ahead and behind else "")
                             + ". Won't auto-commit behind a push.",
                             running=False, actions=ship_actions)
                return
        if not was_dirty:
            _ship_log(repo_path, "worktree already clean ✓", "ok")

        _core._ship_integrate(repo_path, branch)
    except Exception as e:
        _ship_update(repo_path, "error", f"ship flow crashed: {e}", running=False)


def start_repo_ship(repo_path):
    """Kick off (or no-op rejoin) the ship flow for a repo. Returns the job."""
    repo_path = _core.resolve_repo_path(repo_path)
    rc, branch, _ = _core._git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    branch = (branch or "").strip() or "main"
    with _core._ship_jobs_lock:
        existing = _core._ship_jobs.get(repo_path)
        if existing and existing.get("running"):
            return dict(existing, already_running=True)
        job = {
            "phase": "starting",
            "message": "Starting ship…",
            "running": True,
            "branch": branch,
            "nudged": [],
            "started_at": time.time(),
            "updated_at": time.time(),
            "log": [],
        }
        _append_ship_log(job, f"Push all → {Path(repo_path).name} ({branch})", "info")
        _core._ship_jobs[repo_path] = job
        snapshot = dict(job)
    threading.Thread(
        target=_core._run_ship_flow, args=(repo_path, branch), daemon=True
    ).start()
    return snapshot


_REPO_SHIP_STATUS_TTL = 15.0
_repo_ship_status_cache = {}        # repo_path -> {"ts", "data"}
_repo_ship_status_cache_lock = threading.Lock()
_repo_ship_status_build_locks = {}  # repo_path -> threading.Lock


def _repo_ship_status_slow_parts(repo_path):
    """The expensive half of ship/status: the `git status` subprocess, the
    on-disk job/state JSON reads, and the vercel-link probe. TTL-cached per
    repo with a per-repo build lock so the folder-header burst (one request
    per repo on every conversation-list render) collapses to one subprocess
    per repo per TTL instead of one per request (CCC-614)."""
    now = time.time()
    with _repo_ship_status_cache_lock:
        ent = _core._repo_ship_status_cache.get(repo_path)
        if ent is not None and now - ent["ts"] < _core._REPO_SHIP_STATUS_TTL:
            return ent["data"]
        lock = _core._repo_ship_status_build_locks.setdefault(repo_path, threading.Lock())
    with lock:
        with _repo_ship_status_cache_lock:
            ent = _core._repo_ship_status_cache.get(repo_path)
            if ent is not None and time.time() - ent["ts"] < _core._REPO_SHIP_STATUS_TTL:
                return ent["data"]
        persisted = _core._load_repo_ship_state().get(repo_path) or {}
        rc, out, _ = _core._git(["status", "--porcelain"], repo_path)
        data = {
            "job": _core._load_ship_job(repo_path),
            "dirty": bool(out.strip()) if rc == 0 else None,
            "last_ship_at": persisted.get("last_ship_at"),
            "last_ship_sha": persisted.get("last_ship_sha"),
            "vercel": bool(_core._resolve_vercel_project(repo_path)),
        }
        with _repo_ship_status_cache_lock:
            if len(_core._repo_ship_status_cache) > 256:
                _core._repo_ship_status_cache.clear()
                _core._repo_ship_status_build_locks.clear()
            _core._repo_ship_status_cache[repo_path] = {"ts": time.time(), "data": data}
        return data


def repo_ship_status(repo_path):
    """Current ship phase + persisted last-ship + live dirty/clean for a repo."""
    repo_path = _core.resolve_repo_path(repo_path)
    with _core._ship_jobs_lock:
        job = dict(_core._ship_jobs.get(repo_path) or {}) or None
    cached = _repo_ship_status_slow_parts(repo_path)
    if job is None:                       # in-memory gone (refresh/restart) → disk
        job = cached["job"]
    return {
        "ok": True,
        "repo_path": repo_path,
        "dirty": cached["dirty"],
        "job": job,
        "last_ship_at": cached["last_ship_at"],
        "last_ship_sha": cached["last_ship_sha"],
        "vercel": cached["vercel"],
    }


def continue_repo_ship(repo_path):
    """Ask a live Push all wait loop to stop waiting for remaining replies."""
    with _core._ship_jobs_lock:
        job = _core._ship_jobs.get(repo_path)
        if not job:
            return {"ok": False, "error": "no active ship job"}
        if not job.get("running") or job.get("phase") != "waiting_commits":
            return {"ok": False, "error": "ship job is not waiting for replies", "job": dict(job)}
        job["continue_requested"] = True
        job["updated_at"] = time.time()
        _append_ship_log(
            job,
            "Continue requested - no longer waiting for remaining commit replies.",
            "warn",
        )
        snapshot = dict(job)
    try:
        _persist_ship_job(repo_path, snapshot)
    except OSError:
        pass
    return {"ok": True, "job": snapshot}


def _execute_ship_action(repo_path, action):
    """Run one approved handoff action. Scoped to the action's explicit paths;
    commits only those (race-safe), deletes only junk-classified untracked
    files, never touches review/app code."""
    kind = action.get("kind")
    paths = [p for p in (action.get("paths") or []) if p]
    if kind == "commit":
        if not paths:
            return {"ok": False, "log": "nothing to commit", "level": "warn"}
        untracked = [p for p in paths
                     if _core._git(["ls-files", "--error-unmatch", "--", p], repo_path)[0] != 0]
        if untracked:
            _core._git(["add", "--", *untracked], repo_path)
        msg = action.get("message") or "chore: commit infra"
        rc, out, err = _core._git(["commit", "--only", "-m", msg, "--", *paths], repo_path, timeout=30)
        if rc != 0:
            return {"ok": False, "log": f"commit failed: {err or out.strip() or rc}", "level": "error"}
        return {"ok": True, "log": f"✓ committed {len(paths)} file(s): {msg}", "level": "ok"}
    if kind == "drop":
        removed = 0
        for p in paths:
            if not _core._ship_is_junk(p):       # safety: only ever delete junk
                continue
            fp = (Path(repo_path) / p).resolve()
            if not str(fp).startswith(str(Path(repo_path).resolve()) + os.sep):
                continue                   # path-escape guard
            try:
                if fp.is_dir():
                    shutil.rmtree(fp); removed += 1
                elif fp.exists():
                    fp.unlink(); removed += 1
            except OSError:
                pass
        return {"ok": True, "log": f"✓ deleted {removed} junk path(s)", "level": "ok"}
    if kind == "restore":
        # Safety re-check: only discard if STILL byte-identical to a committed
        # version (content preserved in git). Refuse otherwise.
        for p in paths:
            if not _ship_safe_restore_ref(repo_path, p):
                return {"ok": False, "log": f"refused: {p} no longer matches a commit", "level": "warn"}
        rc, out, err = _core._git(["restore", "--staged", "--worktree", "--", *paths], repo_path, timeout=15)
        if rc != 0:
            return {"ok": False, "log": f"restore failed: {err or out.strip() or rc}", "level": "error"}
        return {"ok": True, "log": f"↩ discarded {len(paths)} file(s) (already in git)", "level": "ok"}
    if kind in ("review", "leave"):
        return {"ok": False, "log": "left for the owning branch/PR — by hand", "level": "warn"}
    return {"ok": False, "log": "unknown action", "level": "warn"}


def apply_ship_action(repo_path, action_id, decision):
    """Approve/reject one handoff action (build 3). Returns updated status."""
    repo_path = _core.resolve_repo_path(repo_path)
    with _core._ship_jobs_lock:
        job = _core._ship_jobs.get(repo_path) or _core._load_ship_job(repo_path) or {}
        action = next((a for a in (job.get("actions") or []) if a.get("id") == action_id), None)
    if not action:
        return {"ok": False, "error": "unknown or expired action"}
    decision = (decision or "").lower()
    if decision == "reject":
        result = {"ok": True, "log": f"✗ skipped: {action.get('label')}", "level": "info"}
    elif decision == "approve":
        result = _execute_ship_action(repo_path, action)
    else:
        return {"ok": False, "error": "decision must be 'approve' or 'reject'"}
    with _core._ship_jobs_lock:
        job = _core._ship_jobs.get(repo_path) or _core._load_ship_job(repo_path) or {}
        # Drop the action only if it succeeded (or was rejected); keep on failure
        # so the user can retry after fixing.
        if result.get("ok") or decision == "reject":
            job["actions"] = [a for a in (job.get("actions") or []) if a.get("id") != action_id]
        _append_ship_log(job, result.get("log") or decision, result.get("level", "info"))
        job["updated_at"] = time.time()
        # Once the LAST handoff action is resolved (committed, dropped, or
        # skipped), carry straight through to integrate (pull + push) instead of
        # dead-ending. running=True is set under the lock so a double-click can't
        # launch two integrate threads.
        branch = job.get("branch") or "main"
        will_continue = not job.get("actions") and not job.get("running")
        if will_continue:
            job["running"] = True
            job["phase"] = "pulling"
            _append_ship_log(job, "all actions resolved - integrating (pull + push)…", "info")
        _core._ship_jobs[repo_path] = job
        snapshot = dict(job)
    _persist_ship_job(repo_path, snapshot)
    if will_continue:
        threading.Thread(
            target=_core._ship_integrate, args=(repo_path, branch), daemon=True
        ).start()
    return {"ok": result.get("ok", True), "job": snapshot, "message": result.get("log")}


# ── Next.js dev server (localhost pill) ──────────────────────────────────────
# One tracked dev server per repo. Started on demand from the localhost pill,
# polled by /api/nextjs/status, opens http://localhost:<port> on click.

_NEXTJS_PROCS: dict = {}
_NEXTJS_LOCK = threading.Lock()
_NEXTJS_LOG_DIR = _core.COMMAND_CENTER_STATE_DIR / "nextjs"
_NEXTJS_PORT_RE = re.compile(
    r"(?:Local:|started server on|ready - started server on|Local URL:)\s+"
    r"https?://[^\s:/]+:(\d{2,5})",
    re.IGNORECASE,
)


def _has_next_locally(path: Path) -> bool:
    """Per-directory Next.js check (no recursion, no walking up)."""
    for cfg in ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs"):
        if (path / cfg).is_file():
            return True
    pkg = path / "package.json"
    if not pkg.is_file():
        return False
    try:
        with open(pkg, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = data.get(key) or {}
        if isinstance(deps, dict) and "next" in deps:
            return True
    return False


def _read_pkg_json(path: Path):
    pkg = Path(path) / "package.json"
    if not pkg.is_file():
        return None
    try:
        with open(pkg, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _has_dev_script(path: Path) -> bool:
    """True when the dir's package.json declares a runnable `dev` script.
    Catches Vite / CRA / Astro / SvelteKit / Remix / plain-node dev servers —
    anything started with `npm/pnpm/yarn run dev` — so the localhost pill isn't
    Next.js-only. `_resolve_dev_invocation` already returns the right `… dev`
    command for these."""
    data = _read_pkg_json(path) or {}
    scripts = data.get("scripts")
    dev = scripts.get("dev") if isinstance(scripts, dict) else None
    return isinstance(dev, str) and bool(dev.strip())


def _enumerate_workspaces(repo_path: Path):
    """Glob the root package.json's `workspaces` field. Tolerant of dict and
    list forms (yarn classic vs npm/pnpm)."""
    data = _read_pkg_json(repo_path) or {}
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages") or []
    if not isinstance(workspaces, list):
        return []
    out = []
    for pattern in workspaces:
        if not isinstance(pattern, str):
            continue
        for m in repo_path.glob(pattern):
            if m.is_dir():
                out.append(m)
    return out


def _find_turbo_root(repo_path: Path):
    """Walk up looking for turbo.json. Returns the dir or None."""
    p = Path(repo_path).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / "turbo.json").is_file():
            return candidate
    return None


def _detect_nextjs(repo_path: Path) -> bool:
    """True when the repo has a startable dev server. Next.js directly, or as a
    turbo/npm-workspaces monorepo root with a Next.js workspace, OR any project
    with a plain `dev` script (Vite/CRA/Astro/etc.). Name kept for history; the
    localhost pill is engine-agnostic now."""
    repo_path = Path(repo_path)
    if _has_next_locally(repo_path) or _has_dev_script(repo_path):
        return True
    # Monorepo root: if any declared workspace is Next.js, the pill should
    # offer to start it (turbo dev or npm-run-dev with a workspace filter).
    for ws in _enumerate_workspaces(repo_path):
        if _has_next_locally(ws):
            return True
    return False


def _discover_node_bins():
    """Return a list of directories likely to contain `node`/`npm`/`npx`,
    in priority order. Used to augment the spawn env's PATH when CCC was
    started from a shell that didn't source nvm.

    Strategy: prefer nvm's `default` alias, then nvm's newest installed
    version, then Homebrew (Apple Silicon + Intel), then `/usr/local/bin`.
    Only includes paths that actually exist + contain a `node` executable
    so we don't accidentally PATH-poison with stale entries.
    """
    candidates = []
    home = Path.home()
    nvm_versions = home / ".nvm" / "versions" / "node"

    # nvm "default" alias points at the canonical user-preferred version
    default_alias = home / ".nvm" / "alias" / "default"
    if default_alias.is_file():
        try:
            ver = default_alias.read_text().strip()
            if ver:
                # alias may store a version like "v24.12.0" or "stable" — both
                # resolve to a versioned dir name under versions/node/.
                if not ver.startswith("v"):
                    # Resolve named aliases like "stable" → versioned dir
                    for sub in sorted(nvm_versions.iterdir() if nvm_versions.is_dir() else [], reverse=True):
                        if sub.is_dir():
                            ver = sub.name
                            break
                candidates.append(str(nvm_versions / ver / "bin"))
        except OSError:
            pass

    # Newest installed nvm version, as a fallback
    if nvm_versions.is_dir():
        try:
            installed = sorted(
                (p for p in nvm_versions.iterdir() if p.is_dir()),
                key=lambda p: p.name,
                reverse=True,
            )
            for p in installed:
                candidates.append(str(p / "bin"))
        except OSError:
            pass

    # System / package-manager Node installs
    candidates.extend([
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(home / ".local" / "bin"),
    ])

    # Dedup while keeping order; keep only paths that contain a `node` binary
    seen = set()
    out = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        if Path(c, "node").exists() or Path(c, "node").is_symlink():
            out.append(c)
    return out


def _nextjs_pkg_manager(repo_path: Path):
    """Pick a package manager + dev argv based on lockfile presence."""
    repo_path = Path(repo_path)
    if (repo_path / "pnpm-lock.yaml").is_file():
        return ["pnpm", "dev"]
    if (repo_path / "yarn.lock").is_file():
        return ["yarn", "dev"]
    return ["npm", "run", "dev"]


def _nextjs_dev_script_ports(path: Path):
    """Ports explicitly declared by a package's `dev` script."""
    data = _read_pkg_json(path) or {}
    scripts = data.get("scripts") or {}
    dev = scripts.get("dev")
    if not isinstance(dev, str) or not dev.strip():
        return []
    try:
        parts = shlex.split(dev)
    except ValueError:
        parts = dev.split()
    ports = []
    for i, part in enumerate(parts):
        val = None
        if part in ("--port", "-p") and i + 1 < len(parts):
            val = parts[i + 1]
        elif part.startswith("--port="):
            val = part.split("=", 1)[1]
        elif part.startswith("-p="):
            val = part.split("=", 1)[1]
        if val and re.fullmatch(r"\d{2,5}", str(val)):
            port = int(val)
            if 0 < port <= 65535 and port not in ports:
                ports.append(port)
    return ports


def _nextjs_workspace_sort_key(path: Path):
    data = _read_pkg_json(path) or {}
    name = data.get("name") if isinstance(data.get("name"), str) else ""
    return (name.lower() or path.name.lower(), str(path))


def _pick_nextjs_workspace(repo_path: Path):
    """Choose a concrete Next.js app when the selected target is a monorepo root."""
    workspaces = [ws for ws in _enumerate_workspaces(repo_path) if _has_next_locally(ws)]
    if not workspaces:
        return None
    workspaces = sorted(workspaces, key=_nextjs_workspace_sort_key)
    if len(workspaces) == 1:
        return workspaces[0]
    non_default_port = [
        ws for ws in workspaces
        if any(port and port != 3000 for port in _nextjs_dev_script_ports(ws))
    ]
    if non_default_port:
        return non_default_port[0]
    return workspaces[0]


def _nextjs_target_paths(repo_path, cwd=None):
    """Validate a Next.js target cwd inside a repo-scoped request.

    `repo_path` is the stable API/security boundary. `cwd` lets the UI point
    at a workspace package inside that repo, so turbo filters and dev-script
    ports are resolved from the app that is actually selected.

    A session's live cwd often hops to a non-app package inside a monorepo
    (e.g. `packages/foo`, `.a5c`) that has no `dev` script. Rather than report
    "no dev server" there, fall back to a real Next.js workspace, then the repo
    root, so the localhost pill stays useful wherever the session is parked.
    """
    repo_root = Path(_core.resolve_repo_path(repo_path)).resolve()
    target = Path(cwd or repo_root).expanduser().resolve()
    if not target.is_dir():
        raise _core.RepoContextError("invalid_cwd", f"cwd is not a directory: {target}", path=str(target))
    # Security boundary: the requested cwd must live inside the repo, checked
    # before any fallback so an out-of-repo cwd still 403s instead of being
    # silently swapped for a workspace.
    try:
        target.relative_to(repo_root)
    except ValueError:
        raise _core.RepoContextError(
            "invalid_cwd",
            "cwd must be inside repo_path for Next.js dev server actions",
            path=str(target),
            status=403,
        )
    # Prefer the requested cwd when it is itself startable. Otherwise (the repo
    # root, or a non-app package) resolve to a concrete Next.js workspace, then
    # fall back to the root if it is startable on its own.
    if target == repo_root or not _detect_nextjs(target):
        workspace_target = _pick_nextjs_workspace(repo_root)
        if workspace_target:
            target = workspace_target.resolve()
        elif target != repo_root and _detect_nextjs(repo_root):
            target = repo_root
    return repo_root, target


def _resolve_dev_invocation(repo_path: Path):
    """Return (cmd, cwd) for spawning the dev server.

    Turbo-aware: if a `turbo.json` lives at the picked dir or any ancestor
    AND its `tasks` (or legacy `pipeline`) include `dev`, we run the user's
    monorepo flow — `npx turbo dev` from the turbo root, scoped to the
    current package via `--filter=<name>` when picked at a workspace.

    Falls back to `npm run dev` / `pnpm dev` / `yarn dev` from the picked
    dir when there's no turbo setup.
    """
    repo_path = Path(repo_path).resolve()
    turbo_root = _find_turbo_root(repo_path)
    if turbo_root:
        turbo_cfg = {}
        try:
            with open(turbo_root / "turbo.json", "r") as f:
                turbo_cfg = json.load(f) or {}
        except (OSError, json.JSONDecodeError):
            turbo_cfg = {}
        tasks = turbo_cfg.get("tasks") or turbo_cfg.get("pipeline") or {}
        if "dev" in tasks:
            if repo_path == turbo_root:
                return (["npx", "turbo", "dev"], turbo_root)
            pkg = _read_pkg_json(repo_path) or {}
            pkg_name = pkg.get("name")
            if pkg_name:
                return (
                    ["npx", "turbo", "dev", f"--filter={pkg_name}"],
                    turbo_root,
                )
            # Workspace without a name field — fall through to direct npm.
    return (_nextjs_pkg_manager(repo_path), repo_path)


def _nextjs_proc_alive(entry):
    """True when the tracked process is still running.

    Uses Popen.poll() (which calls waitpid internally) when we have the
    handle — that reaps zombies and reflects an exited child correctly.
    Falls back to `os.kill(pid, 0)` only when the entry is from a foreign
    source (currently nothing — kept defensively).
    """
    if not entry:
        return False
    proc = entry.get("proc")
    if proc is not None:
        return proc.poll() is None
    pid = entry.get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _ps_dev_processes():
    """Return a small process-table snapshot for Next/Turbo rediscovery."""
    try:
        r = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid=,pgid=,command="],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    rows = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            rows.append({
                "pid": int(parts[0]),
                "ppid": int(parts[1]),
                "pgid": int(parts[2]),
                "command": parts[3],
            })
        except ValueError:
            continue
    return rows


def _process_basename(command: str):
    first = (command or "").strip().split(None, 1)[0] if (command or "").strip() else ""
    return Path(first).name


def _dev_command_port(command: str):
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    for i, part in enumerate(parts):
        val = None
        if part in ("--port", "-p") and i + 1 < len(parts):
            val = parts[i + 1]
        elif part.startswith("--port="):
            val = part.split("=", 1)[1]
        elif part.startswith("-p="):
            val = part.split("=", 1)[1]
        if val and re.fullmatch(r"\d{2,5}", str(val)):
            port = int(val)
            if 0 < port <= 65535:
                return port
    return None


def _localhost_port_responds(port: int, timeout=0.35):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(
                b"HEAD / HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Connection: close\r\n\r\n"
            )
            try:
                return sock.recv(16).startswith(b"HTTP/")
            except socket.timeout:
                return False
    except (OSError, ValueError):
        return False


def _nextjs_external_match_info(target_path: Path):
    target_path = Path(target_path).resolve()
    cmd, run_cwd = _core._resolve_dev_invocation(target_path)
    pkg = _read_pkg_json(target_path) or {}
    pkg_name = pkg.get("name") if isinstance(pkg.get("name"), str) else ""
    expected_ports = _nextjs_dev_script_ports(target_path)
    return {
        "cmd": cmd,
        "run_cwd": Path(run_cwd).resolve(),
        "target_path": target_path,
        "package_name": pkg_name,
        "filter_expected": any(str(arg).startswith("--filter") for arg in cmd),
        "ports": expected_ports,
    }


def _nextjs_command_matches(command: str, info: dict):
    """True when a process row looks like this target's dev server.

    This intentionally rejects arbitrary commands whose argv merely contains
    the same text (for example an agent prompt that pasted a `ps | rg` line).
    """
    command = command or ""
    base = _process_basename(command)
    allowed = {"node", "next", "npx", "npm", "pnpm", "yarn", "turbo"}
    if base not in allowed:
        return False

    pkg_name = info.get("package_name") or ""
    expected_ports = info.get("ports") or []
    target_s = str(info.get("target_path") or "")
    cwd_s = str(info.get("run_cwd") or "")

    has_turbo_dev = bool(re.search(r"(?:^|[/\s])turbo(?:\s+run)?\s+dev(?:\s|$)", command))
    if has_turbo_dev:
        if pkg_name and info.get("filter_expected"):
            filter_re = r"--filter(?:=|\s+)" + re.escape(pkg_name) + r"(?:\s|$)"
            return bool(re.search(filter_re, command))
        return cwd_s and cwd_s in command

    has_next_dev = bool(re.search(r"(?:^|[/\s])next\s+dev(?:\s|$)", command))
    if has_next_dev:
        port = _dev_command_port(command)
        if expected_ports and port in expected_ports:
            return True
        if target_s and target_s in command:
            return True
        if cwd_s and cwd_s in command and not expected_ports:
            return True
    return False


def _find_external_nextjs_process(target_path: Path):
    """Find a dev server that survived a CCC restart or was started by hand."""
    info = _nextjs_external_match_info(target_path)
    rows = _ps_dev_processes()
    base_matches = [row for row in rows if _core._nextjs_command_matches(row.get("command", ""), info)]
    if not base_matches:
        return None

    matched_pids = {row["pid"] for row in base_matches}
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row["pid"] in matched_pids:
                continue
            if row["ppid"] in matched_pids:
                matched_pids.add(row["pid"])
                changed = True

    matched_rows = [row for row in rows if row["pid"] in matched_pids]
    ports = []
    for row in matched_rows:
        port = _dev_command_port(row.get("command", ""))
        if port and port not in ports:
            ports.append(port)
    for port in info.get("ports") or []:
        if port not in ports:
            ports.append(port)
    ready_port = next((p for p in ports if _localhost_port_responds(p)), None)
    first = base_matches[0]
    return {
        "pid": first["pid"],
        "pids": sorted(matched_pids),
        "port": ready_port,
        "started_at": None,
        "log_path": None,
        "cmd": first.get("command") or " ".join(info["cmd"]),
        "cwd": str(info["run_cwd"]),
        "external": True,
        "rows": matched_rows,
    }


def _nextjs_evict(repo_key: str):
    """Drop the tracked entry for a repo; safe to call without holding lock."""
    with _NEXTJS_LOCK:
        _NEXTJS_PROCS.pop(repo_key, None)


def _nextjs_tail_for_port(repo_key: str, log_path: Path, popen):
    """Background thread: tail the dev-server log, parse the port, store it."""
    deadline = time.time() + 60
    while time.time() < deadline:
        if popen.poll() is not None:
            return  # process exited before printing port
        try:
            text = log_path.read_text(errors="replace")
        except OSError:
            text = ""
        m = _NEXTJS_PORT_RE.search(text)
        if m:
            port = int(m.group(1))
            with _NEXTJS_LOCK:
                entry = _NEXTJS_PROCS.get(repo_key)
                if entry and entry.get("pid") == popen.pid:
                    entry["port"] = port
            return
        time.sleep(0.5)


def _nextjs_log_tail(log_path, max_lines=12):
    """Last N non-blank lines of a dev-server log, for failure surfacing."""
    if not log_path:
        return ""
    try:
        text = Path(log_path).read_text(errors="replace")
    except OSError:
        return ""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-max_lines:])


# Sticky exit info per repo so the UI can surface why a server died after
# eviction. Cleared on next successful start.
_NEXTJS_LAST_EXIT: dict = {}


def nextjs_status(repo_path, cwd=None):
    """Status payload for /api/nextjs/status."""
    _repo_root, target_path = _nextjs_target_paths(repo_path, cwd)
    repo_key = str(target_path)
    detected = _detect_nextjs(target_path)
    launch_cmd, launch_cwd = _core._resolve_dev_invocation(target_path)
    with _NEXTJS_LOCK:
        entry = _NEXTJS_PROCS.get(repo_key)
        snapshot = dict(entry) if entry else None
    last_exit = None
    if snapshot and not _nextjs_proc_alive(snapshot):
        proc = snapshot.get("proc")
        rc = proc.returncode if proc is not None else None
        last_exit = {
            "returncode": rc,
            "log_tail": _nextjs_log_tail(snapshot.get("log_path")),
            "log_path": snapshot.get("log_path"),
            "cmd": snapshot.get("cmd"),
            "ended_at": time.time(),
        }
        _NEXTJS_LAST_EXIT[repo_key] = last_exit
        _nextjs_evict(repo_key)
        snapshot = None
    elif not snapshot:
        last_exit = _NEXTJS_LAST_EXIT.get(repo_key)
    external = None if snapshot or not detected else _find_external_nextjs_process(target_path)
    running = snapshot or external
    return {
        "detected": detected,
        "running": bool(running),
        "pid": running.get("pid") if running else None,
        "pids": running.get("pids") if running else None,
        "port": running.get("port") if running else None,
        "log_path": running.get("log_path") if running else None,
        "started_at": running.get("started_at") if running else None,
        "cmd": running.get("cmd") if running else None,
        "cwd": running.get("cwd") if running else None,
        "launch_cmd": " ".join(launch_cmd),
        "launch_cwd": str(launch_cwd),
        "target_path": str(target_path),
        "external": bool(external),
        "last_exit": last_exit,
    }


def nextjs_start(repo_path, cwd=None):
    """Spawn `<pm> dev` in repo_path, track it, return pid + log path."""
    _repo_root, target_path = _nextjs_target_paths(repo_path, cwd)
    repo_key = str(target_path)
    if not _detect_nextjs(target_path):
        return {"ok": False, "error": "no dev server detected (no `dev` script / next config)"}, 400
    with _NEXTJS_LOCK:
        existing = _NEXTJS_PROCS.get(repo_key)
        if existing and _nextjs_proc_alive(existing):
            payload = {k: v for k, v in existing.items() if k != "proc"}
            return {"ok": False, "error": "already running", **payload}, 409
        if existing:
            _NEXTJS_PROCS.pop(repo_key, None)
    external = _find_external_nextjs_process(target_path)
    if external:
        payload = {k: v for k, v in external.items() if k != "rows"}
        return {"ok": False, "error": "already running", **payload}, 409
    cmd, run_cwd = _core._resolve_dev_invocation(target_path)
    _NEXTJS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", repo_key).strip("_")[-120:]
    log_path = _NEXTJS_LOG_DIR / f"{slug}.log"
    # The CCC server itself runs on $PORT; if that env var leaks into the
    # spawned `next dev`, Next.js will try to bind to the same port and the
    # user gets a baffling "URL is the CCC UI, not my app" experience (or a
    # bind error). Strip PORT/HOSTNAME so Next.js picks its default 3000
    # (or auto-bumps to 3001/3002 on collision).
    spawn_env = {k: v for k, v in os.environ.items() if k not in ("PORT", "HOSTNAME", "HOST")}
    spawn_env["FORCE_COLOR"] = "0"
    spawn_env["NO_COLOR"] = "1"
    spawn_env["BROWSER"] = "none"
    # If CCC was launched from a shell that didn't source nvm, its PATH
    # won't include the user's installed node — and `npx`/`turbo`/`next`
    # all fail with ENOENT. Probe for common node locations and prepend
    # them so subprocess (and the `sh -c next dev` shell inside npm/turbo)
    # can resolve the binaries.
    node_bins = _discover_node_bins()
    if node_bins:
        spawn_env["PATH"] = ":".join(node_bins) + ":" + spawn_env.get("PATH", "")
    try:
        log_path.write_text("")  # truncate prior run
        log_fh = open(log_path, "a")
        proc = subprocess.Popen(
            cmd,
            cwd=str(run_cwd),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=spawn_env,
        )
    except FileNotFoundError as e:
        return {"ok": False, "error": f"package manager not found: {cmd[0]} ({e})"}, 500
    except OSError as e:
        return {"ok": False, "error": str(e)}, 500
    entry = {
        "pid": proc.pid,
        "proc": proc,
        "port": None,
        "started_at": time.time(),
        "log_path": str(log_path),
        "cmd": " ".join(cmd),
        "cwd": str(run_cwd),
    }
    with _NEXTJS_LOCK:
        _NEXTJS_PROCS[repo_key] = entry
        _NEXTJS_LAST_EXIT.pop(repo_key, None)
    threading.Thread(
        target=_nextjs_tail_for_port,
        args=(repo_key, log_path, proc),
        daemon=True,
    ).start()
    payload = {k: v for k, v in entry.items() if k != "proc"}
    return {"ok": True, **payload}, 200


def _terminate_external_nextjs(target_path: Path):
    external = _find_external_nextjs_process(target_path)
    if not external:
        return {"matched": False, "pids": []}
    pids = [pid for pid in external.get("pids") or [] if pid and pid != os.getpid()]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            pass
    deadline = time.time() + 3
    while time.time() < deadline:
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except OSError:
                pass
        if not alive:
            break
        time.sleep(0.1)
    for pid in pids:
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return {"matched": True, "pids": pids}


def nextjs_stop(repo_path, cwd=None):
    """Stop a tracked or externally-discovered Next.js/Turbo dev server."""
    _repo_root, target_path = _nextjs_target_paths(repo_path, cwd)
    repo_key = str(target_path)
    with _NEXTJS_LOCK:
        entry = _NEXTJS_PROCS.get(repo_key)
    if not entry:
        external = _terminate_external_nextjs(target_path)
        return {"ok": True, "running": False, **external}, 200
    pid = entry.get("pid")
    if not pid:
        _nextjs_evict(repo_key)
        return {"ok": True, "running": False}, 200
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        _nextjs_evict(repo_key)
        return {"ok": True, "running": False}, 200
    except OSError as e:
        return {"ok": False, "error": str(e)}, 500
    deadline = time.time() + 3
    while time.time() < deadline:
        if not _nextjs_proc_alive(entry):
            break
        time.sleep(0.1)
    if _nextjs_proc_alive(entry):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass
    _nextjs_evict(repo_key)
    return {"ok": True, "running": False, "pid": pid}, 200


def nextjs_restart(repo_path, cwd=None):
    """Stop any matching dev server, then start a fresh one."""
    stop_payload, _stop_status = nextjs_stop(repo_path, cwd)
    start_payload, start_status = nextjs_start(repo_path, cwd)
    if start_payload.get("ok"):
        start_payload["restarted"] = True
        start_payload["stopped"] = stop_payload
    return start_payload, start_status


def _nextjs_shutdown_all():
    """atexit hook: SIGTERM every tracked Next.js process group."""
    with _NEXTJS_LOCK:
        entries = list(_NEXTJS_PROCS.values())
        _NEXTJS_PROCS.clear()
    for entry in entries:
        pid = entry.get("pid")
        if not pid:
            continue
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            pass


import atexit as _atexit
_atexit.register(_core._codex_app_server_shutdown)
_atexit.register(_core._acp_shutdown_all)
_atexit.register(_nextjs_shutdown_all)


def auto_verify_closed_issues(repo_path, conversations=None):
    """For any session with has_push + linked to a CLOSED GitHub issue,
    auto-set verified=True if not already. Returns what was changed."""
    repo_path = _core.resolve_repo_path(repo_path)
    verified_list = _core._load_verified_conversations()
    verified_set = set(verified_list)
    issue_states = _core._fetch_issue_states(repo_path)
    convs = (
        _core.find_conversations(repo_path) or []
        if conversations is None
        else conversations
    )
    newly_verified = []

    for c in convs:
        if c.get("verified") or c.get("archived"):
            continue
        tail_inum = c.get("tail_issue_number")
        has_push = c.get("has_push")
        if not has_push and not tail_inum:
            continue
        inum = c.get("linked_issue")
        if not inum:
            # Heuristic: parse display_name
            m = re.match(r"^issue-(\d+)$", c.get("display_name") or "")
            if m:
                inum = m.group(1)
        if not inum:
            # Last resort: the full detector (includes tail_issue_number from
            # in-session `gh issue` / `Closes #N` signals)
            inum = _core._detect_issue_number_for_session(c)
        if not inum:
            continue
        # Only verify when the ORIGINAL (spawn-time) committed issue is CLOSED.
        # Do NOT verify on tail_issue_number matches when they differ from the
        # linked issue — sessions often create sibling issues (e.g. via the
        # /announce-feature skill) that close separately; our commitment is to
        # the original issue the session was spawned for, which stays open
        # until that bug/feature is actually resolved.
        if not has_push and str(tail_inum) != str(inum):
            continue
        st = issue_states.get(str(inum))
        if not st or st["state"] != "CLOSED":
            continue
        sid = c.get("session_id") or c.get("id")
        if sid in verified_set:
            continue
        verified_list.append(sid)
        verified_set.add(sid)
        newly_verified.append({"session_id": sid, "issue": inum, "display_name": (c.get("display_name") or "")[:80]})
        # Also strip in-progress label
        remove_in_progress_label(inum, repo_path=repo_path)

    if newly_verified:
        _core._save_verified_conversations(verified_list)
        _core._bust_issue_state_cache(repo_path)

    return {"ok": True, "newly_verified": newly_verified, "count": len(newly_verified)}


def backfill_in_progress_labels(repo_path):
    """Scan current conversations; for each session whose display_name looks like
    'issue-N' and isn't verified/archived, mark its linked issue as in-progress.
    Skips issues that are already closed on GitHub.
    """
    marked = []
    skipped = []
    errors = []
    repo_path = _core.resolve_repo_path(repo_path)
    convs = _core.find_conversations(repo_path) or []
    # Collect currently-open issue numbers to avoid marking closed issues.
    open_issues = _core._fetch_backlog_issues(repo_path) or []
    open_set = {str(i.get("number")) for i in open_issues}

    seen = set()
    for c in convs:
        if c.get("verified") or c.get("archived"):
            continue
        issue_num = None
        dn = c.get("display_name") or ""
        m = re.match(r"^issue-(\d+)$", dn)
        if m:
            issue_num = m.group(1)
        elif c.get("linked_issue"):
            issue_num = str(c["linked_issue"])
        if not issue_num or issue_num in seen:
            continue
        seen.add(issue_num)
        if issue_num not in open_set:
            skipped.append({"issue": issue_num, "reason": "not open"})
            continue
        r = mark_issue_in_progress(issue_num, repo_path=repo_path)
        if r.get("ok"):
            marked.append(issue_num)
        else:
            errors.append({"issue": issue_num, "error": r.get("error", "?")})
    return {"ok": True, "marked": marked, "skipped": skipped, "errors": errors}


def mark_issue_in_progress(issue_number, repo_path, force_reopen=False):
    """Signal to GitHub that work is starting on an issue:
    - reopens the issue if closed as NOT_PLANNED (never if COMPLETED)
    - adds 'claude-in-progress' label
    - self-assigns to the authenticated gh user (@me)

    Will NOT reopen an issue that was closed with stateReason=COMPLETED unless
    force_reopen=True. This prevents stale-card drags from resurrecting shipped
    work (see 2026-04-18 #126 incident: UI showed 5-min-stale OPEN; drag→Working
    called mark_issue_in_progress which unconditionally reopened the issue).
    """
    repo_path = _core.resolve_repo_path(repo_path)
    result = {"ok": False, "issue_number": str(issue_number)}
    # Reopen only when safe
    try:
        st_out = subprocess.run(
            ["gh", "issue", "view", str(issue_number),
             "--json", "state,stateReason"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        if st_out.returncode == 0:
            st_data = json.loads(st_out.stdout)
            st = (st_data.get("state") or "").upper()
            reason = (st_data.get("stateReason") or "").upper()
            if st == "CLOSED":
                if reason == "COMPLETED" and not force_reopen:
                    result["skipped_reopen"] = "already completed"
                    result["ok"] = True
                    return result
                subprocess.run(
                    ["gh", "issue", "reopen", str(issue_number)],
                    capture_output=True, text=True, timeout=10, cwd=str(repo_path),
                )
                result["reopened"] = True
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        out = subprocess.run(
            ["gh", "issue", "edit", str(issue_number),
             "--add-label", "claude-in-progress",
             "--remove-label", "icebox",
             "--add-assignee", "@me"],
            capture_output=True, text=True, timeout=15, cwd=str(repo_path),
        )
        if out.returncode == 0:
            result["ok"] = True
            _core._bust_backlog_issue_cache(repo_path)
            _core._bust_issue_state_cache(repo_path)
        else:
            result["error"] = (out.stderr or out.stdout or "").strip()[:300]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result["error"] = str(e)
    return result


def mark_issue_icebox(issue_number, repo_path):
    """Signal that an issue is parked in the Icebox column:
    - adds the `icebox` label
    - removes `claude-in-progress` since the issue is parked, not being worked

    Mirror of mark_issue_in_progress: each operation adds its own label and
    strips the other so the GitHub state always matches a single column.
    """
    repo_path = _core.resolve_repo_path(repo_path)
    result = {"ok": False, "issue_number": str(issue_number)}
    try:
        out = subprocess.run(
            ["gh", "issue", "edit", str(issue_number),
             "--add-label", "icebox",
             "--remove-label", "claude-in-progress"],
            capture_output=True, text=True, timeout=15, cwd=str(repo_path),
        )
        if out.returncode == 0:
            result["ok"] = True
            _core._bust_backlog_issue_cache(repo_path)
            _core._bust_issue_state_cache(repo_path)
        else:
            result["error"] = (out.stderr or out.stdout or "").strip()[:300]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result["error"] = str(e)
    return result


def remove_in_progress_label(issue_number, repo_path):
    """Strip the claude-in-progress label (ignore if absent)."""
    repo_path = _core.resolve_repo_path(repo_path)
    try:
        subprocess.run(
            ["gh", "issue", "edit", str(issue_number),
             "--remove-label", "claude-in-progress"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        _core._bust_backlog_issue_cache(repo_path)
        _core._bust_issue_state_cache(repo_path)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def close_issue(issue_number, reason, duplicate_of=None, repo_path=None):
    """Close a GitHub issue with the given reason.

    reason ∈ {'completed', 'not planned', 'duplicate'}
    For 'duplicate', we close with reason='not planned' and add a comment
    "Duplicate of #N" (GitHub doesn't have a native 'duplicate' close reason).
    """
    repo_path = _core.resolve_repo_path(repo_path)
    reason = (reason or "").strip().lower()
    result = {"ok": False}
    try:
        if reason == "duplicate":
            if not duplicate_of:
                result["error"] = "duplicate_of is required for duplicate close"
                return result
            dup = str(duplicate_of).lstrip("#")
            comment = f"Duplicate of #{dup}"
            subprocess.run(
                ["gh", "issue", "comment", str(issue_number), "--body", comment],
                check=True, capture_output=True, text=True, cwd=str(repo_path),
            )
            subprocess.run(
                ["gh", "issue", "close", str(issue_number), "--reason", "not planned"],
                check=True, capture_output=True, text=True, cwd=str(repo_path),
            )
            remove_in_progress_label(issue_number, repo_path=repo_path)
            _core._bust_backlog_issue_cache(repo_path)
            result["ok"] = True
            result["comment"] = comment
            return result
        elif reason in ("completed", "not planned"):
            subprocess.run(
                ["gh", "issue", "close", str(issue_number), "--reason", reason],
                check=True, capture_output=True, text=True, cwd=str(repo_path),
            )
            remove_in_progress_label(issue_number, repo_path=repo_path)
            _core._bust_backlog_issue_cache(repo_path)
            result["ok"] = True
            return result
        else:
            result["error"] = f"unknown reason: {reason}"
            return result
    except subprocess.CalledProcessError as e:
        result["error"] = (e.stderr or e.stdout or str(e)).strip()[:300]
        return result


def reply_to_issue(issue_number, body, action, repo_path):
    """Post a comment on a GH issue, then optionally label or close it.

    action ∈ {'comment', 'needs_attention', 'close'}
    """
    repo_path = _core.resolve_repo_path(repo_path)
    result = {"ok": False}
    try:
        if not body or not body.strip():
            result["error"] = "comment body is required"
            return result
        subprocess.run(
            ["gh", "issue", "comment", str(issue_number), "--body", body.strip()],
            check=True, capture_output=True, text=True, cwd=str(repo_path),
        )
        if action == "needs_attention":
            subprocess.run(
                ["gh", "issue", "edit", str(issue_number), "--add-label", "needs-attention"],
                check=True, capture_output=True, text=True, cwd=str(repo_path),
            )
        elif action == "close":
            subprocess.run(
                ["gh", "issue", "close", str(issue_number), "--reason", "completed"],
                check=True, capture_output=True, text=True, cwd=str(repo_path),
            )
            remove_in_progress_label(issue_number, repo_path=repo_path)
        _core._bust_backlog_issue_cache(repo_path)
        result["ok"] = True
        return result
    except subprocess.CalledProcessError as e:
        result["error"] = (e.stderr or e.stdout or str(e)).strip()[:300]
        return result


def get_issue_details(issue_number, repo_path):
    """Return the full GitHub issue (title, body, labels, comments, URL)."""
    data = _gh(
        repo_path,
        "issue", "view", str(issue_number),
        "--json", "title,body,labels,comments,url,author,state,createdAt,updatedAt",
    )
    if not data:
        return {"ok": False, "error": "gh issue view failed"}
    return {"ok": True, "issue": data}


def get_issue_summary(issue_number, repo_path):
    """Get Claude's summary comment from a closed issue."""
    comments = _gh(
        repo_path,
        "issue", "view", str(issue_number),
        "--json", "comments",
        "--jq", ".comments",
    )
    if not comments:
        # Try without jq
        data = _gh(repo_path, "issue", "view", str(issue_number), "--json", "comments,body")
        comments = (data or {}).get("comments", [])

    # Find Claude's closing comment (contains "Fixed and merged" or "Claude Code")
    for c in reversed(comments or []):
        body = c.get("body", "")
        if "Fixed and merged" in body or "Claude Code" in body or "failed" in body.lower():
            return {"summary": body}
    return {"summary": None}
