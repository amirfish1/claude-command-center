"""Extracted from server.py (originally lines 25686-26464).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import hashlib
import json
import subprocess
import threading
import time

from ccc_server import core as _core
from ccc_server import github_quota as _github_quota

# ---------------------------------------------------------------------------
# Cross-repo issues — Phase B of the multi-repo design.
#
# Aggregates `gh issue list` across every known repo (recent ∪ pinned), in
# parallel, with per-repo TTL cache. Lets the UI surface a flat "issues
# across all my work" list without needing the user to switch repos. Each
# returned issue carries its repo_path / repo_label so click-to-spawn can
# target the right cwd. Failures (no gh auth in some folder, missing dir,
# timeout) are isolated per-repo — one bad folder doesn't break the rest.
# ---------------------------------------------------------------------------

_CROSS_REPO_ISSUES_CACHE = {}  # repo_path → {issues, error, ts}
_CROSS_REPO_ISSUES_LOCK = threading.Lock()
_CROSS_REPO_ISSUES_TTL = 300  # 5 minutes — same as the per-repo cache


def _cross_repo_feed_repo_paths():
    """Repos eligible for cross-repo issue/PR feeds.

    `recent-repos.txt` is ordering metadata for the picker, not membership.
    Use load_known_repos() so stale recent paths do not leak into all-repos
    issue and ready-to-merge sections. Restrict to GitHub repos owned by the
    authenticated account (or configured/fallback owner) so cloned external OSS
    repos do not appear in "my repos" feeds.
    """
    try:
        entries = _core.load_known_repos()
    except Exception:
        return []
    owner_candidates = _core._github_owner_login_candidates()
    out = []
    seen = set()
    for entry in entries or []:
        repo = (entry.get("path") if isinstance(entry, dict) else "") or ""
        if not repo:
            continue
        try:
            repo = str(Path(repo).expanduser().resolve())
        except (OSError, ValueError, RuntimeError):
            continue
        if repo in seen:
            continue
        owner = (_core._github_repo_owner_for_path(repo) or "").lower()
        if owner_candidates:
            if not owner or owner not in owner_candidates:
                continue
        elif not owner:
            continue
        seen.add(repo)
        out.append(repo)
    return out


def _fetch_one_repo_issues(repo_path):
    """Fetch open + recently-closed issues for ONE repo. Cached per-repo
    by repo_path. Returns {"issues": list, "error": str|None, "ts": float}.
    Never raises — every failure mode lands in the `error` field so the
    aggregator can degrade gracefully."""
    cache_key = str(repo_path)
    _core._hydrate_gh_cache("cross_repo_issues", _core._CROSS_REPO_ISSUES_CACHE)
    now = time.time()
    with _CROSS_REPO_ISSUES_LOCK:
        cached = _core._CROSS_REPO_ISSUES_CACHE.get(cache_key)
        if cached and (now - cached["ts"]) < _CROSS_REPO_ISSUES_TTL:
            return cached

    # Single-flight (lane W6-1). fetch_cross_repo_issues() fans this out over
    # every known repo in a thread pool, so without a per-repo flight N
    # concurrent dashboard requests on a stale cache cost N x repos x 1.7
    # GraphQL points. Followers re-read the cache the leader just filled.
    value, was_leader = _github_quota.single_flight(
        f"cross-repo-issues|{cache_key}",
        lambda: _fetch_one_repo_issues_uncached(repo_path, cache_key),
    )
    if was_leader and value is not None:
        return value
    with _CROSS_REPO_ISSUES_LOCK:
        cached = _core._CROSS_REPO_ISSUES_CACHE.get(cache_key)
    if cached:
        return cached
    return {"issues": [], "error": "fetch in flight", "ts": now}


def _fetch_one_repo_issues_uncached(repo_path, cache_key):
    """The actual `gh` round-trips for one repo. Only ever entered by the
    single-flight leader in _fetch_one_repo_issues()."""
    now = time.time()
    issues = []
    error = None
    try:
        if not Path(repo_path).is_dir():
            error = "directory not found"
        elif not (Path(repo_path) / ".git").is_dir():
            error = "not a git repo"
        else:
            try:
                open_out = subprocess.run(
                    ["gh", "issue", "list", "--state", "open",
                     "--limit", str(_github_quota.issue_limit_open()),
                     "--json", "number,title,labels,body,createdAt,updatedAt,state,stateReason,url"],
                    capture_output=True, text=True, timeout=10, cwd=str(repo_path),
                )
                if open_out.returncode == 0:
                    issues.extend(json.loads(open_out.stdout))
                else:
                    # Swallow common gh failures (no auth, no remote, etc.)
                    # into an error string. stderr first line is enough.
                    err = (open_out.stderr or "").strip().splitlines()
                    error = (err[0] if err else "gh exited non-zero")[:200]
            except subprocess.TimeoutExpired:
                error = "gh timed out (10s)"
            except FileNotFoundError:
                error = "gh CLI not installed"
            except json.JSONDecodeError:
                error = "gh returned malformed JSON"
            # Only attempt closed if open succeeded — otherwise we'd double-
            # report the same auth error.
            if not error:
                try:
                    closed_out = subprocess.run(
                        ["gh", "issue", "list", "--state", "closed",
                         "--limit", str(_github_quota.issue_limit_closed()),
                         "--json", "number,title,labels,body,createdAt,updatedAt,closedAt,state,stateReason,url"],
                        capture_output=True, text=True, timeout=10, cwd=str(repo_path),
                    )
                    if closed_out.returncode == 0:
                        issues.extend(json.loads(closed_out.stdout))
                    # Closed-failure is non-fatal — open list is the
                    # important one for backlog UX.
                except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
                    pass
    except OSError as e:
        error = f"OSError: {e}"[:200]

    result = {"issues": issues, "error": error, "ts": now}
    with _CROSS_REPO_ISSUES_LOCK:
        _core._CROSS_REPO_ISSUES_CACHE[cache_key] = result
    _core._persist_gh_cache("cross_repo_issues", _core._CROSS_REPO_ISSUES_CACHE)
    return result


def fetch_cross_repo_issues():
    """Walk known repos in parallel; return a tagged flat list + per-repo
    error map.

    Repo set: `_load_recent_repos() ∪ _load_custom_repos()` — same source
    the rail / dropdown uses. Each issue gets `repo_path` + `repo_label`
    grafted on so click-to-spawn knows the cwd; sorted by updatedAt desc.

    Returns:
        {
          "issues": [{...gh fields..., "repo_path", "repo_label"}, ...],
          "errors": {repo_path: error_string},
          "fetched_at": epoch,
        }
    """
    repos = _core._cross_repo_feed_repo_paths()
    if not repos:
        return {"issues": [], "errors": {}, "fetched_at": time.time()}

    out = []
    errors = {}
    # Parallelize: ~10 repos × 1–3s each → 1–3s total instead of 10–30s.
    # 8 workers is enough for typical fleets without spawning a thread storm.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(8, len(repos))) as ex:
        futures = {ex.submit(_fetch_one_repo_issues, r): r for r in repos}
        for f in as_completed(futures):
            repo = futures[f]
            try:
                result = f.result()
            except Exception as e:
                errors[repo] = f"unexpected: {e}"[:200]
                continue
            if result.get("error"):
                errors[repo] = result["error"]
                # Even on error, surface any issues that *did* parse
                # before the error (defensive — shouldn't happen given
                # the early-return shape, but cheap).
            label = Path(repo).name
            for issue in result.get("issues") or []:
                # Tag with repo info so the UI knows where to spawn.
                issue["repo_path"] = repo
                issue["repo_label"] = label
                out.append(issue)

    # Sort by updatedAt desc (fall back to createdAt then 0).
    out.sort(
        key=lambda i: i.get("updatedAt") or i.get("createdAt") or "",
        reverse=True,
    )
    return {"issues": out, "errors": errors, "fetched_at": time.time()}


def _gh_pr_status_notes(pr):
    notes = []
    if pr.get("isDraft"):
        notes.append({"kind": "warning", "label": "draft", "title": "Draft PR"})
    mergeable = (pr.get("mergeable") or "").upper()
    if mergeable == "CONFLICTING":
        notes.append({"kind": "danger", "label": "conflicts", "title": "PR has merge conflicts"})
    elif mergeable in ("UNKNOWN", "BLOCKED"):
        notes.append({
            "kind": "warning",
            "label": mergeable.lower(),
            "title": f"Mergeability is {mergeable.lower()}",
        })
    for check in pr.get("statusCheckRollup") or []:
        name = (check.get("name") or check.get("workflowName") or "check").strip()
        conclusion = (check.get("conclusion") or "").upper()
        if conclusion not in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
            continue
        if "CLA" in name.upper():
            label = "CLA failing"
            title = f"{name} is failing"
        else:
            label = "check failed"
            title = f"{name} failed"
        notes.append({"kind": "danger", "label": label, "title": title})
    return notes


def fetch_cross_repo_prs():
    repos = _core._cross_repo_feed_repo_paths()
    if not repos:
        return {"pull_requests": [], "errors": {}, "fetched_at": time.time()}

    out = []
    errors = {}
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(8, len(repos))) as ex:
        futures = {ex.submit(_core._open_prs_cached, r): r for r in repos}
        for f in as_completed(futures):
            repo = futures[f]
            try:
                prs = f.result()
            except Exception as e:
                errors[repo] = f"unexpected: {e}"[:200]
                continue
            label = Path(repo).name
            for pr in prs or []:
                pr["repo_path"] = repo
                pr["repo_label"] = label
                pr["status_notes"] = _gh_pr_status_notes(pr)
                out.append(pr)
    out.sort(
        key=lambda p: p.get("updatedAt") or p.get("createdAt") or "",
        reverse=True,
    )
    return {"pull_requests": out, "errors": errors, "fetched_at": time.time()}


def _iso_epoch_or_now(ts):
    if not ts:
        return time.time()
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return time.time()


def _open_pr_archive_row(pr):
    repo_path = pr.get("repo_path") or ""
    number = int(pr.get("number") or 0)
    digest = hashlib.sha1(f"{repo_path}:{number}".encode("utf-8")).hexdigest()
    synthetic_id = f"00000000-0000-0000-0000-{digest[:12]}"
    title = pr.get("title") or f"PR #{number}"
    branch = pr.get("headRefName") or ""
    mtime = _iso_epoch_or_now(pr.get("updatedAt") or pr.get("createdAt"))
    return {
        "id": synthetic_id,
        "session_id": synthetic_id,
        "source": "github_pr",
        "engine": "github",
        "timestamp": "",
        "first_message": title,
        "display_name": f"#{number}: {title}",
        "name_overridden": False,
        "mtime": mtime,
        "size": 0,
        "is_live": False,
        "archived": False,
        "worktree_dirty": False,
        "has_commit": False,
        "has_push": True,
        "has_edit": False,
        "tail_pr_number": number,
        "tail_pr_url": pr.get("url") or "",
        "pr_state": "OPEN",
        "pr_is_draft": bool(pr.get("isDraft")),
        "pr_mergeable": pr.get("mergeable") or "",
        "pr_review_decision": pr.get("reviewDecision") or "",
        "pr_notes": pr.get("status_notes") or [],
        "sidecar_status": None,
        "sidecar_tool": None,
        "sidecar_file": None,
        "sidecar_ts": 0,
        "sidecar_in_flight": False,
        "sidecar_has_writes": False,
        "needs_approval": False,
        "needs_approval_message": "",
        "question_waiting": False,
        "question_text": "",
        "question_header": "",
        "question_preamble": "",
        "question_options": [],
        "question_option_details": [],
        "session_cwd": repo_path,
        "session_cwd_exists": Path(repo_path).is_dir() if repo_path else False,
        "session_cwd_is_worktree": False,
        "git_branch": branch,
        "branch": branch,
        "effective_branch": branch,
        "effective_kind": None,
        "folder_label": pr.get("repo_label") or (Path(repo_path).name if repo_path else "GitHub"),
        "folder_path": repo_path,
        "slug": _core._encode_project_slug(repo_path) if repo_path else "github-pr",
        "pinned_repo": False,
    }


def conversations_with_open_prs(convs):
    convs = list(convs or [])
    open_prs = _core.fetch_cross_repo_prs().get("pull_requests") or []
    pr_by_url = {
        str(pr.get("url") or ""): pr
        for pr in open_prs
        if pr.get("url")
    }
    pr_by_key = {}
    for pr in open_prs:
        try:
            n = int(pr.get("number") or 0)
        except (TypeError, ValueError):
            n = 0
        repo_path = pr.get("repo_path") or ""
        if n and repo_path:
            pr_by_key[(repo_path, n)] = pr

    seen_urls = {
        str(c.get("tail_pr_url") or "")
        for c in convs
        if c.get("tail_pr_url")
    }
    seen_keys = set()
    for c in convs:
        try:
            n = int(c.get("tail_pr_number") or 0)
        except (TypeError, ValueError):
            n = 0
        repo_path = c.get("folder_path") or c.get("session_cwd") or ""
        if n and repo_path:
            seen_keys.add((repo_path, n))
        pr = pr_by_url.get(str(c.get("tail_pr_url") or "")) or pr_by_key.get((repo_path, n))
        if pr:
            c["pr_notes"] = pr.get("status_notes") or []
            c["pr_is_draft"] = bool(pr.get("isDraft"))
            c["pr_mergeable"] = pr.get("mergeable") or ""
            c["pr_review_decision"] = pr.get("reviewDecision") or ""

    for pr in open_prs:
        try:
            number = int(pr.get("number") or 0)
        except (TypeError, ValueError):
            number = 0
        if not number:
            continue
        repo_path = pr.get("repo_path") or ""
        url = pr.get("url") or ""
        if (url and url in seen_urls) or ((repo_path, number) in seen_keys):
            continue
        convs.append(_open_pr_archive_row(pr))
    convs.sort(key=lambda c: c.get("mtime") or c.get("modified") or 0, reverse=True)
    return convs


def _bust_backlog_issue_cache(repo_path=None):
    # See _bust_issue_state_cache: claim hydration first so a bust can't be
    # undone by a later disk read re-adding the pre-mutation entry.
    with _core._GH_CACHE_LOCK:
        _core._GH_CACHE_HYDRATED.add("backlog_issues")
    if repo_path:
        try:
            _core._backlog_issues_cache.pop(_core.resolve_repo_path(repo_path), None)
        except _core.RepoContextError:
            pass
    else:
        _core._backlog_issues_cache.clear()
    _core._persist_gh_cache("backlog_issues", _core._backlog_issues_cache)


def _fetch_backlog_issues(repo_path, _blocking=False):
    """Fetch open + recently-closed GitHub issues with labels and body.

    Cached 5 minutes and disk-backed: stale entries serve immediately with a
    background refresh behind them, so a dashboard restart does not block the
    first session render on two `gh issue list` round-trips.

    Closed issues get a `state_reason` field so the UI can route them
    (completed -> Verified, not planned -> Archived).
    """
    repo_path = _core.resolve_repo_path(repo_path)
    _core._hydrate_gh_cache("backlog_issues", _core._backlog_issues_cache)
    cached = _core._backlog_issues_cache.get(repo_path) or {}
    if time.time() - cached.get("ts", 0) < 300 and cached.get("data") is not None:
        return cached.get("data") or []
    if cached.get("data") and not _blocking:
        _core._refresh_gh_cache_async(
            "backlog_issues", repo_path,
            lambda: _fetch_backlog_issues(repo_path, _blocking=True),
        )
        return cached.get("data") or []
    merged = []
    try:
        open_out = subprocess.run(
            ["gh", "issue", "list", "--state", "open",
             "--limit", str(_github_quota.issue_limit_open()),
             "--json", "number,title,labels,body,createdAt,updatedAt,state,stateReason"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        if open_out.returncode == 0:
            merged.extend(json.loads(open_out.stdout))
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        closed_out = subprocess.run(
            ["gh", "issue", "list", "--state", "closed",
             "--limit", str(_github_quota.issue_limit_closed()),
             "--json", "number,title,labels,body,createdAt,updatedAt,closedAt,state,stateReason"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        if closed_out.returncode == 0:
            merged.extend(json.loads(closed_out.stdout))
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    _core._backlog_issues_cache[repo_path] = {"ts": time.time(), "data": merged}
    _core._persist_gh_cache("backlog_issues", _core._backlog_issues_cache)
    return merged


def _parse_todo_md(repo_path):
    """Parse TODO.md for unchecked items (- [ ] lines)."""
    todo_path = Path(repo_path) / "TODO.md"
    items = []
    try:
        with open(todo_path, "r") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("- [ ]"):
                    text = stripped[5:].strip()
                    if text:
                        items.append(text)
    except (OSError, UnicodeDecodeError):
        pass
    return items


def _load_native_tasks():
    """Surface Claude Code's built-in TodoWrite output as backlog records.

    Claude Code persists per-session todos to ``~/.claude/tasks/<session_id>/<task_id>.json``.
    Each file is one task with shape ``{id, subject, description, activeForm,
    status, blocks, blockedBy}`` — ``status`` is one of ``pending``,
    ``in_progress``, ``completed``.

    To avoid spamming the kanban (a session with 6 todos shouldn't add 6 cards)
    we collapse each session_id to a single record:
      - Title prefers the in_progress task's ``subject``; falls back to first
        pending; otherwise the most recent completed (so finished sessions still
        show *what* they did).
      - Counts (``total``, ``in_progress_count``, ``pending_count``,
        ``completed_count``) are returned so the UI can show e.g. "3/6".
      - ``modified`` is the dir mtime so the card sorts by last-touched session.

    Sessions with zero parseable task files are skipped entirely.
    Files that aren't valid JSON objects are skipped without aborting the
    session record (one bad task shouldn't hide the rest).
    """
    tasks_root = Path.home() / ".claude" / "tasks"
    if not tasks_root.is_dir():
        return []
    records = []
    try:
        session_dirs = [d for d in tasks_root.iterdir() if d.is_dir()]
    except OSError:
        return []
    for sdir in session_dirs:
        session_id = sdir.name
        in_progress = []
        pending = []
        completed = []
        try:
            files = [f for f in sdir.iterdir() if f.is_file() and f.suffix == ".json"]
        except OSError:
            continue
        for tf in files:
            try:
                with open(tf, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            # Schema is a single task object; tolerate the legacy "list of tasks"
            # form too, in case some Claude Code versions wrote arrays.
            if isinstance(raw, list):
                tasks = [t for t in raw if isinstance(t, dict)]
            elif isinstance(raw, dict):
                tasks = [raw]
            else:
                continue
            for task in tasks:
                status = (task.get("status") or "").lower()
                if status == "in_progress":
                    in_progress.append((tf.stat().st_mtime, task))
                elif status == "pending":
                    pending.append((tf.stat().st_mtime, task))
                elif status == "completed":
                    completed.append((tf.stat().st_mtime, task))
        total = len(in_progress) + len(pending) + len(completed)
        if total == 0:
            continue
        # Pick the headline task: in_progress > pending > most-recent completed
        if in_progress:
            headline = max(in_progress, key=lambda x: x[0])[1]
            headline_status = "in_progress"
        elif pending:
            headline = min(pending, key=lambda x: x[0])[1]
            headline_status = "pending"
        else:
            headline = max(completed, key=lambda x: x[0])[1]
            headline_status = "completed"
        title = (headline.get("subject") or headline.get("activeForm")
                 or headline.get("content") or "").strip()
        if not title:
            continue
        try:
            mtime = sdir.stat().st_mtime
        except OSError:
            mtime = 0
        records.append({
            "session_id": session_id,
            "title": title,
            "active_form": (headline.get("activeForm") or "").strip(),
            "description": (headline.get("description") or "").strip(),
            "status": headline_status,
            "in_progress_count": len(in_progress),
            "pending_count": len(pending),
            "completed_count": len(completed),
            "total": total,
            "modified": mtime,
            "source": "native_task",
        })
    return records


def _parse_parking_lot_md(repo_path):
    """Parse PARKING_LOT.md for `## heading` items; body = text until the next
    heading or `---` separator. Returns [{title, body}] in file order."""
    # Case-insensitive filename match for the two common spellings
    repo = Path(repo_path)
    candidates = [repo / "PARKING_LOT.md", repo / "parking-lot.md", repo / "parking_lot.md"]
    path = next((p for p in candidates if p.is_file()), None)
    if not path:
        return []
    items = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    current_title = None
    current_body = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_title:
                items.append({"title": current_title, "body": "\n".join(current_body).strip()})
            current_title = line[3:].strip()
            current_body = []
            continue
        # `---` is a section separator — flush the current item but don't start a new one
        if line.strip() == "---":
            if current_title:
                items.append({"title": current_title, "body": "\n".join(current_body).strip()})
                current_title = None
                current_body = []
            continue
        if current_title is not None:
            current_body.append(line)
    if current_title:
        items.append({"title": current_title, "body": "\n".join(current_body).strip()})
    return items


def find_backlog_items(repo_path, progress=None):
    """Return backlog cards from GitHub issues + TODO.md."""
    repo_path = _core.resolve_repo_path(repo_path)
    items = []

    # Source 1: GitHub Issues
    if progress:
        progress("github", state="running", detail="Querying open and recently closed issues.")
    backlog_issues = _fetch_backlog_issues(repo_path)
    if progress:
        progress(
            "github",
            state="done",
            count=len(backlog_issues),
            detail=f"{len(backlog_issues)} GitHub issue(s) fetched.",
        )
    for issue in backlog_issues:
        number = issue.get("number", 0)
        title = issue.get("title", "")
        body = issue.get("body", "") or ""
        labels = [l.get("name", "") for l in (issue.get("labels") or [])]
        # Parse createdAt ISO 8601 → unix timestamp
        created_ts = 0
        created_at = issue.get("createdAt", "")
        if created_at:
            try:
                from datetime import datetime, timezone
                # Format: "2026-04-12T05:39:47Z" — UTC
                dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                created_ts = dt.timestamp()
            except (ValueError, ImportError):
                pass
        state = (issue.get("state") or "OPEN").upper()
        reason = (issue.get("stateReason") or "").upper()  # COMPLETED, NOT_PLANNED, DUPLICATE, ""
        # AI-summary override — if the user has hit the ✨ button on this
        # issue we use the cached short title instead of the verbose GH one.
        ai_overrides = _core._load_issue_title_overrides()
        ai_entry = ai_overrides.get(str(number))
        ai_title = (ai_entry or {}).get("title")
        display_name = f"#{number}: {ai_title or title}"
        items.append({
            "id": f"backlog-issue-{number}",
            "session_id": f"backlog-issue-{number}",
            "display_name": display_name,
            "first_message": body[:200],
            # name_overridden=True signals the bulk button to skip on rerun
            # (same semantics as session-card cards).
            "name_overridden": bool(ai_title),
            "source": "backlog",
            "backlog_type": "github",
            "issue_number": str(number),
            "issue_labels": labels,
            "issue_created_at": created_at,
            "issue_state": state,
            "issue_state_reason": reason,
            "org": _core._detect_issue_org(body),
            "modified": created_ts,
            "size": 0,
            "branch": "",
            "is_live": False,
            "archived": False,
            "verified": False,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "sidecar_status": None,
            "sidecar_tool": None,
            "sidecar_file": None,
            "sidecar_has_writes": False,
            "sidecar_ts": 0,
        })

    # Source 2: TODO.md
    todo_items = _parse_todo_md(repo_path)
    if progress:
        progress(
            "todo",
            state="done",
            count=len(todo_items),
            detail=f"{len(todo_items)} unchecked TODO item(s).",
        )
    for i, text in enumerate(todo_items):
        items.append({
            "id": f"backlog-todo-{i}",
            "session_id": f"backlog-todo-{i}",
            "display_name": text[:80],
            "first_message": text,
            "source": "backlog",
            "backlog_type": "todo",
            "issue_number": "",
            "issue_labels": [],
            "modified": 0,
            "size": 0,
            "branch": "",
            "is_live": False,
            "archived": False,
            "verified": False,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "sidecar_status": None,
            "sidecar_tool": None,
            "sidecar_file": None,
            "sidecar_has_writes": False,
            "sidecar_ts": 0,
            "name_overridden": False,
        })

    # Source 3: PARKING_LOT.md — richer items (heading + body)
    parking_items = _parse_parking_lot_md(repo_path)
    if progress:
        progress(
            "parking",
            state="done",
            count=len(parking_items),
            detail=f"{len(parking_items)} parking-lot item(s).",
        )
    for i, it in enumerate(parking_items):
        title = it["title"]
        body = it["body"]
        items.append({
            "id": f"backlog-parking-{i}",
            "session_id": f"backlog-parking-{i}",
            "display_name": title[:120],
            "first_message": (title + "\n\n" + body) if body else title,
            "source": "backlog",
            "backlog_type": "parking",
            "issue_number": "",
            "issue_labels": [],
            "modified": 0,
            "size": 0,
            "branch": "",
            "is_live": False,
            "archived": False,
            "verified": False,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "sidecar_status": None,
            "sidecar_tool": None,
            "sidecar_file": None,
            "sidecar_has_writes": False,
            "sidecar_ts": 0,
            "name_overridden": False,
        })

    # Source 4: ~/.claude/tasks/<session_id>/*.json (native TodoWrite output)
    # Only surfaces sessions that aren't already represented as a live/inactive
    # conversation — that filtering happens at the `/api/sessions` merge step,
    # so here we just emit candidate cards.
    native_tasks = _load_native_tasks()
    if progress:
        progress(
            "native_tasks",
            state="done",
            count=len(native_tasks),
            detail=f"{len(native_tasks)} native task session(s).",
        )
    for nt in native_tasks:
        # Pad short subjects with the activeForm so the card body has signal.
        body_bits = [nt["title"]]
        if nt.get("description"):
            body_bits.append(nt["description"])
        if nt.get("active_form") and nt["active_form"] != nt["title"]:
            body_bits.append(nt["active_form"])
        body = "\n\n".join(b for b in body_bits if b)
        items.append({
            "id": f"backlog-task-{nt['session_id']}",
            "session_id": nt["session_id"],
            "display_name": nt["title"][:120],
            "first_message": body[:400],
            "source": "backlog",
            "backlog_type": "native_task",
            "issue_number": "",
            "issue_labels": [],
            "modified": nt.get("modified") or 0,
            "size": 0,
            "branch": "",
            "is_live": False,
            "archived": False,
            "verified": False,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "sidecar_status": None,
            "sidecar_tool": None,
            "sidecar_file": None,
            "sidecar_has_writes": False,
            "sidecar_ts": 0,
            "name_overridden": False,
            # Native-task-specific fields
            "task_status": nt["status"],
            "task_total": nt["total"],
            "task_in_progress": nt["in_progress_count"],
            "task_pending": nt["pending_count"],
            "task_completed": nt["completed_count"],
        })

    return items


