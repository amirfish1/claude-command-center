"""Extracted from server.py (originally lines 53814-56111).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import base64
import fcntl
import federation
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Fleet executor — reviewed plan, persisted resumable jobs
#
# "Resolve all" builds a dependency-ordered plan from the recommendations;
# the user deselects, confirms ONCE, and the plan runs as a persisted job.
# Every step revalidates its preconditions immediately before mutating (a
# stale plan stops safely), checks whether the desired end state already
# holds (restart/retry never repeats an external mutation), and cross-node
# steps ride the idempotent route envelope. Never force-push, never merge
# red checks, never delete dirty or unproven worktrees.
# ---------------------------------------------------------------------------

FLEET_JOBS_DIR = _core.COMMAND_CENTER_STATE_DIR / "fleet-jobs"

_FLEET_EXECUTABLE_KINDS = {
    "push", "pull_ff", "remove_worktree", "mark_ready", "merge_pr",
    "ask_commit", "finish_worktree",
}
_FLEET_JOBS_LOCK = threading.Lock()


def _fleet_job_path(plan_id):
    safe = re.sub(r"[^A-Za-z0-9-]", "", plan_id or "")
    return FLEET_JOBS_DIR / f"{safe}.json"


def _fleet_job_save(job):
    FLEET_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = _fleet_job_path(job["plan_id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, indent=1))
    tmp.replace(path)


def _fleet_job_load(plan_id):
    try:
        data = json.loads(_fleet_job_path(plan_id).read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _fleet_job_log(job, action, text, level="info"):
    job.setdefault("log", []).append({
        "t": time.time(), "action_id": action.get("id") if action else None,
        "text": text, "level": level,
    })
    job["log"] = job["log"][-400:]


def _fleet_plan_create():
    """Resolve-all: fresh inventory → recommendations → reviewable plan."""
    inventory = _core._fleet_inventory_payload(fetch=True)
    recs = _core._fleet_recommendations(inventory)
    actions = []
    for rec in recs:
        entry = dict(rec)
        if rec["kind"] not in _FLEET_EXECUTABLE_KINDS:
            entry["status"] = "manual"
        elif rec["blockers"]:
            entry["status"] = "blocked"
        else:
            entry["status"] = "proposed"
        actions.append(entry)
    job = {
        "plan_id": str(uuid.uuid4()),
        "created_at": time.time(),
        "created_by_node": federation.node_id(),
        "status": "draft",
        "actions": actions,
        "log": [],
    }
    _fleet_job_save(job)
    return job


def _fleet_revalidate_and_execute(action):
    """Execute ONE action on its owning node, revalidating first. Cross-node
    steps route through the idempotent peer envelope."""
    me = federation.node_id()
    if action.get("node_id") and action["node_id"] != me:
        args = {"action": action,
                "req_id": f"{action.get('plan_id', '')}:{action['id']}"}
        return _core._federation_proxy_session_action(
            action["node_id"], "fleet_step", args, timeout=180.0)
    return _fleet_execute_local_step(action)


def _fleet_execute_local_step(action):
    """The single-step executor. Precondition → end-state-already? → mutate."""
    kind = action.get("kind")
    ident = action.get("repo_identity") or ""
    repo_path = federation.resolve_repo_path(ident)
    if kind in ("push", "pull_ff", "remove_worktree", "mark_ready",
                "merge_pr") and not repo_path:
        return {"ok": False, "error": "stale_mapping",
                "detail": f"no local clone mapped for {ident}"}
    if kind == "push":
        branch = action.get("target")
        wt_path = _fleet_find_worktree_for_branch(repo_path, branch) or repo_path
        state = _handoff_git_state(wt_path)
        if state["dirty_count"]:
            return {"ok": False, "error": "revalidation_failed",
                    "detail": "tree became dirty since the plan was built"}
        if not state.get("unpublished_commits"):
            return {"ok": True, "already": True,
                    "detail": "nothing left to push - end state already holds"}
        args = (["push", "origin", f"HEAD:{branch}"] if state["has_upstream"]
                else ["push", "-u", "origin", branch])
        rc, out, err = _core._git(args, wt_path, timeout=120)
        if rc != 0:
            return {"ok": False, "error": "push_failed",
                    "detail": (err or out).strip()[:400]}
        return {"ok": True, "pushed": state.get("unpublished_commits")}
    if kind == "pull_ff":
        branch = action.get("target")
        state = _handoff_git_state(repo_path)
        if state["dirty_count"]:
            return {"ok": False, "error": "revalidation_failed",
                    "detail": "tree became dirty - refusing to pull"}
        if state["branch"] != branch:
            return {"ok": False, "error": "revalidation_failed",
                    "detail": f"clone moved to branch {state['branch']!r}"}
        _core._git(["fetch", "--quiet", "origin"], repo_path, timeout=90)
        rc, out, err = _core._git(["merge", "--ff-only", f"origin/{branch}"],
                            repo_path, timeout=60)
        if rc != 0:
            return {"ok": False, "error": "pull_failed",
                    "detail": (err or out).strip()[:400] or
                              "fast-forward not possible (local diverged)"}
        return {"ok": True, "merged": f"origin/{branch}"}
    if kind == "remove_worktree":
        wt_path = action.get("target")
        if not wt_path or not os.path.isdir(wt_path):
            return {"ok": True, "already": True,
                    "detail": "worktree already absent"}
        # Fresh proof, all three gates: clean, nothing unpublished,
        # head reachable from origin's default branch.
        state = _handoff_git_state(wt_path)
        if state["dirty_count"]:
            return {"ok": False, "error": "revalidation_failed",
                    "detail": "worktree is dirty - never deleted"}
        _core._git(["fetch", "--quiet", "origin"], repo_path, timeout=90)
        rc, out, _e = _core._git(["rev-list", "--count", "HEAD", "--not",
                            "--remotes=origin"], wt_path)
        if not (rc == 0 and out.strip() == "0"):
            return {"ok": False, "error": "revalidation_failed",
                    "detail": "worktree has unpublished commits - never deleted"}
        default = _core._federation_default_branch_view(repo_path)
        head = state.get("commit")
        if not (default.get("branch") and head):
            return {"ok": False, "error": "revalidation_failed",
                    "detail": "cannot prove merged-ness (no default branch/head)"}
        rc, _o, _e = _core._git(["merge-base", "--is-ancestor", head,
                           f"origin/{default['branch']}"], repo_path)
        if rc != 0:
            return {"ok": False, "error": "revalidation_failed",
                    "detail": f"head {head[:12]} is NOT reachable from "
                              f"origin/{default['branch']} — preserved"}
        rc, out, err = _core._git(["worktree", "remove", wt_path], repo_path, timeout=60)
        if rc != 0:
            return {"ok": False, "error": "remove_failed",
                    "detail": (err or out).strip()[:400]}
        return {"ok": True, "removed": wt_path, "proof": {
            "head": head, "reachable_from": f"origin/{default['branch']}"}}
    if kind in ("mark_ready", "merge_pr"):
        number = str(action.get("target") or "").lstrip("#")
        if not number.isdigit():
            return {"ok": False, "error": "bad_request",
                    "detail": "missing PR number"}
        try:
            proc = subprocess.run(
                ["gh", "pr", "view", number, "--json",
                 "state,isDraft,mergeable,mergeStateStatus,reviewDecision,"
                 "statusCheckRollup"],
                cwd=repo_path, capture_output=True, text=True, timeout=15)
            pr = json.loads(proc.stdout) if proc.returncode == 0 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            pr = None
        if not pr:
            return {"ok": False, "error": "revalidation_failed",
                    "detail": "could not re-check the PR via gh"}
        if (pr.get("state") or "").upper() == "MERGED":
            return {"ok": True, "already": True, "detail": "PR already merged"}
        failing = [c.get("name") for c in pr.get("statusCheckRollup") or []
                   if isinstance(c, dict) and (c.get("conclusion") or "").upper()
                   in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED")]
        if kind == "mark_ready":
            if not pr.get("isDraft"):
                return {"ok": True, "already": True, "detail": "already ready"}
            if failing:
                return {"ok": False, "error": "revalidation_failed",
                        "detail": f"checks failing now: {failing[:3]}"}
            proc = subprocess.run(["gh", "pr", "ready", number], cwd=repo_path,
                                  capture_output=True, text=True, timeout=15)
            return ({"ok": True} if proc.returncode == 0 else
                    {"ok": False, "error": "gh_failed",
                     "detail": (proc.stderr or "").strip()[:300]})
        # merge_pr — every objective gate re-checked at mutation time
        if failing:
            return {"ok": False, "error": "revalidation_failed",
                    "detail": f"required checks failing: {failing[:3]} - "
                              "never merged red"}
        if (pr.get("mergeable") or "").upper() == "CONFLICTING":
            return {"ok": False, "error": "revalidation_failed",
                    "detail": "merge conflict appeared"}
        if (pr.get("reviewDecision") or "").upper() in ("CHANGES_REQUESTED",
                                                        "REVIEW_REQUIRED"):
            return {"ok": False, "error": "revalidation_failed",
                    "detail": f"review gate: {pr.get('reviewDecision')}"}
        proc = subprocess.run(["gh", "pr", "merge", number, "--squash"],
                              cwd=repo_path, capture_output=True, text=True,
                              timeout=30)
        return ({"ok": True, "merged": number} if proc.returncode == 0 else
                {"ok": False, "error": "gh_failed",
                 "detail": (proc.stderr or "").strip()[:300]})
    if kind in ("ask_commit", "finish_worktree"):
        candidates = (action.get("evidence") or {}).get("candidate_sessions") or []
        if not candidates:
            return {"ok": False, "error": "no_owner",
                    "detail": "no session evidence - flagged for human review"}
        target = candidates[0]
        ref = target.get("ref") or target.get("session_id")
        if kind == "ask_commit":
            text = ("A fleet review found uncommitted work attributed to you in "
                    f"{action.get('target')}. Please commit YOUR files only "
                    "(git commit --only <your paths>) and reply DONE. If this "
                    "work is not yours, say NOT MINE.")
        else:
            text = (f"A fleet review found your worktree {action.get('target')} "
                    "unfinished (unmerged or dirty). Please finish the slice: "
                    "commit, push, and open/complete its PR — or reply ABANDON "
                    "if it should be preserved for someone else.")
        result = _core._inject_text_into_session(ref, text, source="fleet-step")
        return {"ok": bool(result.get("ok")), "pinged": ref,
                "via": result.get("via"), "detail": result.get("error")}
    return {"ok": False, "error": "unsupported_capability",
            "detail": f"no executor for {kind!r}"}


def _fleet_find_worktree_for_branch(repo_path, branch):
    if not branch:
        return None
    rc, out, _err = _core._git(["worktree", "list", "--porcelain"], repo_path)
    if rc != 0:
        return None
    current = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = line.split(" ", 1)[1].strip()
        elif line.startswith("branch ") and current:
            if line.rsplit("/", 1)[-1].strip() == branch:
                return current
    return None


def _fleet_run_job(plan_id):
    """Background runner: executes every selected step in order, persisting
    after each so a CCC restart can resume without repeating mutations."""
    with _FLEET_JOBS_LOCK:
        job = _fleet_job_load(plan_id)
        if not job:
            return
        job["status"] = "running"
        _fleet_job_save(job)
    for action in job["actions"]:
        if action.get("status") not in ("selected", "running"):
            continue
        action["plan_id"] = plan_id
        action["status"] = "running"
        _fleet_job_log(job, action, f"running {action['kind']} on "
                       f"{action.get('node_name') or 'this node'}: "
                       f"{action.get('target')}")
        with _FLEET_JOBS_LOCK:
            _fleet_job_save(job)
        try:
            result = _fleet_revalidate_and_execute(action)
        except Exception as e:
            result = {"ok": False, "error": "exception", "detail": str(e)[:300]}
        action["result"] = result
        if result.get("ok"):
            action["status"] = "done"
            _fleet_job_log(job, action,
                           "already satisfied" if result.get("already")
                           else "done", "ok")
        elif result.get("error") == "revalidation_failed":
            action["status"] = "stopped_stale"
            _fleet_job_log(job, action,
                           f"stopped safely: {result.get('detail')}", "warn")
        else:
            action["status"] = "failed"
            _fleet_job_log(job, action,
                           f"failed: {result.get('error')}: {result.get('detail')}",
                           "error")
        with _FLEET_JOBS_LOCK:
            _fleet_job_save(job)
    job["status"] = "finished"
    job["finished_at"] = time.time()
    with _FLEET_JOBS_LOCK:
        _fleet_job_save(job)


def _fleet_execute_payload(plan_id, selected_ids):
    job = _fleet_job_load(plan_id)
    if not job:
        return {"ok": False, "error": "unknown_plan"}, 404
    if job.get("status") == "running":
        return {"ok": False, "error": "already_running"}, 409
    selected = set(selected_ids or [])
    chosen = 0
    for action in job["actions"]:
        if action["id"] in selected:
            if action.get("status") in ("blocked", "manual"):
                return {"ok": False, "error": "action_blocked",
                        "detail": f"{action['id']} has unmet gates — it cannot "
                                  "be selected",
                        "blockers": action.get("blockers")}, 400
            action["status"] = "selected"
            chosen += 1
        elif action.get("status") == "proposed":
            action["status"] = "skipped"
    if not chosen:
        return {"ok": False, "error": "nothing_selected"}, 400
    job["status"] = "confirmed"
    job["confirmed_at"] = time.time()
    _fleet_job_save(job)
    threading.Thread(target=_fleet_run_job, args=(plan_id,), daemon=True).start()
    return {"ok": True, "plan_id": plan_id, "selected": chosen}, 200


def _fleet_resume_payload(plan_id):
    """After a restart: re-run steps that never finished. Completed external
    mutations are not repeated — every executor checks end state first."""
    job = _fleet_job_load(plan_id)
    if not job:
        return {"ok": False, "error": "unknown_plan"}, 404
    resumable = [a for a in job["actions"]
                 if a.get("status") in ("selected", "running")]
    if not resumable:
        return {"ok": False, "error": "nothing_to_resume",
                "detail": f"job status {job.get('status')}"}, 400
    _fleet_job_log(job, None, f"resumed after restart: {len(resumable)} step(s)")
    _fleet_job_save(job)
    threading.Thread(target=_fleet_run_job, args=(plan_id,), daemon=True).start()
    return {"ok": True, "plan_id": plan_id, "resuming": len(resumable)}, 200


# ---------------------------------------------------------------------------
# Fleet attribution — evidence hierarchy, never fabricated
# ---------------------------------------------------------------------------

# Per-node provenance index: hook write events (and transcript backfill
# hits) accumulate here so attribution survives sidecar rotation. Appended
# during fleet scans and attribution lookups — never on the dashboard hot
# path.
PROVENANCE_INDEX_FILE = _core.COMMAND_CENTER_STATE_DIR / "federation" / "provenance.jsonl"
_PROVENANCE_SEEN = set()
_PROVENANCE_LOCK = threading.Lock()
_PROVENANCE_CAP = 6000


def _provenance_append(records):
    if not records:
        return
    with _PROVENANCE_LOCK:
        try:
            PROVENANCE_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
            with PROVENANCE_INDEX_FILE.open("a", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec) + "\n")
            # Bounded: compact to the newest half when the cap is exceeded.
            if PROVENANCE_INDEX_FILE.stat().st_size > _PROVENANCE_CAP * 200:
                lines = PROVENANCE_INDEX_FILE.read_text().splitlines()
                if len(lines) > _PROVENANCE_CAP:
                    tmp = PROVENANCE_INDEX_FILE.with_suffix(".jsonl.tmp")
                    tmp.write_text("\n".join(lines[-_PROVENANCE_CAP // 2:]) + "\n")
                    tmp.replace(PROVENANCE_INDEX_FILE)
        except OSError:
            pass


def _provenance_harvest_sidecars(session_ids):
    """Fold the CURRENT live-state hook markers into the persisted index.
    Sidecars only remember the last tool per session; the index keeps the
    history."""
    records = []
    for sid in session_ids:
        try:
            sc = json.loads((_core.SIDECAR_STATE_DIR / f"{sid}.json").read_text())
        except (OSError, ValueError):
            continue
        fpath = sc.get("file")
        ts = sc.get("timestamp")
        if not fpath or not ts:
            continue
        key = (sid, fpath, ts)
        if key in _PROVENANCE_SEEN:
            continue
        _PROVENANCE_SEEN.add(key)
        records.append({"sid": sid, "path": fpath, "ts": ts,
                        "kind": "hook_write", "tool": sc.get("tool")})
    _provenance_append(records)


def _provenance_lookup(target_real):
    """Indexed events for one path — bounded read, newest last."""
    hits = []
    try:
        with PROVENANCE_INDEX_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if os.path.realpath(str(rec.get("path") or "")) == target_real:
                    hits.append(rec)
    except OSError:
        pass
    return hits[-20:]


def _fleet_attribute_path(repo_path, target_path):
    """Who touched this path? Evidence hierarchy: hook write event >
    worktree/session ownership > transcript tool path > timestamp
    correlation. Returns every plausible candidate with confidence and
    evidence; empty candidates = honest unknown."""
    target_path = str(Path(target_path).expanduser())
    if not os.path.isabs(target_path):
        target_path = os.path.join(repo_path, target_path)
    target_real = os.path.realpath(target_path)
    wt_root = _core._git_toplevel_for_existing_dir(
        target_path if os.path.isdir(target_path)
        else os.path.dirname(target_path)) or repo_path
    sessions = _core._fleet_sessions_for_repo(repo_path)
    _provenance_harvest_sidecars([s.get("session_id") for s in sessions
                                  if s.get("session_id")])
    indexed = _provenance_lookup(target_real)
    indexed_by_sid = {}
    for rec in indexed:
        indexed_by_sid.setdefault(rec.get("sid"), []).append(rec)
    candidates = []
    try:
        file_mtime = os.path.getmtime(target_path)
    except OSError:
        file_mtime = None
    for s in sessions:
        sid = s.get("session_id")
        if not sid:
            continue
        evidence = []
        confidence = None
        # 1. Hook write event — live sidecar OR the persisted provenance
        #    index (which outlives sidecar rotation): strongest signal.
        try:
            sidecar = json.loads((_core.SIDECAR_STATE_DIR / f"{sid}.json").read_text())
            sfile = os.path.realpath(str(sidecar.get("file") or ""))
            if sfile and (sfile == target_real or
                          sfile.startswith(wt_root.rstrip("/") + "/")):
                evidence.append({
                    "kind": "hook_write_event",
                    "detail": f"hook recorded a write to {sidecar.get('file')}",
                    "at": sidecar.get("timestamp"),
                })
                confidence = "high"
        except (OSError, ValueError):
            pass
        for rec in indexed_by_sid.get(sid, [])[-3:]:
            evidence.append({
                "kind": "hook_write_event",
                "detail": f"provenance index: {rec.get('tool') or 'tool'} "
                          f"touched {rec.get('path')}",
                "at": rec.get("ts"),
            })
            confidence = "high"
        # 2. Worktree / session ownership.
        s_cwd = (s.get("cwd") or "").rstrip("/")
        if s_cwd == wt_root.rstrip("/"):
            evidence.append({"kind": "worktree_ownership",
                             "detail": f"session works in {wt_root}"})
            confidence = confidence or "medium"
        # 3. Transcript tool paths (bounded scan) — hits are backfilled into
        #    the provenance index so future lookups are cheap.
        try:
            file_paths, _cd = _core._scan_session_tool_paths(sid)
            hits = [p for p in file_paths
                    if os.path.realpath(p) == target_real]
            if hits:
                evidence.append({"kind": "transcript_tool_path",
                                 "detail": f"session edited {target_path} "
                                           f"({len(hits)} tool event(s))"})
                confidence = "high"
                key = (sid, target_path, "backfill")
                if key not in _PROVENANCE_SEEN:
                    _PROVENANCE_SEEN.add(key)
                    _provenance_append([{
                        "sid": sid, "path": target_path, "ts": time.time(),
                        "kind": "transcript_backfill", "hits": len(hits)}])
        except Exception:
            pass
        # 4. Timestamp correlation — weakest, corroboration only.
        if file_mtime and s.get("timestamp"):
            try:
                delta = abs(file_mtime - float(s["timestamp"]) / 1000.0)
                if delta < 1800:
                    evidence.append({
                        "kind": "timestamp_correlation",
                        "detail": f"session active within {int(delta)}s of the "
                                  "file's last change"})
                    confidence = confidence or "low"
            except (TypeError, ValueError):
                pass
        if evidence:
            candidates.append({
                "session_id": sid,
                "ref": s.get("ref"),
                "display_name": s.get("display_name"),
                "node_id": federation.node_id(),
                "confidence": confidence or "low",
                "evidence": evidence,
                "last_event": s.get("timestamp"),
                "is_live": s.get("is_live"),
            })
    rank = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda c: rank.get(c["confidence"], 3))
    return {
        "ok": True,
        "path": target_path,
        "worktree": wt_root,
        "candidates": candidates,
        "unknown": not candidates,
        "observed_at": time.time(),
    }


# ---------------------------------------------------------------------------
# Session handoff — "Continue on another machine"
#
# GitHub transports code; CCC transports session state. The source pushes,
# the destination fetches and prepares a checkout (or isolated worktree),
# then the native transcript travels as a hash-verified bundle, gets its
# path-bearing metadata rewritten to the destination's repo mapping, and is
# activated atomically. An ownership lease on both sides prevents two nodes
# from independently resuming the same conversation.
# ---------------------------------------------------------------------------


def _handoff_locate(session_id):
    """Resolve everything the handoff needs to know about a local session.
    Returns (info, None) or (None, (payload, status))."""
    if not session_id:
        return None, ({"ok": False, "error": "bad_request",
                       "detail": "session_id required"}, 400)
    engine = _core._detect_session_engine(session_id) or "claude"
    if engine != "claude":
        return None, ({"ok": False, "error": "unsupported_capability",
                       "detail": f"engine {engine!r} has no safe native-store "
                                 "migration yet — use cross-machine orchestration "
                                 "through its owning CCC instead"}, 400)
    jsonl = _core._find_session_jsonl(session_id)
    if jsonl is None:
        return None, ({"ok": False, "error": "unknown_session",
                       "detail": f"no transcript for {session_id}"}, 404)
    cwd = _core.find_session_cwd(session_id)
    if not cwd or not os.path.isdir(cwd):
        return None, ({"ok": False, "error": "unknown_session",
                       "detail": "session cwd no longer exists - cannot map paths"}, 409)
    repo_top = _core._git_toplevel_for_existing_dir(cwd)
    if not repo_top:
        return None, ({"ok": False, "error": "bad_request",
                       "detail": f"session cwd {cwd} is not inside a git repository"}, 409)
    ident = federation.repo_identity(repo_top)
    if not ident:
        return None, ({"ok": False, "error": "bad_request",
                       "detail": f"cannot derive a repository identity for {repo_top}"}, 409)
    return {
        "session_id": session_id,
        "engine": engine,
        "transcript_path": str(jsonl),
        "session_cwd": cwd,
        "repo_top": repo_top,
        "repo_identity": ident["identity"],
        "repo_identity_kind": ident["kind"],
    }, None


def _handoff_git_state(repo_top):
    """Source-side git facts for the preflight — each dimension separate."""
    rc, out, _ = _core._git(["status", "--porcelain"], repo_top)
    dirty_files = [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else []
    rc, out, _ = _core._git(["rev-parse", "--abbrev-ref", "HEAD"], repo_top)
    branch = out.strip() if rc == 0 else ""
    detached = branch == "HEAD"
    rc, out, _ = _core._git(["rev-parse", "HEAD"], repo_top)
    commit = out.strip() if rc == 0 else None
    rc, out, _ = _core._git(["rev-list", "--count", "@{u}..HEAD"], repo_top)
    if rc == 0:
        unpushed = int(out.strip() or 0)
        has_upstream = True
    else:
        unpushed = None
        has_upstream = False
    return {
        "branch": None if detached else branch,
        "detached": detached,
        "commit": commit,
        "dirty_files": dirty_files[:50],
        "dirty_count": len(dirty_files),
        "has_upstream": has_upstream,
        "unpublished_commits": unpushed,
    }


def _handoff_preflight_payload(session_id, dest_node_id):
    """Build the exact plan for a handoff, with blockers. Read-only."""
    peer = federation.get_peer(dest_node_id or "")
    if not peer:
        return {"ok": False, "error": "unpaired_peer",
                "detail": f"no paired peer {dest_node_id!r}"}, 404
    info, err = _handoff_locate(session_id)
    if err:
        return err
    me = federation.node_id()
    lease = federation.read_lease(session_id)
    if lease and lease.get("owner_node") not in (None, me):
        return {"ok": False, "error": "not_owner",
                "owner_node": lease.get("owner_node"),
                "detail": "this session was already handed to another node"}, 409
    git_state = _handoff_git_state(info["repo_top"])
    live = _core.session_live_status(session_id, info["session_cwd"]) or {}
    is_live = bool(live.get("running") or live.get("is_live"))
    blockers = []
    if git_state["dirty_count"]:
        # Identify the likely owning session(s) so the guard is actionable:
        # the UI offers "ask to commit" routed at these, never a blind sweep.
        dirty_owner_candidates = []
        try:
            first_dirty = (git_state["dirty_files"][0] or "")[3:].strip()
            if first_dirty:
                attribution = _fleet_attribute_path(
                    info["repo_top"], os.path.join(info["repo_top"], first_dirty))
                dirty_owner_candidates = attribution.get("candidates", [])[:3]
        except Exception:
            dirty_owner_candidates = []
        blockers.append({
            "code": "dirty_worktree",
            "detail": f"{git_state['dirty_count']} dirty file(s) in {info['repo_top']} - "
                      "commit (or ask the owning session to commit) first; "
                      "CCC never copies dirty files between machines",
            "files": git_state["dirty_files"][:10],
            "candidate_sessions": dirty_owner_candidates,
        })
    if is_live:
        blockers.append({
            "code": "source_process_running",
            "detail": "the session process is still running on this machine - "
                      "stop it before handing off so the transcript stops moving",
        })
    if git_state["detached"] and not git_state["commit"]:
        blockers.append({"code": "no_commit", "detail": "repository has no HEAD commit"})
    steps = [
        {"step": "push", "detail": (
            f"push {git_state['unpublished_commits']} unpublished commit(s) on "
            f"branch {git_state['branch']}" if git_state.get("unpublished_commits")
            else ("publish branch to origin (no upstream yet)"
                  if not git_state["has_upstream"] else "nothing to push")),
         "needed": bool(git_state.get("unpublished_commits")) or not git_state["has_upstream"]},
        {"step": "prepare_destination", "detail": (
            f"fetch on {peer.get('name')} and check out "
            f"{git_state['branch'] or git_state['commit']} "
            "(isolated worktree if the clone is busy)"), "needed": True},
        {"step": "transfer_session", "detail": (
            "export transcript + title/model sidecar values, rewrite "
            f"{info['repo_top']} → destination path, verify hashes, "
            "import atomically"), "needed": True},
        {"step": "flip_ownership", "detail": (
            f"lease the conversation to {peer.get('name')} - this node stops "
            "resuming it"), "needed": True},
    ]
    return {
        "ok": True,
        "session": info,
        "git": git_state,
        "source_live": is_live,
        "dest_node": {"node_id": peer["node_id"], "name": peer.get("name")},
        "blockers": blockers,
        "steps": steps,
        "ready": not blockers,
    }, 200


def _handoff_collect_sidecars(session_id):
    """CCC metadata worth carrying: title, per-session overrides. Values only
    — the destination applies them to its own state files."""
    sidecars = {}
    try:
        name = _core._load_session_name_overrides().get(session_id)
        if name:
            sidecars["display_name"] = name
    except Exception:
        pass
    try:
        overrides = _core._load_session_overrides().get(session_id)
        if isinstance(overrides, dict) and overrides:
            sidecars["session_overrides"] = overrides
    except Exception:
        pass
    return sidecars


def _handoff_start_payload(session_id, dest_node_id, allow_overwrite=False):
    """Execute the handoff plan. Revalidates the preflight first."""
    preflight, status = _handoff_preflight_payload(session_id, dest_node_id)
    if status != 200:
        return preflight, status
    if preflight["blockers"]:
        return {"ok": False, "error": "preflight_blocked",
                "blockers": preflight["blockers"], "plan": preflight}, 409
    info = preflight["session"]
    git_state = preflight["git"]
    peer = federation.get_peer(dest_node_id)
    client = federation.PeerClient(peer)
    log = []

    # 1. Publish source commits (the only code transport is git).
    branch = git_state["branch"]
    if git_state.get("unpublished_commits") or not git_state["has_upstream"]:
        if not branch:
            return {"ok": False, "error": "preflight_blocked",
                    "detail": "detached HEAD with unpublished commits - "
                              "create a branch first"}, 409
        push_args = ["push", "origin", f"HEAD:{branch}"]
        if not git_state["has_upstream"]:
            push_args = ["push", "-u", "origin", branch]
        rc, out, errout = _core._git(push_args, info["repo_top"], timeout=120)
        if rc != 0:
            return {"ok": False, "error": "push_failed",
                    "detail": (errout or out).strip()[:400]}, 502
        log.append({"step": "push", "ok": True})
    else:
        log.append({"step": "push", "ok": True, "skipped": "nothing to push"})
    recheck = _handoff_git_state(info["repo_top"])
    if recheck["dirty_count"] or (recheck.get("unpublished_commits") or 0) > 0:
        return {"ok": False, "error": "preflight_blocked",
                "detail": "repository state changed during handoff - re-run preflight"}, 409

    # 2. Destination prepares a checkout and tells us the mapped path.
    try:
        prep = client.request("POST", "/api/federation/v1/handoff/prepare", {
            "repo_identity": info["repo_identity"],
            "branch": branch,
            "commit": git_state["commit"],
        }, timeout=180)
    except federation.PeerError as e:
        return {"ok": False, "error": e.kind,
                "detail": f"destination prepare failed: {e}"}, 502
    if not prep.get("ok"):
        return {"ok": False, "error": prep.get("error") or "prepare_failed",
                "detail": prep.get("detail"), "prepare": prep}, 502
    dest_root = prep["dest_cwd"]
    log.append({"step": "prepare_destination", "ok": True, "dest_cwd": dest_root,
                "method": prep.get("method")})

    # 3. Build the bundle (path rewrite repo-root → repo-root so worktree /
    #    subdirectory cwds map too).
    rel = os.path.relpath(info["session_cwd"], info["repo_top"])
    dest_session_cwd = dest_root if rel == "." else os.path.join(dest_root, rel)
    try:
        bundle = federation.build_transfer_bundle(
            engine=info["engine"],
            session_id=session_id,
            transcript_path=info["transcript_path"],
            source_cwd=info["session_cwd"],
            dest_cwd=dest_session_cwd,
            repo_identity=info["repo_identity"],
            source_node=federation.node_id(),
            dest_node=peer["node_id"],
            branch=branch,
            commit=git_state["commit"],
            sidecars=_handoff_collect_sidecars(session_id),
            rewrite_from=info["repo_top"],
            rewrite_to=dest_root,
        )
    except (OSError, ValueError, federation.PeerError) as e:
        kind = getattr(e, "kind", "export_failed")
        return {"ok": False, "error": kind, "detail": str(e)}, 502

    # 4. Ship it. The import is staged + hash-verified + atomic on the peer.
    files_b64 = {name: base64.b64encode(data).decode("ascii")
                 for name, data in bundle["files"].items()}
    try:
        imported = client.request("POST", "/api/federation/v1/handoff/import", {
            "manifest": bundle["manifest"],
            "files": files_b64,
            "allow_overwrite": bool(allow_overwrite),
        }, timeout=300)
    except federation.PeerError as e:
        return {"ok": False, "error": e.kind,
                "detail": f"destination import failed: {e}"}, 502
    if not imported.get("ok"):
        return {"ok": False, "error": imported.get("error") or "import_failed",
                "detail": imported.get("detail"),
                "import": imported}, 409 if imported.get("error") == "session_exists" else 502
    transcript_sha = imported.get("transcript_sha256") or ""
    log.append({"step": "transfer_session", "ok": True,
                "transcript_sha256": transcript_sha,
                "rewrites": bundle["manifest"]["rewrites"],
                "already_present": imported.get("already_present", False)})

    # 5. Flip ownership. From here the destination is self-sufficient.
    # The lease records the sha of THIS node's local transcript at handoff
    # time, so a future return handoff can prove our copy never moved and
    # overwrite it safely (divergence otherwise blocks the import).
    try:
        local_sha = hashlib.sha256(
            Path(info["transcript_path"]).read_bytes()).hexdigest()
    except OSError:
        local_sha = ""
    lease = federation.write_lease(
        session_id, peer["node_id"],
        transfer_id=bundle["manifest"]["transfer_id"],
        transcript_sha=local_sha,
        note=f"handed off to {peer.get('name')}",
    )
    log.append({"step": "flip_ownership", "ok": True, "owner_node": peer["node_id"]})
    return {
        "ok": True,
        "session_id": session_id,
        "dest_node": {"node_id": peer["node_id"], "name": peer.get("name")},
        "dest_cwd": dest_session_cwd,
        "transfer_id": bundle["manifest"]["transfer_id"],
        "transcript_sha256": transcript_sha,
        "rewrites": bundle["manifest"]["rewrites"],
        "log": log,
        "lease": lease,
        "resume_hint": f"claude --resume {session_id} (run on the destination, "
                       f"cwd {dest_session_cwd})",
    }, 200


def _handoff_prepare_payload(data, peer):
    """Destination side: make the repo ready for the incoming session.
    Fetch origin, verify the commit arrived, then fast-forward the clone if
    it is already on the branch — otherwise create an isolated worktree.
    Never resets, never force-checkouts, never touches dirty files."""
    ident = str(data.get("repo_identity") or "").strip()
    branch = (data.get("branch") or "").strip() or None
    commit = (data.get("commit") or "").strip() or None
    if not ident or not commit:
        return {"ok": False, "error": "bad_request",
                "detail": "repo_identity and commit are required"}, 400
    local = federation.resolve_repo_path(ident)
    if not local:
        # Best-effort auto-map: scan known repos for a matching identity.
        for candidate in _core._known_repo_paths():
            try:
                cand_ident = federation.repo_identity(candidate)
            except Exception:
                cand_ident = None
            if cand_ident and cand_ident["identity"] == ident:
                federation.map_repo(ident, candidate)
                local = candidate
                break
    if not local or not os.path.isdir(local):
        return {"ok": False, "error": "stale_mapping",
                "detail": f"no local clone mapped for {ident} on this node - "
                          "map it in peer settings"}, 404
    rc, out, errout = _core._git(["fetch", "--quiet", "origin"], local, timeout=120)
    fetched = rc == 0
    rc, _out, _err = _core._git(["cat-file", "-e", f"{commit}^{{commit}}"], local)
    if rc != 0:
        return {"ok": False, "error": "commit_not_found",
                "detail": f"commit {commit[:12]} not present after fetch - "
                          "was it pushed on the source?"}, 409
    state = _handoff_git_state(local)
    if branch and state["branch"] == branch and not state["dirty_count"]:
        rc, out, errout = _core._git(["merge", "--ff-only", commit], local, timeout=60)
        if rc == 0:
            return {"ok": True, "dest_cwd": local, "method": "clone_ff",
                    "fetched": fetched, "branch": branch, "commit": commit}, 200
        # fall through to a worktree — the clone diverged; never reset it
    wt_slug = re.sub(r"[^A-Za-z0-9]+", "-", branch or commit[:12]).strip("-").lower()
    base = f"{local}-wt-{wt_slug}"
    wt_path = base
    for i in range(2, 6):
        if not os.path.exists(wt_path):
            break
        reuse_state = _handoff_git_state(wt_path)
        if (branch and reuse_state["branch"] == branch
                and not reuse_state["dirty_count"]):
            rc, _o, _e = _core._git(["merge", "--ff-only", commit], wt_path, timeout=60)
            if rc == 0:
                return {"ok": True, "dest_cwd": wt_path, "method": "worktree_reuse",
                        "fetched": fetched, "branch": branch, "commit": commit}, 200
        wt_path = f"{base}-{i}"
    if os.path.exists(wt_path):
        return {"ok": False, "error": "worktree_conflict",
                "detail": f"no free worktree path near {base}"}, 409
    if branch:
        rc, out, errout = _core._git(["rev-parse", "--verify", "--quiet", branch], local)
        if rc == 0:
            args = ["worktree", "add", wt_path, branch]
        else:
            args = ["worktree", "add", "--track", "-b", branch, wt_path,
                    f"origin/{branch}"]
    else:
        args = ["worktree", "add", "--detach", wt_path, commit]
    rc, out, errout = _core._git(args, local, timeout=60)
    if rc != 0:
        return {"ok": False, "error": "prepare_failed",
                "detail": (errout or out).strip()[:400]}, 502
    if branch:
        rc, _o, _e = _core._git(["merge", "--ff-only", commit], wt_path, timeout=60)
        if rc != 0:
            return {"ok": False, "error": "prepare_failed",
                    "detail": f"worktree branch {branch} would not fast-forward "
                              f"to {commit[:12]}"}, 409
    return {"ok": True, "dest_cwd": wt_path, "method": "worktree_new",
            "fetched": fetched, "branch": branch, "commit": commit}, 200


def _handoff_import_payload(data, peer):
    """Destination side: staged, verified, atomic session import."""
    manifest = data.get("manifest")
    files_b64 = data.get("files")
    if not isinstance(manifest, dict) or not isinstance(files_b64, dict):
        return {"ok": False, "error": "bad_request",
                "detail": "manifest and files are required"}, 400
    try:
        files = {str(name): base64.b64decode(str(blob), validate=True)
                 for name, blob in files_b64.items()}
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": "bad_request",
                "detail": f"files must be base64: {e}"}, 400
    # The destination cwd must belong to the same repository identity the
    # manifest claims — the owning node validates its own filesystem.
    dest_cwd = str(manifest.get("dest_cwd") or "")
    dest_top = _core._git_toplevel_for_existing_dir(dest_cwd) if os.path.isdir(dest_cwd) else None
    if not dest_top:
        return {"ok": False, "error": "bad_request",
                "detail": f"dest_cwd {dest_cwd!r} is not an existing git checkout "
                          "on this node - run handoff/prepare first"}, 409
    local_ident = federation.repo_identity(dest_top)
    if not local_ident or local_ident["identity"] != manifest.get("repo_identity"):
        return {"ok": False, "error": "stale_mapping",
                "detail": "dest_cwd does not belong to the manifest's repository"}, 409
    divergence_guard = None
    lease = federation.read_lease(str(manifest.get("session_id") or ""))
    if lease and lease.get("transcript_sha"):
        divergence_guard = lease["transcript_sha"]
    result = federation.stage_and_import_bundle(
        manifest, files,
        projects_root=_core.PROJECTS_ROOT,
        allow_overwrite=bool(data.get("allow_overwrite")),
    )
    if not result.get("ok"):
        if (result.get("error") == "session_exists" and divergence_guard
                and result.get("existing_sha256") == divergence_guard):
            # Existing copy is exactly what we handed out earlier — a return
            # handoff may safely overwrite it.
            result = federation.stage_and_import_bundle(
                manifest, files, projects_root=_core.PROJECTS_ROOT, allow_overwrite=True)
        if not result.get("ok"):
            status = 409 if result.get("error") in ("session_exists",) else 400
            return result, status
    # Apply CCC sidecar values to this node's own state files.
    sidecars = manifest.get("sidecars") or {}
    try:
        if sidecars.get("display_name"):
            _core._save_session_name_override(result["session_id"], sidecars["display_name"])
        if isinstance(sidecars.get("session_overrides"), dict):
            _handoff_apply_session_overrides(result["session_id"],
                                             sidecars["session_overrides"])
    except Exception as e:
        result["sidecar_warning"] = f"sidecar apply failed: {e}"
    federation.write_lease(
        result["session_id"], federation.node_id(),
        transfer_id=result["transfer_id"],
        transcript_sha=result["transcript_sha256"],
        note=f"imported from {manifest.get('source_node', '')[:8]}",
    )
    result["owner_node"] = federation.node_id()
    return result, 200


def _handoff_apply_session_overrides(session_id, overrides):
    try:
        current = _core._load_session_overrides()
    except Exception:
        current = {}
    entry = current.get(session_id) or {}
    entry.update({k: v for k, v in overrides.items() if v is not None})
    current[session_id] = entry
    _core.COMMAND_CENTER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _core.SESSION_OVERRIDES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2))
    tmp.replace(_core.SESSION_OVERRIDES_FILE)


def _handoff_lease_guard(session_id):
    """409 payload when this node no longer owns the session, else None."""
    owner = federation.lease_owner(session_id or "")
    if owner and owner != federation.node_id():
        peer = federation.get_peer(owner)
        return {
            "ok": False,
            "error": "not_owner",
            "owner_node": owner,
            "owner_name": (peer or {}).get("name"),
            "detail": "this conversation was handed to another node - "
                      "address it there (or force-takeover to reclaim)",
        }
    return None


def _federation_handle_post(path, data, handler):
    """Dispatch every /api/federation/* POST. Returns (payload, status)."""
    # ---- peer-facing v1 protocol -----------------------------------------
    if path == "/api/federation/v1/pair":
        return _core._federation_handle_pair_request(data)
    if path == "/api/federation/v1/unpair":
        peer = _core._federation_require_peer(handler)
        if peer is None:
            return None, None
        federation.remove_peer(peer["node_id"])
        return {"ok": True, "unpaired": peer["node_id"]}, 200
    if path == "/api/federation/v1/route":
        peer = _core._federation_require_peer(handler)
        if peer is None:
            return None, None
        _core._federation_touch_peer(peer["node_id"])
        return _core._federation_execute_route(data)
    if path == "/api/federation/v1/handoff/prepare":
        peer = _core._federation_require_peer(handler)
        if peer is None:
            return None, None
        return _handoff_prepare_payload(data, peer)
    if path == "/api/federation/v1/handoff/import":
        peer = _core._federation_require_peer(handler)
        if peer is None:
            return None, None
        return _handoff_import_payload(data, peer)
    if path == "/api/federation/v1/group-chat/import":
        peer = _core._federation_require_peer(handler)
        if peer is None:
            return None, None
        return _core._group_chat_import_payload(data, peer)
    # ---- local management (browser UI) ------------------------------------
    if path == "/api/federation/handoff/preflight":
        return _handoff_preflight_payload(
            str(data.get("session_id") or "").strip(),
            str(data.get("dest_node_id") or "").strip())
    if path == "/api/federation/handoff/start":
        return _handoff_start_payload(
            str(data.get("session_id") or "").strip(),
            str(data.get("dest_node_id") or "").strip(),
            allow_overwrite=bool(data.get("allow_overwrite")))
    if path == "/api/federation/handoff/takeover":
        sid = str(data.get("session_id") or "").strip()
        if not sid:
            return {"ok": False, "error": "bad_request",
                    "detail": "session_id required"}, 400
        prior = federation.read_lease(sid)
        lease = federation.write_lease(
            sid, federation.node_id(),
            transcript_sha=(prior or {}).get("transcript_sha", ""),
            note=f"FORCE TAKEOVER: {str(data.get('reason') or 'unspecified')[:200]}",
        )
        print(f"  [federation] Force takeover of session {sid[:8]}… "
              f"(was {((prior or {}).get('owner_node') or 'unleased')[:8]})")
        return {"ok": True, "lease": lease,
                "previous_owner": (prior or {}).get("owner_node")}, 200
    if path == "/api/federation/peers/pair":
        return _core._federation_pair_initiate(data)
    if path == "/api/federation/peers/remove":
        peer_node = str(data.get("node_id") or "").strip()
        peer = federation.get_peer(peer_node)
        if peer:
            # Best-effort reciprocal unpair; local removal always proceeds.
            try:
                federation.PeerClient(peer).request(
                    "POST", "/api/federation/v1/unpair", {}, timeout=10)
            except federation.PeerError:
                pass
        removed = federation.remove_peer(peer_node)
        return {"ok": True, "removed": removed}, 200
    if path == "/api/federation/peers/rename":
        peer_node = str(data.get("node_id") or "").strip()
        name = str(data.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "bad_request", "detail": "name required"}, 400
        updated = federation.update_peer(peer_node, name=name[:80])
        if not updated:
            return {"ok": False, "error": "unpaired_peer"}, 404
        return {"ok": True, "peer": _core._federation_public_peer(updated)}, 200
    if path == "/api/federation/peers/test":
        return _core._federation_test_peer(str(data.get("node_id") or "").strip())
    if path == "/api/federation/repo-map":
        identity_key = str(data.get("identity") or "").strip()
        local_path = data.get("local_path")
        if not identity_key:
            return {"ok": False, "error": "bad_request", "detail": "identity required"}, 400
        try:
            if local_path:
                candidate = str(Path(str(local_path)).expanduser().resolve())
                resolved = _core.resolve_repo_path(candidate)
                federation.map_repo(identity_key, resolved)
            else:
                federation.unmap_repo(identity_key)
        except _core.RepoContextError as e:
            return {"ok": False, "error": e.code, "detail": str(e)}, e.status
        except ValueError as e:
            return {"ok": False, "error": "bad_request", "detail": str(e)}, 400
        return {"ok": True, "repo_map": federation.load_repo_map()}, 200
    if path == "/api/federation/node":
        name = str(data.get("display_name") or "").strip()
        if not name:
            return {"ok": False, "error": "bad_request", "detail": "display_name required"}, 400
        try:
            ident = federation.set_node_display_name(name)
        except ValueError as e:
            return {"ok": False, "error": "bad_request", "detail": str(e)}, 400
        return {"ok": True, "node": ident}, 200
    return {"ok": False, "error": "not_found"}, 404


# ---------------------------------------------------------------------------
# Multi-repo peer registry
#
# Each running CCC server writes itself into ~/.claude/command-center/registry.json
# on startup and removes itself on graceful shutdown. Stale entries (pid no
# longer alive) are pruned by readers, so a force-killed server self-heals on
# the next read. The registry is the source of truth for "which CCC servers
# are live"; the UI uses it to discover peers and aggregate cross-repo data
# in the browser. Concurrent writes from sibling servers are serialized via
# fcntl.flock on the registry file itself.
# ---------------------------------------------------------------------------

REGISTRY_FILE = _core.COMMAND_CENTER_STATE_DIR / "registry.json"


def _is_pid_alive(pid):
    """Return True if `pid` is a live process. Sends signal 0 (no-op) and
    treats any OSError as 'not alive'."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _prune_registry_entries(entries):
    """Drop entries whose pid is not alive. Pure function — no I/O."""
    return [e for e in entries if isinstance(e, dict) and _is_pid_alive(e.get("pid"))]


def _registry_locked_rmw(transform_fn):
    """Read-modify-write on REGISTRY_FILE under fcntl.flock. Calls
    `transform_fn(entries) -> entries` with the parsed list. Best-effort on
    the lock — silent on platforms without flock so the call still functions
    (with reduced safety against concurrent writers)."""
    _core.REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # mode "a+": create if missing, no truncate. seek(0) to read.
    with open(_core.REGISTRY_FILE, "a+") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except (OSError, ValueError):
            pass
        try:
            f.seek(0)
            raw = f.read() or "[]"
            try:
                entries = json.loads(raw)
                if not isinstance(entries, list):
                    entries = []
            except json.JSONDecodeError:
                entries = []
            entries = transform_fn(entries)
            f.seek(0)
            f.truncate()
            f.write(json.dumps(entries, indent=2) + "\n")
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except (OSError, ValueError):
                pass


def _git_common_dir(path):
    """Absolute git common-dir for `path`, or None.

    Identical across every worktree of a repo (`git rev-parse
    --path-format=absolute --git-common-dir`), which makes it the right
    identity key for "another CCC instance of THIS repo" as opposed to a
    legitimate multi-repo peer (see registry.json).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    common = (proc.stdout or "").strip()
    if proc.returncode != 0 or not common:
        return None
    return os.path.normpath(common)


def _pid_command(pid):
    """Best-effort command line for `pid` ("" if unreadable or dead)."""
    try:
        proc = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def _other_instance_responding(port):
    """True if a CCC server is actually answering HTTP on `port`.

    Distinguishes a genuinely live peer from a process that is still
    OS-alive (passes `_is_pid_alive`) but hung -- e.g. deadlocked, or stuck
    behind a crashed accept loop. `_is_pid_alive` alone can't tell these
    apart, and a hung holder of the port is exactly the case that turns a
    single crash into an hours-long restart-refused loop under launchd's
    KeepAlive (observed 2026-08-10/11: a stuck pid blocked every respawn
    attempt for ~14h). Short timeout on purpose -- this runs on the startup
    path and a healthy peer answers in milliseconds.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/api/loading-status", timeout=2) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


# ── Self-serve-loop health check ────────────────────────────────────────────
# 2026-08-28 incident: the dashboard process (still apparently OS-alive) went
# silent across a sleep/wake cycle -- it served normal traffic up to 06:23:24,
# the Mac slept 06:44:48-06:46:12, and it never answered another request.
# Nothing in service.err.log or python-stacks.log recorded the transition; the
# only thing that ever noticed was `run.sh`'s duplicate-guard, ~10 minutes
# later, on an unrelated fresh invocation (see `_check_duplicate_repo_instance`
# above). The existing SIGUSR2 stack dumper (`_install_python_stack_dump_handler`)
# only ever fires from the Codex app-server's own liveness probe -- a
# different subsystem entirely -- so a hang in the dashboard's own HTTP accept
# loop had no monitor and left no evidence.
#
# This is diagnostic instrumentation, not a fix: no root cause was confirmed
# (a live repro wasn't possible from a static code read), so per
# systematic-debugging this adds cheap, periodic self-probing instead of
# guessing at a patch. It reuses the exact same probe `_other_instance_responding`
# already makes against a *peer* CCC instance at startup, aimed at this
# process's own port, plus the existing SIGUSR2 all-thread dump on repeated
# misses. One local HTTP GET every 5 minutes is negligible next to the
# 30s-2h loops already running (idle reaper, telemetry, engine maintenance).
_SELF_HEALTH_CHECK_INTERVAL_S = 300  # 5 min between self-probes.
_SELF_HEALTH_CHECK_TIMEOUT_S = 5
# Require 2 consecutive misses (like the Codex app-server dumper) before
# treating it as a genuine stall -- one slow reply under load isn't proof.
_SELF_HEALTH_CHECK_FAIL_THRESHOLD = 2


def _dump_stacks_on_self_health_miss(port, consecutive_misses):
    """All-thread traceback dump when the dashboard misses its own probe.

    Reuses the SIGUSR2 handler installed by `_install_python_stack_dump_handler`
    (see also `_codex_app_server_dump_stacks_on_liveness_miss`, the same
    pattern for the Codex app-server subprocess) instead of duplicating dump
    logic. Best-effort and silent on failure -- this must never be able to
    take down the process it's trying to diagnose.
    """
    sigusr2 = getattr(signal, "SIGUSR2", None)
    if sigusr2 is None or _core._PYTHON_STACK_DUMP_FILE is None:
        return
    try:
        _core._PYTHON_STACK_DUMP_FILE.write(
            f"\n=== dashboard self-health-check miss (port={port}, "
            f"{consecutive_misses} consecutive) pid={os.getpid()} "
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC ===\n"
        )
        _core._PYTHON_STACK_DUMP_FILE.flush()
        os.kill(os.getpid(), sigusr2)
    except OSError:
        pass


def _self_health_check_once(port, consecutive_misses):
    """Run one self-health-check cycle; returns the updated miss streak.

    Split out from `_self_health_check_loop` so it's unit-testable without
    waiting on the loop's 5-minute sleep interval.
    """
    url = f"http://127.0.0.1:{int(port)}/api/loading-status"
    t0 = time.time()
    ok = False
    try:
        with urllib.request.urlopen(url, timeout=_SELF_HEALTH_CHECK_TIMEOUT_S) as r:
            ok = 200 <= r.status < 500
    except Exception:
        ok = False
    elapsed = time.time() - t0
    try:
        if ok:
            if consecutive_misses:
                _core._log_activity(
                    "self-health", "RECOVERED",
                    f"responded again after {consecutive_misses} miss(es) "
                    f"({elapsed:.2f}s)",
                )
            else:
                _core._log_activity("self-health", "beat", f"ok ({elapsed:.2f}s)")
            return 0
        consecutive_misses += 1
        _core._log_activity(
            "self-health", "MISS",
            f"no response on {url} within {_SELF_HEALTH_CHECK_TIMEOUT_S}s "
            f"(waited {elapsed:.1f}s) "
            f"({consecutive_misses}/{_core._SELF_HEALTH_CHECK_FAIL_THRESHOLD})",
        )
        if consecutive_misses >= _core._SELF_HEALTH_CHECK_FAIL_THRESHOLD:
            _core._dump_stacks_on_self_health_miss(port, consecutive_misses)
        return consecutive_misses
    except Exception:
        # Logging must never be able to kill this loop.
        return consecutive_misses


def _self_health_check_loop(port):
    """Background daemon: periodically GETs our own /api/loading-status.

    A real HTTP round-trip through the accept loop and a request-handler
    thread is the only way to prove end-to-end that the server can still
    serve -- unlike watching for arbitrary incoming traffic, this can't
    produce false "stalled" reports when the dashboard is legitimately idle
    (no browser tab open) for a long stretch, because the probe generates
    its own traffic every cycle.

    Logs a beat every cycle either way (`_log_activity` category
    "self-health") so a gap in the activity log itself is evidence too: if
    even this stops logging, the whole interpreter froze, not just the
    listening socket -- a heartbeat log line is only useful if you can also
    tell when it stopped ticking.
    """
    consecutive_misses = 0
    while True:
        try:
            time.sleep(_SELF_HEALTH_CHECK_INTERVAL_S)
        except Exception:
            return
        consecutive_misses = _core._self_health_check_once(port, consecutive_misses)


def _kill_stale_duplicate(pid, port):
    """Best-effort reclaim of a hung duplicate: SIGTERM, wait briefly, SIGKILL.

    Mirrors the stale-worker reap in run.sh (kill, poll for death, replace)
    rather than inventing a new pattern. Never raises -- worst case the pid
    lingers and the caller's own bind() surfaces the real error.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):  # ~2s
        if not _is_pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _check_duplicate_repo_instance():
    """Refuse to start when another live CCC server already serves this repo.

    Multi-repo peers (different repos, each with its own CCC) are a supported
    feature -- the registry exists to discover them. But a second instance of
    the SAME repo (e.g. a forgotten `python3 server.py` from a git worktree,
    observed live as a day-old process leaking app-server children and writing
    into the shared activity log) races the primary on the shared state dir
    and makes logs unattributable. Worktrees of one repo share a git common
    dir, which is the identity key. Intentional duplicates (dev/verification
    instances) bypass via CCC_EPHEMERAL=1 (matching the port.txt convention)
    or CCC_ALLOW_DUPLICATE_REPO=1.

    A registry entry that is OS-alive but not answering HTTP is treated as
    stale rather than a real duplicate: it's reaped and startup proceeds,
    instead of refusing forever until someone manually kills it.
    """
    if os.environ.get("CCC_EPHEMERAL") or os.environ.get("CCC_ALLOW_DUPLICATE_REPO"):
        return
    mine = _git_common_dir(_core.CCC_ROOT)
    if not mine:
        return
    for entry in _read_registry_pruned():
        if not isinstance(entry, dict):
            continue
        other = entry.get("repo_common_dir")
        if not other:
            # Entry written by an older version without the field -- derive
            # it from the recorded install path.
            other = _git_common_dir(entry.get("install_path") or "")
        if other and os.path.normpath(str(other)) == mine:
            cmd = _pid_command(entry.get("pid"))
            if "server.py" not in cmd:
                # Stale registry entry whose pid was recycled by an unrelated
                # process (the registry prunes dead pids, but pid REUSE makes
                # a dead CCC look alive). Not a real duplicate.
                continue
            if _other_instance_responding(entry.get("port")):
                print(
                    f"FATAL: another CCC server for this repo is already running: "
                    f"pid {entry.get('pid')} ({entry.get('install_path')}, "
                    f"port {entry.get('port')}, started {entry.get('started_at')}).\n"
                    f"Kill it first, or set CCC_ALLOW_DUPLICATE_REPO=1 for an "
                    f"intentional second instance.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(
                f"  duplicate-guard: pid {entry.get('pid')} is registered for "
                f"this repo but not answering on port {entry.get('port')} -- "
                f"reaping stale instance and continuing startup.",
                file=sys.stderr,
            )
            _kill_stale_duplicate(entry.get("pid"), entry.get("port"))


def _register_self(port, bind_host):
    """Insert (or replace) this process's entry in the registry.

    Dedup is by pid: each running process owns one entry. The registry
    describes this CCC server process, not an active repo. Ephemeral
    instances (CCC_EPHEMERAL=1) skip registration so they don't advertise
    temporary/verification ports as peer instances.
    """
    if os.environ.get("CCC_EPHEMERAL"):
        print("  [registry] CCC_EPHEMERAL set — not claiming registry entry")
        return
    self_pid = os.getpid()
    payload = {
        "label": _core.CCC_ROOT.name,
        "install_path": str(_core.CCC_ROOT),
        "repo_common_dir": _git_common_dir(_core.CCC_ROOT),
        "port": int(port),
        "bind_host": bind_host,
        "pid": self_pid,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": _core.__version__,
        "pending_input_protocol": 1,
    }

    def replace(entries):
        out = [e for e in entries if not (isinstance(e, dict) and e.get("pid") == self_pid)]
        out.append(payload)
        return out

    try:
        _registry_locked_rmw(replace)
        print(f"  [registry] {_core.REGISTRY_FILE} -> pid {self_pid}, port {payload['port']}")
    except OSError as e:
        print(f"  [registry] could not register ({e})")


def _unregister_self():
    """Remove this process's registry entry by current pid.

    Idempotent; silent on I/O error so it's safe to call from signal handlers.
    """
    if os.environ.get("CCC_EPHEMERAL"):
        return
    if not _core.REGISTRY_FILE.exists():
        return
    self_pid = os.getpid()

    def remove(entries):
        return [e for e in entries if not (isinstance(e, dict) and e.get("pid") == self_pid)]

    try:
        _registry_locked_rmw(remove)
    except OSError:
        pass


def _read_registry_pruned():
    """Return the registry contents with stale entries removed. Performs a
    write-back of the pruned list so the file converges to truth on every
    read — no separate reaper needed. Returns [] on any I/O error."""
    if not _core.REGISTRY_FILE.exists():
        return []

    pruned = []

    def prune(entries):
        nonlocal pruned
        pruned = _prune_registry_entries(entries)
        return pruned

    try:
        _registry_locked_rmw(prune)
    except OSError:
        return []
    return pruned


def write_port_file(bind_host, port=None):
    """Persist the listening URL to ~/.claude/command-center/port.txt so the
    ccc-orchestration skill (and any other scripted caller) can find this
    server without hardcoding the port. Single line, format
    `http://<host>:<port>`. Best-effort — failures are logged and ignored.

    A secondary/ephemeral instance (e.g. a one-off verification server on an
    alternate port) must NOT claim the shared port.txt, or it hijacks discovery
    from the primary. Start such instances with `CCC_EPHEMERAL=1` to skip the
    write. (Prefer `node snapshot.js` against the already-running server over
    spinning up a second instance at all.)"""
    if port is None:
        port = _core.PORT
    if os.environ.get("CCC_EPHEMERAL"):
        print("  [skill] CCC_EPHEMERAL set — not claiming shared port.txt")
        return f"http://127.0.0.1:{port}"
    # Bind addresses like 0.0.0.0/:: are not reliable dial targets. The
    # skill runs locally, so always publish a loopback URL for wildcard or
    # loopback binds and reserve the configured host only for explicit
    # network addresses.
    display_host = (
        "127.0.0.1"
        if bind_host in ("", "0.0.0.0", "::", "127.0.0.1", "localhost", "::1")
        else bind_host
    )
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    url = f"http://{display_host}:{port}"
    port_file = _core.COMMAND_CENTER_STATE_DIR / "port.txt"
    try:
        _core.COMMAND_CENTER_STATE_DIR.mkdir(parents=True, exist_ok=True)
        port_file.write_text(url + "\n")
        print(f"  [skill] port file: {port_file} -> {url}")
    except OSError as e:
        print(f"  [skill] could not write port file ({e})")
    return url


def _install_skill(name: str, skills_root: Path = None):
    """Install (or refresh) a bundled skill into <skills_root>/<name>/SKILL.md
    (default ~/.claude/skills). Idempotent — only writes when the source
    differs from the destination."""
    import shutil
    src = _core.CCC_ROOT / "skills" / f"{name}.md"
    if not src.exists():
        print(f"  [skill] source not found at {src}; skipping")
        return
    if skills_root is None:
        skills_root = Path.home() / ".claude" / "skills"
    dst_dir = skills_root / name
    dst = dst_dir / "SKILL.md"
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            print(f"  [skill] {name} already up to date ({skills_root})")
            return
        shutil.copy2(src, dst)
        print(f"  [skill] installed {name} -> {dst}")
    except OSError as e:
        print(f"  [skill] could not install {name} ({e})")


def _skill_install_roots():
    """Skill destinations: always ~/.claude/skills; also ~/.codex/skills when
    Codex is present (its home dir exists or its CLI resolves). Codex reads
    the same agent-skills SKILL.md layout and ignores Claude-only frontmatter."""
    roots = [Path.home() / ".claude" / "skills"]
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    try:
        codex_present = codex_home.is_dir() or _core._resolve_codex_bin().get("available")
    except Exception:
        codex_present = False
    if codex_present:
        roots.append(codex_home / "skills")
    return roots


def install_orchestration_skill():
    """Install all bundled CCC skills. Skipped when CCC_SKIP_SKILL_INSTALL=1."""
    if os.environ.get("CCC_SKIP_SKILL_INSTALL", "").strip().lower() in ("1", "true", "yes", "on"):
        print("  [skill] install skipped (CCC_SKIP_SKILL_INSTALL=1)")
        return
    for root in _skill_install_roots():
        _install_skill("ccc-orchestration", root)
        _install_skill("group-chat-checkin", root)
        # W86 ecosystem glue: bridge superpowers plans to Watchtower queues,
        # and spawn browser-driven verification lanes. Installed so the
        # "CCC works with your skills" integrations are present out of the box.
        _install_skill("superpowers-to-watchtower", root)
        _install_skill("fleet-verify", root)


def _raise_open_file_limit(min_soft=2048):
    """Raise the soft RLIMIT_NOFILE when launchd starts us with a tiny default."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft >= min_soft:
            return
        target = min_soft
        if hard not in (resource.RLIM_INFINITY, -1):
            target = min(target, hard)
        if target > soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            print(f"  [limits] max open files: {soft} -> {target}")
    except Exception as e:
        print(f"  [limits] could not raise max open files ({e})")


# ── Anonymous opt-in telemetry ──────────────────────────────────────────────
#
# Five fields. Off by default. Inspectable on disk. See docs/telemetry.md.
#
# WHAT IS SENT (the entire payload, no exceptions):
#   1. install_id        — random UUIDv4, generated locally on first launch.
#                          Never derived from machine identity (hostname,
#                          MAC, username, git config, etc.). Stored at
#                          ~/.config/claude-command-center/install-id (0600).
#   2. version           — the __version__ string from this file.
#   3. platform          — sys.platform value ("darwin", "linux", ...).
#   4. engines           — comma-list of installed CLI engines among
#                          {claude, codex, gemini, cursor, antigravity}, derived only from
#                          "is the binary available" — NO usage signal,
#                          NO per-engine counts, NO version probing.
#   5. last_active_date  — ISO date only (YYYY-MM-DD) of the most recent
#                          transcript mtime under ~/.claude/projects/.
#                          NO clock time, NO session count, NO repo info.
#
# WHAT IS NEVER SENT (the trust anchor):
#   - Prompt content, transcripts, conversation events, tool calls.
#   - Session counts, usage volume, per-session timing, token counts.
#   - Repo paths, repo names, file paths, branch names, cwd.
#   - User identity: name, email, hostname, git config, login, IP.
#   - Errors, exception traces, log lines, stack traces.
#   - Anything from the dashboard UI: clicks, searches, navigation.
#
# Server-side: the receiving Cloudflare Worker drops the source IP before
# logging. See docs/telemetry.md for the full contract. This guarantee is
# not enforced from the client — auditors should read the Worker source
# under infra/telemetry-worker/.
#
# KILL SWITCHES (any one wins, checked at every fire):
#   1. Env var CCC_TELEMETRY_DISABLED in {"1","true","yes","on"} — no code runs.
#   2. ~/.config/claude-command-center/telemetry.json opt_in == false.
#   3. Missing install-id file → skip the ping and re-show the opt-in bar.
#
# Cadence: once per UTC day. Background thread checks every hour. First
# attempt is delayed 30s after server start so the dashboard loads first.
# Fire-and-forget over urllib (stdlib only); 10s connect / 15s total timeout;
# no retries. Offline / DNS-fail / non-200 → silent skip, no log spam.
#
# All telemetry log lines are tagged `[telemetry]` so users grepping the
# server log can audit exactly when (and whether) anything fires.

_TELEMETRY_SCHEMA_VERSION = 3
# Heartbeat cadence (client beats every N seconds while the dashboard
# tab is visible). Each accepted beat credits N seconds of "active"
# time to today's bucket. Picked to be coarse enough that the count is
# privacy-friendly (no per-action timing) but fine enough to be useful.
_TELEMETRY_ACTIVE_HEARTBEAT_S = 30
_TELEMETRY_DEFAULT_ENDPOINT = (
    "https://telemetry.claude-command-center.workers.dev/v1/ping"
)
# Anonymous open beacon. Fires at most ONCE PER UTC DAY while the server
# is running, not gated on opt-in (carries NO install_id and NO identity —
# three fields total: schema, version, platform). Daily rather than
# per-boot so the aggregate answers "how many installs ran today", which
# a boot-only beacon cannot: an install left running under launchd for a
# week produces zero boots. Restart-heavy machines now send *fewer* bytes
# than before, not more. Still honors the CCC_TELEMETRY_DISABLED env var
# so users have one switch that kills every wire byte from this process.
_TELEMETRY_OPEN_DEFAULT_ENDPOINT = (
    "https://telemetry.claude-command-center.workers.dev/v1/open"
)
_TELEMETRY_STATE_DIR_PATH = Path.home() / ".config" / "claude-command-center"
_TELEMETRY_DOCS_URL = (
    "https://github.com/amirfish1/claude-command-center/blob/main/docs/telemetry.md"
)
_TELEMETRY_INITIAL_DELAY_S = 30
_TELEMETRY_CHECK_INTERVAL_S = 3600  # 1 hour
_TELEMETRY_CONNECT_TIMEOUT_S = 10
_TELEMETRY_TOTAL_TIMEOUT_S = 15
_TELEMETRY_STATE_LOCK = threading.Lock()


def _telemetry_state_dir():
    """Return the dir holding telemetry state (install-id, opt-in, last-ping).

    Created on first use with mode 0700 so the install-id and consent record
    aren't world-readable on a shared machine.
    """
    d = _TELEMETRY_STATE_DIR_PATH
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir respects umask — re-chmod to be sure.
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    except OSError as e:
        print(f"  [telemetry] could not create state dir {d}: {e}")
    return d


def _telemetry_install_id_path():
    return _core._telemetry_state_dir() / "install-id"


def _telemetry_state_path():
    return _core._telemetry_state_dir() / "telemetry.json"


def _telemetry_last_ping_path():
    return _core._telemetry_state_dir() / "telemetry-last-ping"


def _telemetry_last_open_path():
    """Date of the last anonymous open beacon (YYYY-MM-DD, UTC).

    Local-only bookkeeping so the beacon fires at most once per UTC day
    across restarts. Nothing about this file ever goes on the wire.
    """
    return _core._telemetry_state_dir() / "telemetry-last-open"


def _telemetry_disabled_env():
    """Env-var kill switch. Liberal: 1/true/yes/on (case-insensitive)."""
    v = (os.environ.get("CCC_TELEMETRY_DISABLED") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _telemetry_load_or_init_install_id():
    """Return the UUIDv4 install-id, generating + writing if missing.

    Returns the existing id when present (idempotent). If the file is
    missing or unreadable, generates a fresh UUIDv4, writes it with mode
    0600, and returns the new id. Returns None only if disk writes fail.
    """
    import uuid
    p = _core._telemetry_install_id_path()
    with _TELEMETRY_STATE_LOCK:
        try:
            if p.is_file():
                txt = p.read_text(encoding="utf-8").strip()
                if txt:
                    return txt
        except OSError:
            pass
        new_id = str(uuid.uuid4())
        try:
            p.write_text(new_id + "\n", encoding="utf-8")
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
            return new_id
        except OSError as e:
            print(f"  [telemetry] could not write install-id ({e})")
            return None


def _telemetry_install_id_present():
    try:
        return _core._telemetry_install_id_path().is_file()
    except OSError:
        return False


def _load_telemetry_state():
    """Read the opt-in JSON. Returns a normalized dict; missing == 'not asked'."""
    p = _core._telemetry_state_path()
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return {"opt_in": None, "asked_at": None, "endpoint": None}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"opt_in": None, "asked_at": None, "endpoint": None}
    if not isinstance(data, dict):
        return {"opt_in": None, "asked_at": None, "endpoint": None}
    opt_in = data.get("opt_in")
    if opt_in is not None and not isinstance(opt_in, bool):
        opt_in = None
    asked_at = data.get("asked_at")
    if asked_at is not None and not isinstance(asked_at, str):
        asked_at = None
    endpoint = data.get("endpoint")
    if endpoint is not None and not isinstance(endpoint, str):
        endpoint = None
    return {"opt_in": opt_in, "asked_at": asked_at, "endpoint": endpoint}


def _save_telemetry_state(state):
    """Persist the opt-in JSON with mode 0600."""
    p = _core._telemetry_state_path()
    payload = {
        "opt_in": state.get("opt_in"),
        "asked_at": state.get("asked_at"),
        "endpoint": state.get("endpoint"),
    }
    with _TELEMETRY_STATE_LOCK:
        try:
            _core._telemetry_state_dir()
            p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
            return True
        except OSError as e:
            print(f"  [telemetry] could not write state ({e})")
            return False


def _telemetry_resolved_endpoint():
    return (
        (os.environ.get("CCC_TELEMETRY_ENDPOINT") or "").strip()
        or _TELEMETRY_DEFAULT_ENDPOINT
    )


def _telemetry_detect_engines():
    """List installed engines, in canonical order. 'is the binary available'
    only — no version probe, no usage signal."""
    out = []
    try:
        if _core._resolve_claude_bin().get("available"):
            out.append("claude")
    except Exception:
        pass
    try:
        if _core._resolve_codex_bin().get("available"):
            out.append("codex")
    except Exception:
        pass
    try:
        if _core._resolve_gemini_bin().get("available"):
            out.append("gemini")
    except Exception:
        pass
    try:
        if _core._resolve_cursor_bin().get("available"):
            out.append("cursor")
    except Exception:
        pass
    try:
        if _core._resolve_antigravity_bin().get("available"):
            out.append("antigravity")
    except Exception:
        pass
    try:
        if _core._resolve_kilo_bin().get("available"):
            out.append("kilo")
    except Exception:
        pass
    try:
        if _core._resolve_opencode_bin().get("available"):
            out.append("opencode")
    except Exception:
        pass
    return out


def _telemetry_last_active_date():
    """Most recent transcript activity date (YYYY-MM-DD) under PROJECTS_ROOT.

    Returns "" when no transcripts exist. Uses file mtime — no transcript
    content is opened. Date only; no clock time goes into the payload.
    """
    root = _core.PROJECTS_ROOT
    try:
        if not root.is_dir():
            return ""
    except OSError:
        return ""
    newest = 0.0
    try:
        for project_dir in root.iterdir():
            if not project_dir.is_dir():
                continue
            try:
                for jsonl in project_dir.iterdir():
                    if not jsonl.name.endswith(".jsonl"):
                        continue
                    try:
                        m = jsonl.stat().st_mtime
                    except OSError:
                        continue
                    if m > newest:
                        newest = m
            except OSError:
                continue
    except OSError:
        return ""
    if newest <= 0:
        return ""
    try:
        return datetime.fromtimestamp(newest, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, ValueError):
        return ""


def _telemetry_count_sessions_today():
    """Count distinct JSONL transcripts modified in the last 24h.

    Coarse "daily session count" proxy: scans PROJECTS_ROOT, returns the
    number of *.jsonl files whose mtime falls in the last 24h. No content
    is opened. Capped at 100000 to keep the payload bounded.
    """
    root = _core.PROJECTS_ROOT
    try:
        if not root.is_dir():
            return 0
    except OSError:
        return 0
    cutoff = time.time() - 86400
    n = 0
    try:
        for project_dir in root.iterdir():
            if not project_dir.is_dir():
                continue
            try:
                for jsonl in project_dir.iterdir():
                    if not jsonl.name.endswith(".jsonl"):
                        continue
                    try:
                        if jsonl.stat().st_mtime >= cutoff:
                            n += 1
                            if n >= 100000:
                                return n
                    except OSError:
                        continue
            except OSError:
                continue
    except OSError:
        return n
    return n


def _telemetry_count_total_sessions_managed():
    """Count every JSONL transcript ever seen under PROJECTS_ROOT.

    Lifetime "sessions CCC has indexed" proxy. Same scan shape as the
    24h counter, just without the mtime cutoff. Capped at 10000000 to
    keep the payload bounded; the dashboard cap on its own poll
    rendering is far below that.
    """
    root = _core.PROJECTS_ROOT
    try:
        if not root.is_dir():
            return 0
    except OSError:
        return 0
    n = 0
    try:
        for project_dir in root.iterdir():
            if not project_dir.is_dir():
                continue
            try:
                for jsonl in project_dir.iterdir():
                    if not jsonl.name.endswith(".jsonl"):
                        continue
                    n += 1
                    if n >= 10_000_000:
                        return n
            except OSError:
                continue
    except OSError:
        return n
    return n


def _telemetry_active_state_path():
    return _core._telemetry_state_dir() / "telemetry-active.json"


def _telemetry_today_utc():
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _telemetry_load_active_state():
    """Read the rolling daily "active seconds" bucket. Resets on day
    rollover. Stored under telemetry state dir alongside other opt-in
    files; mode 0600."""
    p = _telemetry_active_state_path()
    today = _telemetry_today_utc()
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("date") == today:
            seconds = data.get("seconds")
            if isinstance(seconds, int) and 0 <= seconds <= 86400:
                return {"date": today, "seconds": seconds}
    except (OSError, ValueError, TypeError):
        pass
    return {"date": today, "seconds": 0}


def _telemetry_record_heartbeat():
    """Credit one heartbeat (_TELEMETRY_ACTIVE_HEARTBEAT_S seconds) to
    today's active-seconds bucket. Honors CCC_TELEMETRY_DISABLED. Caps
    at 86400 (full day). Returns the new bucket state."""
    if _core._telemetry_disabled_env():
        return {"date": _telemetry_today_utc(), "seconds": 0}
    state = _telemetry_load_active_state()
    state["seconds"] = min(86400, state["seconds"] + _TELEMETRY_ACTIVE_HEARTBEAT_S)
    p = _telemetry_active_state_path()
    with _TELEMETRY_STATE_LOCK:
        try:
            _core._telemetry_state_dir()
            p.write_text(json.dumps(state) + "\n", encoding="utf-8")
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        except OSError:
            pass
    return state


def _telemetry_active_seconds_today():
    return _telemetry_load_active_state()["seconds"]


def _build_telemetry_payload():
    """Assemble the schema-v3 dict. Returns None when no install-id is available."""
    install_id = _core._telemetry_load_or_init_install_id()
    if not install_id:
        return None
    payload = {
        "schema_version": _TELEMETRY_SCHEMA_VERSION,
        "install_id": install_id,
        "version": _core.__version__,
        "platform": sys.platform,
        "engines": ",".join(_telemetry_detect_engines()),
        "last_active_date": _telemetry_last_active_date(),
        "sessions_today": _telemetry_count_sessions_today(),
        "active_seconds_today": _telemetry_active_seconds_today(),
        "total_sessions_managed": _telemetry_count_total_sessions_managed(),
    }
    # Same maintainer marker the anonymous beacon carries. Lets the public
    # stats page report user counts both with and without the maintainer's
    # own machine instead of quietly counting it as a user.
    if _telemetry_dev_mode_env():
        payload["dev"] = True
    return payload


def _telemetry_resolved_open_endpoint():
    """Endpoint for the anonymous open beacon. Derive from the opt-in
    endpoint if CCC_TELEMETRY_ENDPOINT is set (swap /v1/ping → /v1/open)
    so forks / staging proxies just need one env var."""
    custom = (os.environ.get("CCC_TELEMETRY_ENDPOINT") or "").strip()
    if custom:
        if custom.endswith("/v1/ping"):
            return custom[: -len("/v1/ping")] + "/v1/open"
        return custom.rstrip("/") + "/v1/open"
    return _TELEMETRY_OPEN_DEFAULT_ENDPOINT


def _telemetry_dev_mode_env():
    """Maintainer's own-machine flag. When set, the beacon carries
    dev:true so the public stats page can filter these rows out —
    otherwise the maintainer's frequent restarts inflate boot counts.
    Adds no identity; the flag is a plain boolean stored server-side."""
    v = (os.environ.get("CCC_TELEMETRY_DEV_MODE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _send_telemetry_open_beacon():
    """Fire-and-forget POST of the anonymous open beacon.

    Not gated on opt-in by design: the payload carries NO install_id and
    NO identifying data, so the privacy contract holds without per-user
    consent. The CCC_TELEMETRY_DISABLED env var still kills it; that is
    the single switch users have for the whole process.
    """
    if _core._telemetry_disabled_env():
        return False
    payload = {
        "schema_version": 1,
        "version": _core.__version__,
        "platform": sys.platform,
    }
    if _telemetry_dev_mode_env():
        payload["dev"] = True
    try:
        data = json.dumps(payload).encode("utf-8")
    except (TypeError, ValueError):
        return False
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"claude-command-center/{_core.__version__} (telemetry-open)",
    }
    req = urllib.request.Request(
        _telemetry_resolved_open_endpoint(), data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=_TELEMETRY_TOTAL_TIMEOUT_S) as resp:
            status = getattr(resp, "status", 0) or 0
            return 200 <= status < 300
    except Exception:
        return False


def _telemetry_read_last_open_date():
    try:
        s = _telemetry_last_open_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    # Stored as YYYY-MM-DD; reject anything else.
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return ""


def _telemetry_write_last_open_date(date_str):
    try:
        _core._telemetry_state_dir()
        _telemetry_last_open_path().write_text(date_str + "\n", encoding="utf-8")
        try:
            os.chmod(_telemetry_last_open_path(), 0o600)
        except OSError:
            pass
        return True
    except OSError:
        return False


def _maybe_send_telemetry_open_beacon():
    """Send the anonymous beacon if it hasn't gone out yet this UTC day.

    Returns one of: "disabled-env", "already-today", "sent", "failed".
    The string is only used for log routing by the background loop.
    """
    if _core._telemetry_disabled_env():
        return "disabled-env"
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    last = _core._telemetry_read_last_open_date()
    if last and last >= today:
        return "already-today"
    if _core._send_telemetry_open_beacon():
        _core._telemetry_write_last_open_date(today)
        return "sent"
    return "failed"


def _telemetry_open_beacon_loop():
    """Daemon thread target. Sleeps the same initial delay as the opt-in
    loop so the dashboard paints first, then fires the beacon at most once
    per UTC day for as long as this process lives.

    The at-most-once-a-day gate is a date file in the telemetry state dir,
    so a restart (or twenty) inside the same day sends nothing extra."""
    try:
        time.sleep(_TELEMETRY_INITIAL_DELAY_S)
    except Exception:
        return
    while True:
        try:
            if _core._maybe_send_telemetry_open_beacon() == "sent":
                print("  [telemetry] anonymous daily beacon sent")
        except Exception:
            # Defensive: never crash the daemon thread.
            pass
        try:
            time.sleep(_TELEMETRY_CHECK_INTERVAL_S)
        except Exception:
            return


def _telemetry_read_last_ping_date():
    try:
        s = _telemetry_last_ping_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    # Stored as YYYY-MM-DD; reject anything else.
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return ""


def _telemetry_write_last_ping_date(date_str):
    try:
        _core._telemetry_state_dir()
        _telemetry_last_ping_path().write_text(date_str + "\n", encoding="utf-8")
        try:
            os.chmod(_telemetry_last_ping_path(), 0o600)
        except OSError:
            pass
        return True
    except OSError:
        return False


def _send_telemetry_ping(payload, endpoint=None):
    """POST the payload. Fire-and-forget; returns True on 2xx, False otherwise.

    No retries, no log spam. Network failures / DNS errors / non-200 all
    return False silently so a missing Worker doesn't fill the log.
    """
    if not payload:
        return False
    url = endpoint or _core._telemetry_resolved_endpoint()
    try:
        data = json.dumps(payload).encode("utf-8")
    except (TypeError, ValueError):
        return False
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"claude-command-center/{_core.__version__} (telemetry)",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TELEMETRY_TOTAL_TIMEOUT_S) as resp:
            status = getattr(resp, "status", 0) or 0
            return 200 <= status < 300
    except Exception:
        return False


def _maybe_send_telemetry():
    """Send a daily ping if (and only if) every gate passes.

    Returns one of: "disabled-env", "no-opt-in", "no-install-id",
    "already-today", "sent", "failed". The string is used by the
    background loop for log routing only.
    """
    if _core._telemetry_disabled_env():
        return "disabled-env"
    state = _core._load_telemetry_state()
    if state.get("opt_in") is not True:
        return "no-opt-in"
    if not _core._telemetry_install_id_present():
        # Treat missing id as "user reset" — fall back to never-asked
        # behavior. The state JSON is intentionally left alone so the user
        # can re-opt-in via the dashboard; we just don't ping.
        return "no-install-id"
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    last = _core._telemetry_read_last_ping_date()
    if last and last >= today:
        return "already-today"
    payload = _core._build_telemetry_payload()
    if not payload:
        return "no-install-id"
    ok = _core._send_telemetry_ping(payload)
    if ok:
        _telemetry_write_last_ping_date(today)
        return "sent"
    return "failed"


def _telemetry_loop():
    """Background daemon: initial delay, then hourly check + maybe-send.

    Quietly no-ops when telemetry is disabled or the user hasn't opted in.
    """
    try:
        time.sleep(_TELEMETRY_INITIAL_DELAY_S)
    except Exception:
        return
    while True:
        try:
            result = _core._maybe_send_telemetry()
            if result == "sent":
                print("  [telemetry] daily ping sent")
            elif result == "failed":
                # Don't log every failure — that's log spam if the Worker
                # isn't deployed. We'd just retry next hour anyway.
                pass
        except Exception:
            # Defensive: never crash the daemon thread.
            pass
        try:
            time.sleep(_TELEMETRY_CHECK_INTERVAL_S)
        except Exception:
            return

