# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 75409-75653).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

import re

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Fleet recommendations — deterministic rules over the inventory
#
# Pure function: inventory in, ordered action list out. Every action carries
# its reason and EVERY unmet gate as an explicit blocker; nothing destructive
# is ever recommended without objective proof (clean tree + head provably
# reachable from origin's default branch, or a merged PR). Evidence over
# guesses: attribution is separate and never fabricates a single owner.
# ---------------------------------------------------------------------------


def _fleet_action(kind, repo_identity, node, *, target=None, reason="",
                  evidence=None, blockers=None, destructive=False,
                  command_intent="", requires=None):
    node_id = node.get("node_id") if isinstance(node, dict) else node
    ident_slug = re.sub(r"[^A-Za-z0-9]+", "-", repo_identity or "repo")
    target_slug = re.sub(r"[^A-Za-z0-9]+", "-", str(target or ""))[:60]
    return {
        "id": f"{kind}:{ident_slug}:{(node_id or '')[:8]}:{target_slug}",
        "kind": kind,
        "repo_identity": repo_identity,
        "node_id": node_id,
        "node_name": node.get("name") if isinstance(node, dict) else None,
        "target": target,
        "reason": reason,
        "evidence": evidence or {},
        "blockers": blockers or [],
        "destructive": destructive,
        "command_intent": command_intent,
        "requires": requires or [],
        "ready": not (blockers or []),
    }


def _fleet_recommendations(inventory):
    """Build deterministic recommendations from a fleet inventory payload."""
    actions = []
    nodes_by_id = {n["node_id"]: n for n in inventory.get("nodes", [])}
    for repo in inventory.get("repos", []):
        ident = repo.get("identity")
        entries = repo.get("nodes", {})
        # Which node (if any) has published the newest origin default head?
        for node_id, entry in entries.items():
            node = nodes_by_id.get(node_id, {"node_id": node_id})
            if not isinstance(entry, dict) or not entry.get("ok"):
                if isinstance(entry, dict) and entry.get("error"):
                    actions.append(_fleet_action(
                        "investigate_source", ident, node,
                        target=entry.get("error"),
                        reason=f"inventory for this repo failed on "
                               f"{node.get('name') or node_id[:8]}: "
                               f"{entry.get('detail') or entry.get('error')}",
                        command_intent="fix the collection error, then refresh"))
                continue
            if entry.get("stale"):
                actions.append(_fleet_action(
                    "investigate_source", ident, node, target="stale",
                    reason="data for this node is stale (peer unreachable) - "
                           "conclusions below may be outdated",
                    command_intent="restore connectivity, then refresh"))
            default_branch = (entry.get("default_branch") or {}).get("branch")
            default_sha = (entry.get("default_branch") or {}).get("sha")
            prs = entry.get("prs") or {}
            prs_by_branch = {p.get("branch"): p for p in prs.get("open", [])}
            sessions = entry.get("sessions") or []
            for wt in entry.get("worktrees", []):
                actions.extend(_fleet_worktree_actions(
                    ident, node, entry, wt, default_branch, default_sha,
                    prs_by_branch, sessions))
            # PR-level actions (independent of local worktrees)
            for pr in prs.get("open", []):
                actions.extend(_fleet_pr_actions(ident, node, entry, pr))
            # Deployment is its own dimension: a failed deploy of a pushed
            # commit is NEVER solved by another push.
            deploy = entry.get("deployment") or {}
            if (deploy.get("state") or "").upper() == "ERROR":
                actions.append(_fleet_action(
                    "investigate_deploy", ident, node,
                    target=deploy.get("commit_sha"),
                    reason="production deployment failed for commit "
                           f"{deploy.get('commit_sha') or '?'} — Git state is "
                           "complete; investigate the deployment itself",
                    evidence={"deployment": deploy},
                    command_intent="inspect provider logs / run the deploy "
                                   "fix flow; do NOT push again"))
    # Dependency ordering: commits before pushes before pulls/PR actions
    # before cleanup.
    order = {"ask_commit": 0, "publish_branch": 1, "push": 1, "pull_ff": 2,
             "mark_ready": 3, "merge_pr": 4, "remove_worktree": 5,
             "finish_worktree": 3, "investigate_deploy": 3,
             "investigate_source": 0, "review_orphan": 4,
             "flag_for_human": 6}
    actions.sort(key=lambda a: (a["repo_identity"] or "", order.get(a["kind"], 9),
                                a["node_id"] or ""))
    return actions


def _fleet_worktree_actions(ident, node, entry, wt, default_branch,
                            default_sha, prs_by_branch, sessions):
    out = []
    wt_path = wt.get("path")
    branch = wt.get("branch")
    wt_sessions = [s for s in sessions
                   if (s.get("cwd") or "").rstrip("/") == (wt_path or "").rstrip("/")]
    if wt.get("dirty"):
        candidates = wt_sessions or sessions
        out.append(_fleet_action(
            "ask_commit", ident, node, target=wt_path,
            reason=f"{wt.get('dirty_files')} dirty file(s) on branch "
                   f"{branch or '(detached)'} — work should be committed by "
                   "whoever owns it, never swept blindly",
            evidence={
                "dirty_files": wt.get("dirty_files"),
                "staged": wt.get("staged"),
                "untracked": wt.get("untracked"),
                "candidate_sessions": candidates[:5],
                "attribution": ("session-cwd match" if wt_sessions
                                else ("repo-level candidates only" if sessions
                                      else "unknown — no session evidence")),
            },
            command_intent="ping the owning session to commit "
                           "(git commit --only <its paths>)"))
    unpublished = wt.get("unpublished_commits")
    if unpublished:
        blockers = []
        if wt.get("dirty"):
            blockers.append({"code": "dirty_worktree",
                             "detail": "commit or clean the tree first"})
        if not branch:
            blockers.append({"code": "detached_head",
                             "detail": "create a branch before publishing"})
        out.append(_fleet_action(
            "push", ident, node, target=branch or wt_path,
            reason=f"{unpublished} commit(s) on {branch or 'detached HEAD'} "
                   "are unreachable from origin — only this node has them",
            evidence={"unpublished_commits": unpublished,
                      "head_sha": wt.get("head_sha")},
            blockers=blockers,
            command_intent=f"git push origin {branch or 'HEAD'}"))
    if (wt.get("is_primary_clone") and branch and default_branch
            and branch == default_branch and default_sha
            and wt.get("head_sha") and wt["head_sha"] != default_sha
            and not unpublished):
        blockers = []
        if wt.get("dirty"):
            blockers.append({"code": "dirty_worktree",
                             "detail": "clean the tree before pulling"})
        out.append(_fleet_action(
            "pull_ff", ident, node, target=branch,
            reason=f"origin/{default_branch} moved (another node pushed) - "
                   "this clone is behind",
            evidence={"local_head": wt.get("head_sha"),
                      "origin_head": default_sha},
            blockers=blockers,
            command_intent=f"git merge --ff-only origin/{default_branch}"))
    # Cleanup / preservation for secondary worktrees
    if not wt.get("is_primary_clone"):
        merged = wt.get("merged_into_default")
        pr = prs_by_branch.get(branch)
        if not wt.get("dirty") and merged is True and not unpublished:
            out.append(_fleet_action(
                "remove_worktree", ident, node, target=wt_path,
                reason=f"worktree is clean and its head is provably reachable "
                       f"from origin/{default_branch} — the work is merged",
                evidence={"head_sha": wt.get("head_sha"),
                          "merged_into_default": True,
                          "proof": f"git merge-base --is-ancestor "
                                   f"{(wt.get('head_sha') or '')[:12]} "
                                   f"origin/{default_branch}"},
                destructive=True,
                command_intent=f"git worktree remove {wt_path} "
                               "(branch deletion is a separate action)"))
        elif wt.get("dirty") or unpublished or merged is False:
            why = []
            if wt.get("dirty"):
                why.append(f"{wt.get('dirty_files')} dirty file(s)")
            if unpublished:
                why.append(f"{unpublished} unpublished commit(s)")
            if merged is False:
                why.append("head not reachable from the default branch")
            out.append(_fleet_action(
                "finish_worktree", ident, node, target=wt_path,
                reason="worktree must be preserved (" + ", ".join(why) + ") - "
                       "offer a finish path instead of deletion",
                evidence={
                    "candidate_sessions": wt_sessions[:5],
                    "pr": {"number": pr.get("number"), "url": pr.get("url")}
                    if pr else None,
                },
                blockers=[{"code": "unmerged_or_dirty",
                           "detail": "never delete unproven work"}],
                command_intent="wake the owning session or spawn a focused "
                               "finishing session in this worktree"))
    return out


def _fleet_pr_actions(ident, node, entry, pr):
    out = []
    checks_ok = not pr.get("checks_failing") and not pr.get("checks_pending")
    gates = []
    if pr.get("checks_failing"):
        gates.append({"code": "checks_failing",
                      "detail": f"failing checks: {', '.join(pr['checks_failing'][:5])}"})
    if pr.get("checks_pending"):
        gates.append({"code": "checks_pending",
                      "detail": f"checks still running: {', '.join(pr['checks_pending'][:5])}"})
    if (pr.get("mergeable") or "").upper() == "CONFLICTING":
        gates.append({"code": "merge_conflict",
                      "detail": "branch conflicts with the base"})
    if pr.get("draft"):
        out.append(_fleet_action(
            "mark_ready", ident, node, target=f"#{pr.get('number')}",
            reason=f"draft PR #{pr.get('number')} ({pr.get('branch')}) has "
                   + ("green checks and no conflicts — ready for review"
                      if checks_ok and not gates else "unmet gates"),
            evidence={"pr": pr},
            blockers=gates,
            command_intent=f"gh pr ready {pr.get('number')}"))
    else:
        merge_gates = list(gates)
        review = (pr.get("review_decision") or "").upper()
        if review == "CHANGES_REQUESTED":
            merge_gates.append({"code": "changes_requested",
                                "detail": "review requested changes"})
        elif review == "REVIEW_REQUIRED":
            merge_gates.append({"code": "review_required",
                                "detail": "required review is missing"})
        out.append(_fleet_action(
            "merge_pr", ident, node, target=f"#{pr.get('number')}",
            reason=f"open PR #{pr.get('number')} ({pr.get('branch')})"
                   + (" meets every objective gate" if not merge_gates
                      else " has unmet gates"),
            evidence={"pr": pr},
            blockers=merge_gates,
            command_intent=f"gh pr merge {pr.get('number')} --squash"))
    if pr.get("checks_failing"):
        out.append(_fleet_action(
            "investigate_checks", ident, node, target=f"#{pr.get('number')}",
            reason=f"PR #{pr.get('number')} has failing checks - investigate "
                   "independently of Git state",
            evidence={"failing": pr.get("checks_failing")},
            command_intent="open the failing check logs"))
    return out

