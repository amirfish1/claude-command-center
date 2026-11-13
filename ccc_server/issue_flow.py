# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""GitHub issue auto-fix flow: backend for the setup wizard.

The flow the wizard builds:

    your product  ->  GitHub issue  ->  WatchTower queue  ->  agent  ->  issue closed

Nothing here is a new engine. A WatchTower queue with ``backend: github``
already treats a repo's issues as its tickets; this module only answers the
questions a person setting that up for the first time has to ask ("is gh
signed in?", "how many issues are already open?", "did my test issue get
picked up?") and does the two GitHub writes the wizard needs: labels and one
test issue.

Two modes, because a queue that drains every issue is not always safe:

``labeled``  auto-drain stays off; only issues carrying ``watchtower:play``
             run. GitHub lets only people with write access add labels, so a
             stranger opening an issue on a public repo cannot start an agent.
``all``      auto-drain on; every open issue in the repo is worked after the
             queue's grace period.

HTTP surface (wired in server.py):

    GET  /api/issue-flow/status              readiness + known repos + flows
    GET  /api/issue-flow/repo?github_repo=   one repo: visibility, open issues
    POST /api/issue-flow/prepare             labels (and hold existing issues)
    POST /api/issue-flow/test                open the test issue
    GET  /api/issue-flow/test?queue=&number= where the test issue is now

Stdlib-only. Every ``gh`` call is made on an explicit wizard action; nothing
here runs per row or on a timer.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# WatchTower's own label names (watchtower/github_backend.py). Colours and
# descriptions match what WatchTower creates, so whichever side makes the
# label first, the repo ends up looking the same.
RUN_LABEL = "watchtower:play"
HOLD_LABEL = "watchtower:no-auto-drain"
_LABEL_META = {
    RUN_LABEL: ("0e8a16", "WatchTower: run this ticket now"),
    HOLD_LABEL: ("b60205", "WatchTower: never auto-drain this ticket"),
}
QUEUE_LABEL_PREFIX = "watchtower:"
CATCH_ALL_LABEL = "*"
DEFAULT_GRACE_S = 180

MODES = ("labeled", "all")
# Holding back existing issues labels each one. Past this many the wizard
# says so and points at labeled mode instead of running for minutes.
HOLD_MAX = 200
_HOLD_CHUNK = 40

TEST_TITLE = "[CCC test] Pipeline check, safe to close"
TEST_BODY = (
    "This issue was opened by the Claude Command Center setup wizard to prove "
    "the path from a GitHub issue to a WatchTower queue to an agent.\n\n"
    "**Task for the agent:** make no code changes and commit nothing. Close "
    "this ticket with the summary `Pipeline test passed`.\n\n"
    "Safe to close or delete at any time."
)

_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_QUEUE_RE = re.compile(r"^[A-Z0-9_-]{1,64}$")
_GH_TIMEOUT_S = 25


class FlowError(ValueError):
    """A problem the wizard should show the user as-is."""


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def parse_github_slug(value):
    """``owner/repo`` from a slug or any common GitHub remote URL, else ''."""
    text = str(value or "").strip()
    if not text:
        return ""
    # A pasted browser URL often points deeper (/issues/12, /tree/main):
    # only the first two path segments name the repo.
    match = re.search(r"github\.com[:/]+([^/\s:]+)/([^/\s?#]+)", text)
    slug = f"{match.group(1)}/{match.group(2)}" if match else text.rstrip("/")
    if slug.endswith(".git"):
        slug = slug[:-4]
    if not _SLUG_RE.fullmatch(slug):
        return ""
    if slug.lower() in ("owner/repo", "acme/repo"):
        return ""
    return slug


def suggest_queue_name(github_repo, taken=()):
    """A queue name from the repo name that no existing queue uses."""
    repo = str(github_repo or "").rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Z0-9]+", "-", repo.upper()).strip("-")[:40] or "ISSUES"
    used = {str(name).strip().upper() for name in taken}
    if base not in used:
        return base
    for suffix in ("ISSUES", "FIXES"):
        candidate = f"{base}-{suffix}"
        if candidate not in used:
            return candidate
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    return f"{base}-{n}"


def recommended_mode(private, open_issues):
    """``labeled`` unless the repo is private and empty of open issues.

    A public repo takes issues from anyone, and existing open issues would
    all be picked up at once; ``labeled`` is the mode where neither happens.
    """
    return "all" if private and not open_issues else "labeled"


def repo_queues(all_confs, github_repo):
    """Every github-backed queue configured against ``github_repo``.

    Archived queues count. WatchTower decides whether a repo is shared from
    the same list (``config.github_queues_for_repo``), and a shared repo's
    issues are divided by label: an archived sibling is enough to make an
    unlabelled issue belong to nobody. The wizard has to see the repo the
    way WatchTower does, or its test issue is never picked up.
    """
    target = str(github_repo or "").strip().lower()
    if not target:
        return []
    return sorted(
        str(name) for name, conf in (all_confs or {}).items()
        if isinstance(conf, dict)
        and str(conf.get("backend") or "").strip().lower() == "github"
        and str(conf.get("github_repo") or "").strip().lower() == target
    )


def membership_label(queue, conf, all_confs):
    """The label that routes an issue to ``queue``, or '' when none is needed.

    None is needed when the queue has its repo to itself, or when it is the
    repo's catch-all (``queue_label: "*"``), which owns every issue no
    sibling claimed.
    """
    conf = conf if isinstance(conf, dict) else {}
    if len(repo_queues(all_confs, conf.get("github_repo"))) < 2:
        return ""
    label = str(conf.get("queue_label") or "").strip() or f"{QUEUE_LABEL_PREFIX}{queue}"
    return "" if label == CATCH_ALL_LABEL else label


def issue_labels(queue, conf, all_confs):
    """Labels an issue needs to be run by ``queue``.

    The run label when the queue is not auto-draining, plus the queue's
    membership label when its repo backs more than one queue.
    """
    conf = conf if isinstance(conf, dict) else {}
    labels = []
    if not conf.get("auto_drain"):
        labels.append(RUN_LABEL)
    member = membership_label(queue, conf, all_confs)
    if member:
        labels.append(member)
    return labels


def _parse_iso(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def derive_stage(item, *, auto_drain, grace_s, now=None):
    """Where a test issue is in the pipeline, from its queue row.

    Returns ``{"stage": ..., ...}`` with stage one of:
      sent     on GitHub, the queue has not listed it yet
      grace    in the queue, inside the auto-drain grace period
      queued   in the queue and eligible; waiting for a worker
      parked   in the queue but nothing will start it
      working  an agent has claimed it
      blocked  the agent asked a question
      done     closed
    """
    if not item:
        return {"stage": "sent"}
    now = time.time() if now is None else now
    status = str(item.get("status") or "")
    resolution = item.get("resolution") if isinstance(item.get("resolution"), dict) else {}
    out = {
        "ref": str(item.get("ref") or ""),
        "session_id": str(item.get("claimed_session_id")
                          or resolution.get("session_id") or ""),
        "worker": str(item.get("claimed_by") or ""),
    }
    if status == "closed":
        out.update(stage="done", summary=str(resolution.get("summary") or ""))
        return out
    if item.get("needs_input") or status == "awaiting_answer":
        out.update(stage="blocked", question=str(item.get("block_question") or ""))
        return out
    if status == "in_progress":
        out["stage"] = "working"
        return out
    if item.get("run_requested") or item.get("auto_eligible") or item.get("work_it"):
        out["stage"] = "queued"
        return out
    if auto_drain and not item.get("no_auto_drain"):
        created = _parse_iso(item.get("created_at"))
        remaining = int(grace_s) - (now - created) if created else 0
        if remaining > 0:
            out.update(stage="grace", grace_remaining_s=int(remaining + 0.999))
        else:
            # Past grace; the next list refresh will mark it eligible.
            out["stage"] = "queued"
        return out
    out["stage"] = "parked"
    out["reason"] = (
        f"it carries the {HOLD_LABEL} label" if item.get("no_auto_drain")
        else f"auto-drain is off and it has no {RUN_LABEL} label"
    )
    return out


# --------------------------------------------------------------------------
# Local repo discovery (file reads only)
# --------------------------------------------------------------------------

_ORIGIN_CACHE = {}
_ORIGIN_LOCK = threading.Lock()


def _git_config_path(repo_path):
    """The config file holding this checkout's remotes.

    A linked worktree's ``.git`` is a file pointing at
    ``<main>/.git/worktrees/<name>``; its remotes live in the main repo's
    config, named by that directory's ``commondir``.
    """
    dot_git = Path(repo_path) / ".git"
    if dot_git.is_dir():
        return dot_git / "config"
    try:
        text = dot_git.read_text(errors="replace")
    except OSError:
        return None
    match = re.search(r"^gitdir:\s*(.+)$", text, re.M)
    if not match:
        return None
    gitdir = Path(match.group(1).strip())
    if not gitdir.is_absolute():
        gitdir = (Path(repo_path) / gitdir).resolve()
    try:
        common = (gitdir / "commondir").read_text().strip()
    except OSError:
        return gitdir / "config"
    common_dir = Path(common)
    if not common_dir.is_absolute():
        common_dir = (gitdir / common_dir).resolve()
    return common_dir / "config"


def origin_slug(repo_path):
    """``owner/repo`` of a checkout's GitHub ``origin``, else ''.

    Parses the git config file directly: listing known repos must not fork a
    ``git`` per row. Cached on the config file's (mtime, size).
    """
    path = _git_config_path(repo_path)
    if path is None:
        return ""
    try:
        st = path.stat()
    except OSError:
        return ""
    key = str(path)
    stamp = (st.st_mtime, st.st_size)
    with _ORIGIN_LOCK:
        hit = _ORIGIN_CACHE.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    slug = ""
    try:
        section = ""
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("["):
                section = line
                continue
            if section.replace(" ", "") == '[remote"origin"]' and line.startswith("url"):
                slug = parse_github_slug(line.split("=", 1)[-1])
                break
    except OSError:
        slug = ""
    with _ORIGIN_LOCK:
        if len(_ORIGIN_CACHE) > 512:
            _ORIGIN_CACHE.clear()
        _ORIGIN_CACHE[key] = (stamp, slug)
    return slug


def inspect_repo_path(repo_path, github_repo=""):
    """Is this a usable local checkout for ``github_repo``?"""
    raw = str(repo_path or "").strip()
    if not raw:
        return {"path": "", "exists": False, "is_git": False, "origin": "", "matches": False}
    path = Path(raw).expanduser()
    exists = path.is_dir()
    is_git = exists and (path / ".git").exists()
    origin = origin_slug(path) if is_git else ""
    wanted = str(github_repo or "").lower()
    return {
        "path": str(path),
        "exists": exists,
        "is_git": is_git,
        "origin": origin,
        "matches": bool(origin and wanted and origin.lower() == wanted),
    }


def known_repos(paths, limit=80):
    """Local checkouts with a GitHub origin, for the repo picker."""
    out, seen = [], set()
    for raw in paths or ():
        path = str(raw or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        slug = origin_slug(path)
        if not slug:
            continue
        out.append({"path": path, "name": Path(path).name, "github_repo": slug})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# gh
# --------------------------------------------------------------------------

def _gh_bin():
    return shutil.which("gh")


def _gh(args, timeout=_GH_TIMEOUT_S):
    """Run gh; returns (returncode, stdout, stderr). Never raises."""
    binary = _gh_bin()
    if not binary:
        return 127, "", "gh is not installed"
    try:
        proc = subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"gh timed out after {timeout}s"
    except OSError as exc:
        return 126, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _gh_error(stderr, fallback):
    lines = [line.strip() for line in str(stderr or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else fallback


_AUTH_CACHE = {"at": 0.0, "value": None}
_AUTH_OK_TTL_S = 60.0


def gh_account(refresh=False):
    """{"installed", "authed", "login"}. A good answer is cached briefly; a
    bad one is never cached, so "Check again" after ``gh auth login`` works."""
    now = time.time()
    cached = _AUTH_CACHE["value"]
    if not refresh and cached and now - _AUTH_CACHE["at"] < _AUTH_OK_TTL_S:
        return dict(cached)
    if not _gh_bin():
        return {"installed": False, "authed": False, "login": ""}
    rc, out, _err = _gh(["api", "user", "--jq", ".login"], timeout=15)
    login = out.strip() if rc == 0 else ""
    value = {"installed": True, "authed": bool(login), "login": login}
    if login:
        _AUTH_CACHE.update(at=now, value=dict(value))
    else:
        _AUTH_CACHE.update(at=0.0, value=None)
    return value


# --------------------------------------------------------------------------
# Wizard operations
# --------------------------------------------------------------------------

def _github_queues(cfg):
    out = {}
    for name, conf in (cfg or {}).items():
        if (isinstance(conf, dict) and str(conf.get("backend") or "") == "github"
                and conf.get("github_repo") and not conf.get("archived")):
            out[str(name)] = conf
    return out


def _queue_conf(cfg, queue):
    wanted = str(queue or "").strip().upper()
    if not _QUEUE_RE.fullmatch(wanted):
        raise FlowError("queue name is required")
    for name, conf in (cfg or {}).items():
        if str(name).strip().upper() == wanted and isinstance(conf, dict):
            if str(conf.get("backend") or "") != "github" or not conf.get("github_repo"):
                raise FlowError(f"{name} is not a GitHub-backed queue")
            return str(name), conf
    raise FlowError(f"queue {wanted} does not exist yet")


def _flow_row(name, conf, cfg):
    return {
        "queue": str(name).upper(),
        "github_repo": str(conf.get("github_repo") or ""),
        "repo_path": str(conf.get("repo_path") or ""),
        "auto_drain": bool(conf.get("auto_drain")),
        "workers": int(conf.get("desired_workers") or 0),
        "mode": "all" if conf.get("auto_drain") else "labeled",
        "grace_s": _grace_s(conf),
        # What an issue must carry for this queue to run it; the wizard
        # puts these in the code it hands the user.
        "labels": issue_labels(str(name).upper(), conf, cfg),
    }


def _grace_s(conf):
    try:
        value = int(conf.get("grace_s", DEFAULT_GRACE_S))
    except (TypeError, ValueError):
        return DEFAULT_GRACE_S
    return value if value >= 0 else DEFAULT_GRACE_S


def status(core, refresh=False):
    """Readiness for the wizard's first screen."""
    account = gh_account(refresh=refresh)
    try:
        wt = core._watchtower_service_status(probe_api=False)
    except Exception:
        wt = {}
    watchtower = {
        "installed": bool(wt.get("installed")),
        "running": bool(wt.get("running")),
    }
    try:
        from ccc_server import repo_paths
        paths = repo_paths._known_repo_paths()
    except Exception:
        paths = []
    cfg = core._wt_read_config() or {}
    return {
        "ok": True,
        "gh": account,
        "watchtower": watchtower,
        "ready": bool(account["authed"] and watchtower["running"]),
        "repos": known_repos(paths),
        "flows": sorted((_flow_row(n, c, cfg) for n, c in _github_queues(cfg).items()),
                        key=lambda row: row["queue"]),
        "labels": {"run": RUN_LABEL, "hold": HOLD_LABEL},
        "hold_max": HOLD_MAX,
    }


def repo_info(core, github_repo, repo_path=""):
    """Everything the wizard shows about one repo before creating a queue."""
    slug = parse_github_slug(github_repo)
    if not slug:
        raise FlowError("Enter the repository as owner/repo")
    rc, out, err = _gh([
        "repo", "view", slug, "--json",
        "nameWithOwner,isPrivate,viewerPermission,url,hasIssuesEnabled,issues",
    ])
    if rc != 0:
        raise FlowError(_gh_error(err, f"Could not read {slug} from GitHub"))
    try:
        data = json.loads(out)
    except ValueError:
        raise FlowError(f"Unexpected answer from GitHub for {slug}")
    slug = str(data.get("nameWithOwner") or slug)
    private = bool(data.get("isPrivate"))
    open_issues = int((data.get("issues") or {}).get("totalCount") or 0)
    permission = str(data.get("viewerPermission") or "").upper()
    cfg = core._wt_read_config() or {}
    existing = sorted(
        name.upper() for name, conf in _github_queues(cfg).items()
        if str(conf.get("github_repo") or "").lower() == slug.lower()
    )
    # Queues on this repo that are not live (archived). They cannot be used,
    # but WatchTower still counts them when deciding the repo is shared, so
    # a new queue has to be the repo's catch-all to receive plain issues.
    dormant = [name for name in repo_queues(cfg, slug) if name.upper() not in existing]
    catch_all_taken = any(
        str((cfg.get(name) or {}).get("queue_label") or "").strip() == CATCH_ALL_LABEL
        for name in dormant
    )
    local = inspect_repo_path(repo_path, slug)
    if not local["path"]:
        match = next((row for row in known_repos(_safe_known_paths())
                      if row["github_repo"].lower() == slug.lower()), None)
        if match:
            local = inspect_repo_path(match["path"], slug)
    return {
        "ok": True,
        "github_repo": slug,
        "url": str(data.get("url") or f"https://github.com/{slug}"),
        "private": private,
        "permission": permission,
        "can_write": permission in ("ADMIN", "MAINTAIN", "WRITE"),
        "issues_enabled": bool(data.get("hasIssuesEnabled")),
        "open_issues": open_issues,
        "existing_queues": existing,
        "suggested_queue": suggest_queue_name(slug, cfg.keys()),
        # Pass this through as the new queue's queue_label ('' = leave unset).
        "queue_label": CATCH_ALL_LABEL if dormant and not catch_all_taken else "",
        "dormant_queues": sorted(name.upper() for name in dormant),
        # Saving a queue replaces its whole config, so the wizard must never
        # post a name that is already in use.
        "taken_queues": sorted(str(name).strip().upper() for name in cfg),
        "recommended_mode": recommended_mode(private, open_issues),
        "local": local,
        "hold_max": HOLD_MAX,
    }


def _safe_known_paths():
    try:
        from ccc_server import repo_paths
        return repo_paths._known_repo_paths()
    except Exception:
        return []


def _ensure_label(repo, name):
    """Create ``name`` on ``repo`` if missing. True when it was created.

    ``gh issue create --label`` fails outright on a label the repo does not
    have, so every label an issue is about to carry goes through here.
    """
    color, description = _LABEL_META.get(
        name, ("5319e7", "WatchTower: routes this ticket to its queue"))
    rc, _out, err = _gh([
        "label", "create", name, "--repo", repo,
        "--color", color, "--description", description,
    ])
    if rc == 0:
        return True
    if "already exists" in err.lower():
        return False
    raise FlowError(_gh_error(err, f"Could not create the {name} label on {repo}"))


def prepare(core, queue, mode, hold_existing=False):
    """Make the repo ready for ``mode`` before the queue starts draining.

    ``labeled``: make sure the run label exists, so the product's first issue
    can carry it. ``all`` with ``hold_existing``: mark every issue that is
    open right now so auto-drain leaves it alone. Called while auto-drain is
    still off; the caller turns it on only after this returns ok.
    """
    if mode not in MODES:
        raise FlowError("mode must be labeled or all")
    name, conf = _queue_conf(core._wt_read_config() or {}, queue)
    repo = str(conf["github_repo"])
    created, held = [], 0
    member = membership_label(name, conf, core._wt_read_config() or {})
    if member and _ensure_label(repo, member):
        created.append(member)
    if mode == "labeled":
        if _ensure_label(repo, RUN_LABEL):
            created.append(RUN_LABEL)
    elif hold_existing:
        rc, out, err = _gh([
            "issue", "list", "--repo", repo, "--state", "open",
            "--limit", str(HOLD_MAX + 1), "--json", "number,labels",
        ])
        if rc != 0:
            raise FlowError(_gh_error(err, f"Could not list open issues on {repo}"))
        try:
            issues = json.loads(out) or []
        except ValueError:
            raise FlowError(f"Unexpected answer from GitHub for {repo}")
        if len(issues) > HOLD_MAX:
            raise FlowError(
                f"{repo} has more than {HOLD_MAX} open issues. Use "
                f"\"Only issues my product tags\" instead."
            )
        numbers = [
            str(issue.get("number")) for issue in issues
            if issue.get("number") and HOLD_LABEL not in {
                str((label or {}).get("name") or "") for label in (issue.get("labels") or [])
            }
        ]
        if numbers:
            if _ensure_label(repo, HOLD_LABEL):
                created.append(HOLD_LABEL)
            for start in range(0, len(numbers), _HOLD_CHUNK):
                chunk = numbers[start:start + _HOLD_CHUNK]
                rc, _out, err = _gh(
                    ["issue", "edit", *chunk, "--repo", repo, "--add-label", HOLD_LABEL],
                    timeout=120,
                )
                if rc != 0:
                    raise FlowError(_gh_error(
                        err, f"Could not label existing issues on {repo}"))
                held += len(chunk)
    return {"ok": True, "queue": name.upper(), "github_repo": repo, "mode": mode,
            "labels_created": created, "held": held}


def create_test_issue(core, queue):
    """Open one harmless issue the way the user's product would."""
    cfg = core._wt_read_config() or {}
    name, conf = _queue_conf(cfg, queue)
    repo = str(conf["github_repo"])
    labels = issue_labels(name, conf, cfg)
    for label in labels:
        _ensure_label(repo, label)
    args = ["issue", "create", "--repo", repo, "--title", TEST_TITLE, "--body", TEST_BODY]
    for label in labels:
        args += ["--label", label]
    rc, out, err = _gh(args, timeout=40)
    if rc != 0:
        raise FlowError(_gh_error(err, f"Could not open an issue on {repo}"))
    url = next((line.strip() for line in reversed(out.splitlines())
                if "/issues/" in line), "")
    match = re.search(r"/issues/(\d+)", url)
    if not match:
        raise FlowError("GitHub opened the issue but returned no issue number")
    number = int(match.group(1))
    return {
        "ok": True,
        "queue": name.upper(),
        "github_repo": repo,
        "number": number,
        "ref": f"{name.upper()}-{number}",
        "url": url,
        "labels": labels,
        "mode": "all" if conf.get("auto_drain") else "labeled",
        "grace_s": _grace_s(conf),
        "created_at": time.time(),
    }


_ISSUE_STATE_TTL_S = 8.0
_issue_state_lock = threading.Lock()
_issue_state_memo = {}          # (repo, number) -> (checked_at, result)
_RESOLUTION_PREFIX = "watchtower resolution:"


def closed_issue_stage(issue):
    """``{"stage": "done", "summary": ...}`` for a closed GitHub issue, else {}.

    ``issue`` is ``gh issue view --json state,comments``. The summary is the
    agent's closing note, which WatchTower posts as the issue's last
    "WatchTower resolution: ..." comment.
    """
    if not isinstance(issue, dict) or str(issue.get("state") or "").upper() != "CLOSED":
        return {}
    summary = ""
    for comment in reversed(issue.get("comments") or []):
        body = str((comment or {}).get("body") or "").strip()
        if body.lower().startswith(_RESOLUTION_PREFIX):
            summary = body[len(_RESOLUTION_PREFIX):].strip()
            break
    return {"stage": "done", "summary": summary[:600]}


def _closed_on_github(repo, number):
    """closed_issue_stage for a live issue, memoized for a few seconds.

    The wizard polls every few seconds while it waits; the memo keeps that to
    one ``gh`` call per window however many tabs are watching. A closed issue
    is remembered for good: it does not reopen itself.
    """
    key = (repo.lower(), int(number))
    now = time.time()
    with _issue_state_lock:
        hit = _issue_state_memo.get(key)
        if hit and (hit[1] or now - hit[0] < _ISSUE_STATE_TTL_S):
            return dict(hit[1])
    result = {}
    code, out, _err = _gh(["issue", "view", str(int(number)), "--repo", repo,
                           "--json", "state,comments"], timeout=20)
    if code == 0:
        try:
            result = closed_issue_stage(json.loads(out or "{}"))
        except ValueError:
            result = {}
    with _issue_state_lock:
        if len(_issue_state_memo) > 64:
            _issue_state_memo.clear()
        _issue_state_memo[key] = (now, result)
    return dict(result)


def test_status(core, queue, number):
    """Where the test issue is now, read from the same list the Queues board
    shows, so the wizard and the board never disagree."""
    name, conf = _queue_conf(core._wt_read_config() or {}, queue)
    try:
        number = int(number)
    except (TypeError, ValueError):
        raise FlowError("number is required")
    ref = f"{name.upper()}-{number}"
    try:
        items = core._ux_fixes_list_items_cached(None, None) or []
    except Exception:
        items = []
    item = next((it for it in items if str(it.get("ref") or "").upper() == ref), None)
    out = derive_stage(item, auto_drain=bool(conf.get("auto_drain")),
                       grace_s=_grace_s(conf))
    if not item:
        # Not in the queue list. That is true twice in a ticket's life: before
        # the queue has seen it, and again once the agent closed the issue and
        # the open-issue list dropped it. Only GitHub can tell the two apart.
        out.update(_closed_on_github(str(conf["github_repo"]), number))
    out.update(ok=True, queue=name.upper(), number=number, ref=ref,
               url=f"https://github.com/{conf['github_repo']}/issues/{number}")
    return out
