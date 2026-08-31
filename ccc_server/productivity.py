"""Extracted from server.py (originally lines 41773-43163).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
from productivity import (
    ProductivityStore,
    aggregate_productivity,
    collect_git_commits,
    describe_git_repo,
    discover_git_identities,
    presence_health,
    presence_summary,
    run_presence_sampler,
    system_local_timezone,
)
import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Productivity dashboard — outcome, project, activity, and time evidence.
#
# Unlike Throughput, this is a 16-week cross-source view.  Its expensive build
# never runs on the request thread: callers receive the last SQLite snapshot
# immediately and one shared worker refreshes Git/transcript/WatchTower data.
# ---------------------------------------------------------------------------

_PRODUCTIVITY_SCHEMA = 3
_PRODUCTIVITY_WEEKS = (6, 8, 12, 16)
_PRODUCTIVITY_CACHE_TTL = 15 * 60
_PRODUCTIVITY_STORE = ProductivityStore(_core.COMMAND_CENTER_STATE_DIR / "productivity.db")
_PRODUCTIVITY_REFRESH_LOCK = threading.Lock()
_PRODUCTIVITY_REFRESH = {
    "state": "idle",
    "started_at": None,
    "completed_at": None,
    "error": None,
    "error_code": None,
}


def _productivity_safe_project_name(value):
    text = " ".join(str(value or "").split()).strip()
    return text[:120] or "Unknown project"


def _productivity_known_repos(conversations):
    """Describe every CCC-observed Git repo, grouped by remote identity.

    Returned ``path``/``paths`` values are private collection details and are
    never copied into the API payload.  Warnings contain only directory names.
    """
    candidates = [str(Path.cwd())]
    candidates.extend(_core._load_recent_repos())
    candidates.extend(_core._load_custom_repos())
    for config in (_core._wt_read_config() or {}).values():
        if isinstance(config, dict) and config.get("repo_path"):
            candidates.append(str(config["repo_path"]))
    for row in conversations or []:
        if not isinstance(row, dict):
            continue
        path = (
            row.get("session_cwd")
            or row.get("cwd")
            or row.get("folder_path")
            or row.get("repo_path")
        )
        if path:
            candidates.append(str(path))

    described = {}
    warnings = []
    seen_paths = set()
    for candidate in candidates:
        try:
            resolved = str(Path(candidate).expanduser().resolve(strict=False))
        except (OSError, ValueError):
            continue
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        try:
            repo = _core.describe_git_repo(resolved)
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
            name = Path(resolved).name
            if name:
                warnings.append(f"Git unavailable for {name}")
            continue
        repo_id = str(repo.get("id") or "")
        if not repo_id:
            continue
        root = str(repo.get("path") or resolved)
        existing = described.get(repo_id)
        if existing:
            if root not in existing["paths"]:
                existing["paths"].append(root)
            if resolved not in existing["paths"]:
                existing["paths"].append(resolved)
            continue
        described[repo_id] = {
            "id": repo_id,
            "name": _productivity_safe_project_name(repo.get("name")),
            "path": root,
            "paths": list(dict.fromkeys([root, resolved])),
            "identity": str(repo.get("identity") or ""),
        }
    return list(described.values()), sorted(set(warnings))


def _productivity_project_for_path(path, repositories):
    try:
        target = Path(path).expanduser().resolve(strict=False)
    except (OSError, ValueError, TypeError):
        target = None
    if target is not None:
        for repo in repositories:
            for candidate in repo.get("paths") or [repo.get("path")]:
                try:
                    root = Path(candidate).expanduser().resolve(strict=False)
                    if target == root or root in target.parents:
                        return {"id": repo["id"], "name": repo["name"]}
                except (OSError, ValueError, TypeError):
                    continue
    return None


def _productivity_queue_projects(repositories):
    out = {}
    for queue, config in (_core._wt_read_config() or {}).items():
        config = config if isinstance(config, dict) else {}
        project = _core._productivity_project_for_path(config.get("repo_path"), repositories)
        if project:
            out[str(queue).upper()] = project
    return out


def _productivity_normalize_ticket(item, queue_projects):
    if not isinstance(item, dict):
        return None
    queue = str(item.get("project") or item.get("queue") or "WatchTower").strip()
    queue_key = queue.upper()
    project = queue_projects.get(queue_key)
    if not project:
        project = {
            "id": "watchtower-" + hashlib.sha256(queue_key.encode("utf-8")).hexdigest()[:12],
            "name": _productivity_safe_project_name(queue),
        }
    raw_kind = str(item.get("type") or item.get("item_type") or "").strip().lower()
    if raw_kind in ("feature", "feat"):
        kind = "feature"
    elif raw_kind in ("bug", "fix"):
        kind = "fix"
    else:
        kind = "other"
    ref = str(item.get("ref") or item.get("id") or "").strip()
    return {
        "ref": ref,
        "project_id": project["id"],
        "project_name": project["name"],
        "kind": kind,
        "status": str(item.get("status") or ""),
        "title": _productivity_safe_project_name(
            item.get("title") or item.get("note") or item.get("text") or ref
        ),
        "created_at": item.get("created_at"),
        "closed_at": item.get("closed_at"),
    }


def _productivity_turn_rows(conversations, repositories, cutoff_epoch):
    turns = []
    considered = parsed_count = error_count = 0
    for row in conversations or []:
        if not isinstance(row, dict):
            continue
        modified = row.get("last_interacted") or row.get("modified") or row.get("mtime") or 0
        try:
            if float(modified or 0) < cutoff_epoch:
                continue
        except (TypeError, ValueError):
            pass
        project = _core._productivity_project_for_path(
            row.get("session_cwd")
            or row.get("cwd")
            or row.get("folder_path")
            or row.get("repo_path"),
            repositories,
        )
        if not project:
            continue
        sid = str(row.get("session_id") or row.get("id") or "").strip()
        if not sid:
            continue
        considered += 1
        engine = str(row.get("engine") or row.get("source") or "claude").lower()
        is_codex = engine == "codex"
        name = row.get("display_name") or row.get("name") or "Untitled"
        model_hint = row.get("model") or ""
        if is_codex:
            cache_path = _core._resolve_codex_rollout_path(sid)

            def extract(sid=sid, name=name, model_hint=model_hint):
                return _core._throughput_codex_turns_from_file(
                    sid, session_name=name, model_hint=model_hint, cutoff_epoch=None
                )
        else:
            cache_path = row.get("jsonl_path")

            def extract(sid=sid, name=name, engine=engine, model_hint=model_hint):
                parsed = _core.parse_conversation(sid)
                return _core._throughput_turns_from_events(
                    parsed.get("events") or [],
                    session_id=sid,
                    session_name=name,
                    engine=engine,
                    model_hint=model_hint,
                    cutoff_epoch=None,
                )
        try:
            full_turns = _core._throughput_file_turns(cache_path, extract) if cache_path else extract()
        except Exception:
            error_count += 1
            continue
        if full_turns is None:
            error_count += 1
            continue
        parsed_count += 1
        for turn in _core._throughput_dedupe_turns(
            [dict(item) for item in full_turns if isinstance(item, dict)]
        ):
            if not _core._throughput_turn_after_cutoff(turn, cutoff_epoch):
                continue
            tokens = int(
                (turn.get("raw_context_tokens") or turn.get("tokens_in") or 0)
                + (turn.get("tokens_out") or 0)
            )
            turns.append(
                {
                    "project_id": project["id"],
                    "project_name": project["name"],
                    "t_start": turn.get("t_start"),
                    "t_end": turn.get("t_end"),
                    "dur_sec": turn.get("dur_sec") or 0,
                    "tokens": tokens,
                    "human_trigger": turn.get("trigger_type") == "user_text",
                    "engine": engine,
                    "message_id": turn.get("message_id") or "",
                }
            )
    return turns, {
        "conversations_considered": considered,
        "transcripts_parsed": parsed_count,
        "transcript_errors": error_count,
    }


def _productivity_build_snapshot(now=None):
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_tz = _core.system_local_timezone()
    end_date = now.astimezone(local_tz).date()
    full_start = end_date - timedelta(weeks=16) + timedelta(days=1)
    cutoff = datetime.combine(full_start, datetime_time.min, tzinfo=local_tz)

    conversations = _core.find_all_conversations(
        resolve_pr_states=False,
        resolve_effective=False,
        resolve_worktree_dirty=False,
    )
    watchtower_status = {"available": True, "items": 0, "reason": None}
    try:
        raw_tickets = _core._q.list_items() or []
    except Exception:
        raw_tickets = []
        watchtower_status = {
            "available": False,
            "items": 0,
            "reason": "read_failed",
        }
    repositories, warnings = _core._productivity_known_repos(conversations)
    identities = _core.discover_git_identities(repositories)
    commits = []
    seen_commits = set()
    git_scanned = 0
    for repo in repositories:
        try:
            rows = _core.collect_git_commits(repo, cutoff, identities)
            git_scanned += 1
        except (OSError, RuntimeError, subprocess.SubprocessError):
            warnings.append(f"Git history unavailable for {repo['name']}")
            continue
        for row in rows:
            key = (row.get("project_id"), row.get("sha"))
            if key in seen_commits:
                continue
            seen_commits.add(key)
            commits.append(row)

    turns, transcript_coverage = _core._productivity_turn_rows(
        conversations, repositories, cutoff.timestamp()
    )
    queue_projects = _productivity_queue_projects(repositories)
    tickets = [
        normalized
        for normalized in (
            _core._productivity_normalize_ticket(item, queue_projects) for item in raw_tickets
        )
        if normalized
    ]
    watchtower_status["items"] = len(tickets)
    presence = _core._PRODUCTIVITY_STORE.load_presence(full_start, end_date, tzinfo=local_tz)
    sampled = _core.presence_summary(presence, tzinfo=local_tz)
    sampler = _core.presence_health()
    if sampled.get("sample_minutes"):
        presence_source = "macos_idle_sampler"
    elif sampler.get("sampler_available"):
        presence_source = "not_yet_sampled"
    else:
        presence_source = "unavailable"
    unique_warnings = sorted(set(warnings))

    datasets = {}
    for weeks in _PRODUCTIVITY_WEEKS:
        start_date = end_date - timedelta(weeks=weeks) + timedelta(days=1)
        dataset = _core.aggregate_productivity(
            commits=commits,
            turns=turns,
            tickets=tickets,
            presence=presence,
            start_date=start_date,
            end_date=end_date,
            tzinfo=local_tz,
        )
        dataset["range"]["weeks"] = weeks
        datasets[str(weeks)] = dataset

    coverage = {
        "repositories": len(repositories),
        "git_repositories_scanned": git_scanned,
        "git_identity_available": bool(identities),
        **transcript_coverage,
        "watchtower_items": len(tickets),
        "watchtower": watchtower_status,
        "presence": {
            **sampled,
            "available": bool(sampled.get("sample_minutes")),
            **sampler,
            "source": presence_source,
        },
        "warning_count": len(unique_warnings),
        "warnings": unique_warnings[:12],
        "proxies": [
            "Pushed commits are commits by a configured local Git identity that are now reachable from remote-tracking refs, bucketed by committer date.",
            "Observed work time clusters human prompts separated by no more than 30 minutes and adds a five-minute tail.",
            "Gross agent time sums turn durations; net agent time removes overlapping agent intervals.",
            "Computer presence is measured only while CCC is running and cannot be reconstructed historically.",
            "Agent association is correlation, not evidence that agents caused a productivity change.",
        ],
    }
    return {
        "schema": _core._PRODUCTIVITY_SCHEMA,
        "generated_at": time.time(),
        "datasets": datasets,
        "coverage": coverage,
    }


def _productivity_refresh_public():
    with _PRODUCTIVITY_REFRESH_LOCK:
        return dict(_core._PRODUCTIVITY_REFRESH)


def _productivity_refresh_start(*, force=False):
    with _PRODUCTIVITY_REFRESH_LOCK:
        state = _core._PRODUCTIVITY_REFRESH.get("state")
        if state == "building" or (state == "failed" and not force):
            return dict(_core._PRODUCTIVITY_REFRESH)
        _core._PRODUCTIVITY_REFRESH.update(
            {
                "state": "building",
                "started_at": time.time(),
                "completed_at": None,
                "error": None,
                "error_code": None,
            }
        )

    def run():
        try:
            snapshot = _core._productivity_build_snapshot()
            watchtower = (snapshot.get("coverage") or {}).get("watchtower") or {}
            if watchtower.get("available") is False:
                previous = _core._PRODUCTIVITY_STORE.load_payload() or {}
                previous_payload = previous.get("payload") or {}
                previous_watchtower = (
                    (previous_payload.get("coverage") or {}).get("watchtower") or {}
                )
                if (
                    previous_payload.get("schema") == _core._PRODUCTIVITY_SCHEMA
                    and previous_watchtower.get("available") is True
                ):
                    raise RuntimeError(
                        "WatchTower unavailable; preserving the last good productivity snapshot"
                    )
            generated_at = float(snapshot.get("generated_at") or time.time())
            _core._PRODUCTIVITY_STORE.save_payload(snapshot, generated_at=generated_at)
            with _PRODUCTIVITY_REFRESH_LOCK:
                _core._PRODUCTIVITY_REFRESH.update(
                    {
                        "state": "complete",
                        "completed_at": time.time(),
                        "error": None,
                        "error_code": None,
                    }
                )
        except Exception as exc:
            print(f"[productivity] refresh failed: {exc!r}", file=sys.stderr)
            with _PRODUCTIVITY_REFRESH_LOCK:
                _core._PRODUCTIVITY_REFRESH.update(
                    {
                        "state": "failed",
                        "completed_at": time.time(),
                        "error": "Productivity refresh failed. Retry manually.",
                        "error_code": "refresh_failed",
                    }
                )

    threading.Thread(
        target=run,
        daemon=True,
        name="ccc-productivity-refresh",
    ).start()
    return _core._productivity_refresh_public()


def _productivity_payload(*, weeks=8, force_refresh=False, status_only=False):
    weeks = weeks if weeks in _PRODUCTIVITY_WEEKS else 8
    cached = _core._PRODUCTIVITY_STORE.load_payload()
    valid_cache = (
        isinstance(cached, dict)
        and isinstance(cached.get("payload"), dict)
        and cached["payload"].get("schema") == _core._PRODUCTIVITY_SCHEMA
        and str(weeks) in (cached["payload"].get("datasets") or {})
    )
    stale = True
    if valid_cache:
        stale = (time.time() - float(cached.get("generated_at") or 0)) > _PRODUCTIVITY_CACHE_TTL
    refresh = _core._productivity_refresh_public()
    if force_refresh:
        refresh = _core._productivity_refresh_start(force=True)
    elif (stale or not valid_cache) and refresh.get("state") != "failed":
        refresh = _core._productivity_refresh_start()
    if not valid_cache:
        state = refresh.get("state") or "building"
        payload = {
            "ok": state != "failed",
            "state": state,
            "range": {"weeks": weeks},
            "refresh": {**refresh, "cached": False, "generated_at": None},
        }
        if state == "failed":
            payload.update(
                {
                    "error": "Productivity refresh failed. Retry manually.",
                    "error_code": "refresh_failed",
                }
            )
            return payload, 503
        return payload, 202

    snapshot = cached["payload"]
    refresh_payload = {
        **refresh,
        "cached": True,
        "stale": stale,
        "generated_at": cached.get("generated_at"),
    }
    if status_only:
        state = refresh.get("state") or "idle"
        return {
            "ok": True,
            "state": state,
            "range": {"weeks": weeks},
            "refresh": refresh_payload,
        }, (202 if state == "building" else 200)

    payload = dict(snapshot["datasets"][str(weeks)])
    payload["coverage"] = snapshot.get("coverage") or {}
    payload["refresh"] = refresh_payload
    return payload, 200


_MORNING_BRAINDUMP_PROMPT = """You are analyzing the user's morning brain-dump.

For each item in the dump, classify as exactly one of:
- NEW: a fresh task/idea not already in the user's system. This INCLUDES
  personal errands or one-off todos (e.g. "call mom", "pick up dry cleaning")
  even when they don't map to any configured goal. If the user typed it and
  it's a real action item, it's NEW — regardless of whether a goal matches.
- EXISTING: matches or refines something already tracked; identify which
- CONTEXT: not a task — a thought, update, reflection, or meeting note
- DISCARD: ONLY pure filler with no content ("ok", "hmm", "uh", "so yeah").
  Never DISCARD an actual intent just because no goal fits — use NEW with
  suggested_goal: null instead.

Also suggest which GOAL it maps to (or null if unclear). Goal slugs are shown below.

## Goals

{goals}

## Existing tactical items (sample)

{tactical}

## Braindump

```
{dump}
```

Return ONLY a JSON array. No prose. No markdown fences. Each item looks like:
{{"original_text": "...", "classification": "NEW"|"EXISTING"|"CONTEXT"|"DISCARD", "matched_existing": "short text of what it matched, or null", "suggested_goal": "slug or null", "notes": "one-sentence why"}}

Items in the dump are separated by newlines. Preserve the user's original phrasing in original_text.
"""


def morning_braindump(text):
    """Run `claude -p --model haiku` on a brain-dump with context about
    existing goals/tactical items. Returns the parsed analysis array.
    """
    import morning_store as _store
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty dump"}

    try:
        goals = _store.load_all_goals()
    except Exception:
        goals = []
    goal_lines = []
    for g in goals:
        strats = g.get("strategies") or []
        slug = g.get("slug", "?")
        name = g.get("name", slug)
        strat_ids = ", ".join(s.get("id", "?") for s in strats if s.get("status") == "active")
        goal_lines.append(f"- {slug}: {name} (active strategies: {strat_ids or 'none'})")
    goals_block = "\n".join(goal_lines) or "(no goals configured)"

    # Grab current tactical items so Claude can match against them.
    import morning as _morning
    try:
        state = _morning.get_morning_state()
        tactical_sample = state.get("tactical", [])[:30]
    except Exception:
        tactical_sample = []
    tact_lines = []
    for t in tactical_sample:
        tact_lines.append(f"- [{t.get('source','?')}] {t.get('text','')}")
    tact_block = "\n".join(tact_lines) or "(no tactical items)"

    prompt = _MORNING_BRAINDUMP_PROMPT.format(
        goals=goals_block,
        tactical=tact_block,
        dump=text,
    )

    claude_bin = _core._resolve_claude_bin()
    if not claude_bin.get("available"):
        return {
            "ok": False,
            "error": claude_bin.get("reason") or "Claude Code CLI not found",
            "code": claude_bin.get("code", "claude_unavailable"),
        }

    try:
        r = subprocess.run(
            [claude_bin["bin"], "-p", "--model", "haiku"],
            input=prompt, capture_output=True, text=True, timeout=60,
            cwd=str(_core._SCRATCH_DIR),  # keep throwaway JSONLs out of repo project dirs
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": f"claude -p failed: {e}", "code": "claude_unavailable"}
    if r.returncode != 0:
        return {"ok": False, "error": f"claude -p exited {r.returncode}: {r.stderr[:200]}"}

    out = (r.stdout or "").strip()
    out = re.sub(r"^```(?:json)?\s*|\s*```$", "", out, flags=re.M).strip()
    m = re.search(r"\[.*\]", out, flags=re.S)
    if not m:
        return {"ok": False, "error": "no JSON array in response", "raw": out[:500]}
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"JSON parse: {e}", "raw": out[:500]}

    return {"ok": True, "items": items}


def _morning_session_ids():
    """Return a dict {session_id: {"goal_slug": ..., "strategy_id": ...}}
    for every strategy across all goal.md files that has a claude_session_id.
    Used to route sessions to the Morning Kanban vs. the Dev Kanban.
    """
    if not _core.MORNING_ENABLED:
        return {}
    import morning_store as _store
    out = {}
    try:
        goals = _store.load_all_goals()
    except Exception:
        goals = []
    goal_meta_by_slug = {g["slug"]: g for g in goals}
    for g in goals:
        for s in g.get("strategies", []):
            sid = s.get("claude_session_id")
            if sid:
                out[sid] = {
                    "goal_slug": g["slug"],
                    "goal_name": g.get("name"),
                    "goal_accent": g.get("accent"),
                    "strategy_id": s.get("id"),
                    "strategy_text": s.get("text"),
                    "strategy_status": s.get("status"),
                }
    # Also claim sessions bound to Today tasks (via ▶ Start on a task card).
    # Without this, task-spawned sessions leak into the Dev Kanban because the
    # dev/morning split is driven by presence in this map.
    try:
        for ut in _store.load_user_tactical(include_dismissed=True):
            sid = ut.get("claude_session_id")
            if not sid or sid in out:
                continue
            slug = ut.get("goal_slug") or ""
            gmeta = goal_meta_by_slug.get(slug, {})
            out[sid] = {
                "goal_slug": slug,
                "goal_name": gmeta.get("name") or slug,
                "goal_accent": gmeta.get("accent") or "#5ac8fa",
                "strategy_id": None,
                "strategy_text": ut.get("text") or "",
                "strategy_status": "task",
                "user_tactical_id": ut.get("id"),
            }
    except Exception:
        pass
    return out


def _promote_task_to_strategy(task_id, launch=False):
    """Convert a user-tactical task into a new strategy on its goal.

    If the task has no goal_slug, refuses. On success, dismisses the task
    (it now lives as a strategy). If launch=True, also spawns a session for
    the new strategy and saves the session_id on the strategy entry.
    """
    import morning_store as _store
    tasks = _store.load_user_tactical(include_dismissed=True)
    task = next((t for t in tasks if t.get("id") == task_id), None)
    if task is None:
        return {"ok": False, "error": f"unknown task: {task_id}"}
    goal_slug = task.get("goal_slug")
    if not goal_slug:
        return {"ok": False, "error": "task has no goal - set one before promoting"}
    text = task.get("text") or ""
    result = _store.append_strategy(goal_slug, text, status="active")
    if not result.get("ok"):
        return result
    strategy_id = result["strategy_id"]
    _store.dismiss_user_tactical(task_id)
    if launch:
        launch_result = morning_launch(goal_slug, strategy_id)
        return {"ok": True, "action": "promoted_and_launched", "strategy_id": strategy_id, "goal_slug": goal_slug, "launch": launch_result}
    return {"ok": True, "action": "promoted", "strategy_id": strategy_id, "goal_slug": goal_slug}


def _demote_strategy_to_task(goal_slug, strategy_id, keep_session=False):
    """Convert a strategy into a user-tactical task and mark the strategy
    as dropped. If keep_session=True and the strategy has a session_id, the
    new task carries that session_id so the user can still Resume it.
    """
    import morning as _morning
    import morning_store as _store
    detail = _morning.get_goal_detail(goal_slug) or {}
    strat = next((s for s in detail.get("strategies", []) if s.get("id") == strategy_id), None)
    if strat is None:
        return {"ok": False, "error": f"unknown strategy: {goal_slug}/{strategy_id}"}
    add = _store.add_user_tactical(goal_slug, strat.get("text") or strategy_id, source_note="demoted")
    if not add.get("ok"):
        return add
    if keep_session and strat.get("claude_session_id"):
        _store.update_user_tactical(add["id"], {"claude_session_id": strat["claude_session_id"]})
    _store.set_strategy_field(goal_slug, strategy_id, "status", "dropped")
    if not keep_session and strat.get("claude_session_id"):
        # Detach the session so it's not double-tracked.
        _store.set_strategy_field(goal_slug, strategy_id, "claude_session_id", None)
    return {"ok": True, "action": "demoted", "user_tactical_id": add["id"]}


def _detach_session_from_strategy(goal_slug, strategy_id):
    """Clear the claude_session_id on a strategy (leaves session running)."""
    import morning_store as _store
    return _store.set_strategy_field(goal_slug, strategy_id, "claude_session_id", None)


def _kill_session_by_id(session_id):
    """Best-effort: find ALL pids claiming this session and SIGTERM them.

    Multiple PIDs can register the same sessionId — most often when Jump
    spawns `claude --resume <sid>` while the original headless agent is
    still alive — and we want to free the whole set, not just the first.
    Each PID is verified to still be a claude process before we signal,
    so a recycled PID can't end up taking out something unrelated.
    """
    import signal
    sessions_dir = Path.home() / ".claude" / "sessions"
    if not sessions_dir.is_dir():
        return {"ok": False, "error": "no sessions dir"}
    killed = []
    errors = []
    matched = 0
    for f in sessions_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        # Claude writes the field as `sessionId` (camelCase). Older or
        # third-party tooling may use snake_case — accept both so this
        # function actually matches in practice (it didn't before).
        if data.get("sessionId") != session_id and data.get("session_id") != session_id:
            continue
        pid = data.get("pid")
        if not pid:
            continue
        matched += 1
        if not _core._pid_is_engine_process(pid, "claude"):
            # Stale sessions/<pid>.json — process is gone or the PID got
            # recycled to something else. Nothing to signal safely.
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
            killed.append(int(pid))
        except (OSError, ProcessLookupError) as e:
            errors.append({"pid": pid, "error": str(e)})
    if matched == 0:
        return {"ok": False, "error": "no process found for session"}
    if not killed and not errors:
        return {"ok": True, "action": "noop", "note": "session already dead"}
    result = {"ok": bool(killed), "action": "killed", "pids": killed}
    if errors:
        result["errors"] = errors
    return result


def _session_repo_has_live_dev_server(cwd):
    """True when `cwd` (or a matching workspace) has a dev server running —
    either CCC-tracked (started from the localhost pill) or discovered by
    hand (`npm run dev` left running in a Bash tool). Guards the idle-session
    reaper: SIGTERM only targets the `claude` pid, not its process group, but
    the CLI's own shutdown path tears down any background tasks it's still
    tracking — including a dev server the session started — so an idle-but-
    still-serving session shouldn't be reaped out from under a live preview
    (OPS-65)."""
    if not cwd:
        return False
    try:
        target_path = Path(cwd).resolve()
    except OSError:
        return False
    if not target_path.is_dir():
        return False
    repo_key = str(target_path)
    with _core._NEXTJS_LOCK:
        entry = _core._NEXTJS_PROCS.get(repo_key)
    if entry and _core._nextjs_proc_alive(entry):
        return True
    try:
        return bool(_core._detect_nextjs(target_path) and _core._find_external_nextjs_process(target_path))
    except Exception:
        return False


def _reap_idle_sessions(now=None):
    """Sweep registered `claude` sessions and SIGTERM the ones whose JSONL
    has had no meaningful event in `_IDLE_REAPER_AGE_HOURS`.

    Activity signal is `last_meaningful_ts` from `_extract_tail_meta` — the
    timestamp of the most recent user/assistant/result event. Administrative
    writes (custom-title, agent-name, pr-link) don't bump it, so a session
    isn't kept alive just because something renamed it. If the JSONL has
    never had a meaningful event, falls back to file mtime so a never-active
    spawn can't live forever.

    Returns a list of {sid, pid, age_hours} for everything reaped. Caller
    can log; this function deliberately doesn't print so it's safe to call
    from tests.

    Skips: pkood agents, codex sessions, anything whose argv[0] basename
    isn't `claude`. Multi-PID-per-sid (Resume scenario) is handled because
    each PID gets its own `~/.claude/sessions/<pid>.json` file and is
    evaluated independently.
    """
    import signal as _signal
    sessions_dir = Path.home() / ".claude" / "sessions"
    if not sessions_dir.is_dir():
        return []
    if now is None:
        now = time.time()
    cutoff = now - _core._IDLE_REAPER_AGE_HOURS * 3600
    # A WatchTower queue worker is itself a `claude` process, but WT owns its
    # lifecycle (watchtower.workers release/reap) — never SIGTERM one here.
    wt_pids, wt_sids = _core._wt_live_worker_guard()
    reaped = []
    # Lazy batched process snapshot for the mid-turn guard below — built at
    # most once per sweep, and only when a candidate actually crossed the
    # idle cutoff (CLAUDE.md "Performance gates": no per-row subprocess).
    ps_table = None
    for f in sessions_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        sid = data.get("sessionId") or data.get("session_id")
        pid = data.get("pid")
        if not sid or not pid:
            continue
        try:
            if str(sid) in wt_sids or int(pid) in wt_pids:
                continue
        except (TypeError, ValueError):
            pass
        if not _core._pid_is_engine_process(pid, "claude"):
            continue
        jsonl = _core._find_session_jsonl(sid)
        if not jsonl:
            # No JSONL on disk → nothing to measure activity against. Use
            # the sessions/<pid>.json mtime as a last-resort age signal so
            # an orphaned registration doesn't strand a process forever.
            try:
                last_active = f.stat().st_mtime
            except OSError:
                continue
        else:
            meta = _core._extract_tail_meta(jsonl)
            last_active = meta.get("last_meaningful_ts") or 0
            if not last_active:
                try:
                    last_active = jsonl.stat().st_mtime
                except OSError:
                    continue
        if last_active >= cutoff:
            continue
        if _session_repo_has_live_dev_server(data.get("cwd")):
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        # Mid-turn guard: a direct tool child means the session only LOOKS
        # idle — a long-running tool doesn't write transcript events. CCC
        # never kills a possibly-mid-turn session on its own; file an
        # approval ask for the dashboard instead. One batched ps snapshot
        # per sweep, built lazily only when a candidate crossed the cutoff.
        if ps_table is None:
            ps_table = _core._spawn_reaper_process_table()
        if _spawn_table_has_active_tool_child(ps_table, pid_int):
            try:
                idle_h = round((now - last_active) / 3600, 1)
                _core._file_interrupt_ask(
                    sid, "idle-reaper",
                    f"Session idle {idle_h}h but a tool subprocess is still "
                    "running. Approve to SIGTERM it, dismiss to leave it alone.",
                    {"kind": "sigterm", "pid": pid_int},
                )
            except Exception:
                pass
            continue
        try:
            os.kill(pid_int, _signal.SIGTERM)
            idle_hours = round((now - last_active) / 3600, 1)
            cwd = data.get("cwd") or ""
            last_seen = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_active))
            # Attribution (premature-death hunt): stamp WHO killed this headless
            # so a later `exit` row (SIGTERM) can be matched to a CCC-side kill.
            # An exit=SIGTERM with no matching kill/retire row = provably external
            # (a closed terminal, an outside cleanup tool), not CCC. Concrete
            # evidence (last-active timestamp, threshold, cwd) so the kill is
            # auditable, not just a bare idle_hours number (CCC-743).
            _core._resume_ledger_append(
                "kill", sid=sid, pid=int(pid), source="idle_reaper",
                idle_hours=idle_hours, ttl_hours=_core._IDLE_REAPER_AGE_HOURS,
                last_active_at=last_seen, cwd=cwd,
            )
            _core._log_activity(
                "kill", "KILL",
                f"pid={int(pid)} sid={sid} source=idle_reaper "
                f"idle_hours={idle_hours} ttl_hours={_core._IDLE_REAPER_AGE_HOURS} "
                f"last_active={last_seen} cwd={cwd or '-'}",
            )
            reaped.append({
                "sid": sid,
                "pid": int(pid),
                "age_hours": idle_hours,
            })
        except (OSError, ProcessLookupError):
            continue
    return reaped


def _spawn_idle_ttl_hours():
    """TTL (hours) before an idle CCC-spawned headless is retired.

    Read from CCC_SPAWN_IDLE_TTL_HOURS at sweep time so the knob works
    without a restart in tests; <= 0 disables the sweep entirely."""
    raw = (os.environ.get("CCC_SPAWN_IDLE_TTL_HOURS") or "").strip()
    if not raw:
        return _core._SPAWN_IDLE_TTL_HOURS_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return _core._SPAWN_IDLE_TTL_HOURS_DEFAULT


def _spawn_reaper_process_table():
    """One batched `ps` sweep -> {pid: {ppid, pgid, stat, command}}.

    The spawn reaper's only process probe. Everything the sweep needs —
    liveness, zombie state, engine identity (argv[0]), and the mid-turn
    tool-child guard — comes from this single snapshot, never a per-row
    subprocess (see CLAUDE.md "Performance gates")."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,stat=,command="],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}
    if out.returncode != 0:
        return {}
    table = {}
    for raw in (out.stdout or "").splitlines():
        parts = raw.strip().split(None, 4)
        if len(parts) < 4:
            continue
        try:
            pid, ppid, pgid = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        table[pid] = {
            "ppid": ppid,
            "pgid": pgid,
            "stat": parts[3],
            "command": parts[4] if len(parts) > 4 else "",
        }
    return table


def _spawn_table_row_is_claude(row):
    """PID-reuse defence from a batched ps row: argv[0] must be Claude."""
    cmd = (row or {}).get("command") or ""
    parts = cmd.split()
    if not parts:
        return False
    return _core._process_comm_is_claude(parts[0])


def _spawn_table_has_active_tool_child(table, parent_pid):
    """Mid-turn guard from the batched snapshot: a direct child in its own
    process group (Bash/tool subprocess) means the worker is still busy.
    Mirrors `_spawn_entry_active_tool_child` without the extra ps fork."""
    for row in table.values():
        if row.get("ppid") != parent_pid or row.get("pgid") == parent_pid:
            continue
        if _core._is_ccc_hook_command(row.get("command") or ""):
            continue
        return True
    return False


def _spawn_entry_spawned_at_epoch(value):
    """Parse the registry's `spawned_at` stamp (20260717T080151) to epoch."""
    try:
        return time.mktime(time.strptime(str(value or ""), "%Y%m%dT%H%M%S"))
    except (ValueError, OverflowError):
        return None


def _reap_idle_spawned_headless(now=None):
    """Sweep the spawn registry and retire idle persistent headless workers.

    The FIFO-stdin design (see _make_stdin_fifo) means a finished worker
    never exits on its own — its own fd 0 keeps the FIFO writer count alive,
    so EOF never arrives. This sweep is the lifecycle backstop: any
    CCC-spawned Claude headless with no activity for `CCC_SPAWN_IDLE_TTL_HOURS`
    (spawn log, FIFO, transcript all quiet) and no active tool child gets a
    graceful SIGTERM to its process group. The registry entry is kept and
    marked `retired` (not deleted) — the transcript is intact, so the session
    remains resumable; the boot reattach sweep prunes the entry once dead.

    Guards, in order:
      - engine must be "claude" (Codex/Gemini/Cursor headless runs are
        one-shot and exit on their own; remote spawns are not local pids);
      - pid must be alive, non-zombie, and still argv[0]-verified Claude
        (PID reuse) — all from ONE batched ps snapshot;
      - never a live WatchTower worker (_wt_live_worker_guard — WT owns its
        workers' lifecycle);
      - never mid-turn: recent spawn-log/FIFO mtime counts as activity, and
        a live tool child (own pgid) defers the retire to a later sweep;
      - the session transcript (any writer: this worker, a takeover terminal,
        another resume) must also be idle past the TTL.

    Returns a list of {pid, sid, name, idle_hours} for everything retired.
    Deliberately print-free so tests can call it directly."""
    ttl_hours = _core._spawn_idle_ttl_hours()
    if ttl_hours <= 0:
        return []
    if now is None:
        now = time.time()
    cutoff = now - ttl_hours * 3600
    entries = _core._load_spawn_registry()
    candidates = [
        e for e in entries
        if isinstance(e, dict)
        and (e.get("engine") or "claude") == "claude"
        and not e.get("retired")
    ]
    if not candidates:
        return []
    table = _core._spawn_reaper_process_table()
    if not table:
        return []
    wt_pids, wt_sids = _core._wt_live_worker_guard()
    reaped = []
    changed = False
    for entry in candidates:
        try:
            pid = int(entry.get("pid"))
        except (TypeError, ValueError):
            continue
        row = table.get(pid)
        if row is None or row.get("stat", "").upper().startswith("Z"):
            continue
        if not _core._spawn_table_row_is_claude(row):
            continue
        sid = entry.get("session_id") or None
        if pid in wt_pids or (sid and str(sid) in wt_sids):
            continue
        # Cheap activity signals first: spawn log + FIFO mtimes (every turn
        # streams to the log; every inject writes the FIFO), fall back to
        # the spawn stamp so a log-less entry can't dodge the TTL forever.
        # Kept as (source, epoch) pairs, not bare floats, so the eventual
        # kill log line can name which signal was stalest instead of just
        # a bare number (CCC-743).
        stamps = []
        for key in ("log", "fifo"):
            path = entry.get(key)
            if path:
                try:
                    stamps.append((key, os.stat(path).st_mtime))
                except OSError:
                    pass
        spawned_epoch = _core._spawn_entry_spawned_at_epoch(entry.get("spawned_at"))
        if spawned_epoch:
            stamps.append(("spawned_at", spawned_epoch))
        if not stamps or max(s[1] for s in stamps) >= cutoff:
            continue
        if _spawn_table_has_active_tool_child(table, pid):
            continue
        # Only for otherwise-condemned candidates: the (pricier) transcript
        # lookup. Any writer bumping the JSONL — this worker, a takeover
        # terminal, another resume — counts as recent activity.
        if sid:
            jsonl = _core._find_session_jsonl(sid)
            if jsonl:
                try:
                    jsonl_mtime = jsonl.stat().st_mtime
                    if jsonl_mtime >= cutoff:
                        continue
                    stamps.append(("transcript", jsonl_mtime))
                except OSError:
                    pass
        last_source, last_epoch = max(stamps, key=lambda s: s[1])
        idle_hours = round((now - last_epoch) / 3600, 1)
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except (PermissionError, OSError, ValueError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError, ValueError):
                continue
        entry["retired"] = True
        entry["retired_at"] = time.strftime("%Y%m%dT%H%M%S")
        entry["retire_reason"] = "idle-ttl"
        changed = True
        # Close our side of the FIFO and drop the node; the in-memory entry
        # (if any) keeps rendering as a finished spawn via its normal poll.
        cleaned = False
        for s in _core._spawned_sessions:
            if isinstance(s, dict) and s.get("pid") == pid:
                _core._cleanup_finished_entry(s)
                cleaned = True
        if not cleaned:
            _core._unlink_quiet(entry.get("fifo"))
        name = entry.get("name") or ""
        cwd = entry.get("cwd") or ""
        last_seen = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_epoch))
        # Attribution for the premature-death ledger: this exit was CCC's
        # idle-TTL policy, not an external kill. Concrete evidence (which
        # signal was stalest, its actual timestamp, the TTL threshold that
        # fired) so a killed session isn't just "source=spawn_idle_ttl" with
        # no way to audit the call (CCC-743).
        _core._resume_ledger_append(
            "kill", sid=sid, pid=pid, source="spawn_idle_ttl",
            idle_hours=idle_hours, ttl_hours=ttl_hours,
            last_activity_source=last_source, last_activity_at=last_seen,
            name=name, cwd=cwd,
        )
        _core._log_activity(
            "kill", "KILL",
            f"pid={pid} sid={sid} name={name or '-'} source=spawn_idle_ttl "
            f"idle_hours={idle_hours} ttl_hours={ttl_hours} "
            f"last_activity={last_source}@{last_seen} cwd={cwd or '-'}",
        )
        reaped.append({
            "pid": pid,
            "sid": sid,
            "name": entry.get("name") or "",
            "idle_hours": idle_hours,
        })
    if changed:
        retired_by_pid = {
            str(entry.get("pid") or ""): {
                "retired": entry.get("retired"),
                "retired_at": entry.get("retired_at"),
                "retire_reason": entry.get("retire_reason"),
            }
            for entry in entries if entry.get("retire_reason") == "idle-ttl"
        }

        def _persist_retired(current_entries):
            updated = False
            for current in current_entries:
                values = retired_by_pid.get(str(current.get("pid") or ""))
                if values and any(current.get(k) != v for k, v in values.items()):
                    current.update(values)
                    updated = True
            return updated

        _core._mutate_spawn_registry(_persist_retired)
    return reaped


def _idle_reaper_loop():
    """Background-thread driver for `_reap_idle_sessions` and the spawn
    idle-TTL sweep. Sleeps first so server start isn't followed by an
    immediate kill spree before the user has had a chance to look at the
    dashboard."""
    while True:
        try:
            time.sleep(_core._IDLE_REAPER_INTERVAL_S)
            reaped = _reap_idle_sessions()
            for r in reaped:
                print(f"[idle-reaper] SIGTERM pid={r['pid']} sid={r['sid'][:8]} idle={r['age_hours']}h")
        except Exception as e:
            print(f"[idle-reaper] sweep failed: {e}")
        try:
            for r in _core._reap_idle_spawned_headless():
                print(f"[idle-reaper] spawn-ttl SIGTERM pid={r['pid']} "
                      f"sid={(r['sid'] or '')[:8]} idle={r['idle_hours']}h")
        except Exception as e:
            print(f"[idle-reaper] spawn-ttl sweep failed: {e}")


def morning_move(payload):
    """Unified dispatcher for all kanban drag-drop transitions.

    Expected payload: {source_col, target_col, card_id, goal_slug?,
    strategy_id?, session_id?, user_tactical_id?, insert_before_id?}.
    Each pair maps to a specific operation; unsupported pairs return a
    no-op result so the UI can toast an appropriate message.
    """
    import morning_store as _store
    src = (payload.get("source_col") or "").strip()
    tgt = (payload.get("target_col") or "").strip()
    goal_slug = payload.get("goal_slug") or ""
    strategy_id = payload.get("strategy_id") or ""
    session_id = payload.get("session_id") or ""
    utid = payload.get("user_tactical_id") or payload.get("card_id") or ""

    # Identical column: only Today supports reorder. Everything else is a
    # render-only move (the user's drop position doesn't change derived
    # columns like Active/Dormant), so we no-op.
    if src == tgt:
        return {"ok": True, "action": "noop-same-col"}

    # Today → Completed : dismiss
    if src == "today" and tgt == "completed":
        return _store.dismiss_user_tactical(utid)
    # Completed → Today : undismiss
    if src == "completed" and tgt == "today":
        return _store.undismiss_user_tactical(utid)
    # Today → Backlog/Active/Dormant : promote task to strategy (+launch for active/dormant)
    if src == "today" and tgt in ("backlog", "active", "dormant"):
        return _promote_task_to_strategy(utid, launch=(tgt in ("active", "dormant")))
    # Completed → Backlog/Active/Dormant : undismiss + promote (+launch for active/dormant)
    if src == "completed" and tgt in ("backlog", "active", "dormant"):
        _store.undismiss_user_tactical(utid)
        return _promote_task_to_strategy(utid, launch=(tgt in ("active", "dormant")))

    # Backlog → Active/Dormant : spawn session on strategy
    if src == "backlog" and tgt in ("active", "dormant"):
        if not goal_slug or not strategy_id:
            return {"ok": False, "error": "missing goal_slug/strategy_id"}
        return morning_launch(goal_slug, strategy_id)
    # Backlog → Completed : mark strategy dropped
    if src == "backlog" and tgt == "completed":
        if not goal_slug or not strategy_id:
            return {"ok": False, "error": "missing goal_slug/strategy_id"}
        return _store.set_strategy_field(goal_slug, strategy_id, "status", "dropped")
    # Backlog → Today : demote strategy to task
    if src == "backlog" and tgt == "today":
        if not goal_slug or not strategy_id:
            return {"ok": False, "error": "missing goal_slug/strategy_id"}
        return _demote_strategy_to_task(goal_slug, strategy_id)

    # Dormant → Active : resume session
    if src == "dormant" and tgt == "active":
        if not goal_slug or not strategy_id:
            return {"ok": False, "error": "missing goal_slug/strategy_id"}
        return morning_launch(goal_slug, strategy_id)
    # Active/Dormant → Backlog : detach session
    if src in ("active", "dormant") and tgt == "backlog":
        if not goal_slug or not strategy_id:
            return {"ok": False, "error": "missing goal_slug/strategy_id"}
        return _detach_session_from_strategy(goal_slug, strategy_id)
    # Active/Dormant → Today : demote session to task (keep session_id on task)
    if src in ("active", "dormant") and tgt == "today":
        if not goal_slug or not strategy_id:
            return {"ok": False, "error": "missing goal_slug/strategy_id"}
        return _demote_strategy_to_task(goal_slug, strategy_id, keep_session=True)
    # Active/Dormant → Completed : mark done (keep session for audit)
    if src in ("active", "dormant") and tgt == "completed":
        if not goal_slug or not strategy_id:
            return {"ok": False, "error": "missing goal_slug/strategy_id"}
        return _store.set_strategy_field(goal_slug, strategy_id, "status", "done")
    # Active → Dormant : kill process (session_id persists)
    if src == "active" and tgt == "dormant":
        if not session_id:
            return {"ok": False, "error": "missing session_id"}
        return _core._kill_session_by_id(session_id)

    return {"ok": False, "error": f"unsupported move: {src} -> {tgt}"}


def morning_launch_task(task_id, custom_message=None):
    """Spawn or resume a Claude session bound to a Today task.

    The task's claude_session_id, once resolved, is persisted back on the
    user-tactical record via an update entry so subsequent clicks resume
    instead of re-spawning.
    """
    import morning as _morning
    import morning_store as _store

    items = _store.load_user_tactical(include_dismissed=True)
    task = next((t for t in items if t.get("id") == task_id), None)
    if task is None:
        return {"ok": False, "error": f"unknown task: {task_id}"}
    goal_slug = task.get("goal_slug") or ""
    detail = _morning.get_goal_detail(goal_slug) or {}
    goal_name = detail.get("name") or goal_slug or "(no goal)"
    intent = detail.get("intent_markdown") or ""
    task_text = task.get("text") or ""
    status = task.get("status") or ""
    session_id = task.get("claude_session_id")

    if session_id:
        message = (custom_message or "").strip() or (
            f"Jumping back into the task: \"{task_text}\". "
            f"What's the current state, and what's the next move?"
        )
        try:
            result = _core.resume_session_headless(session_id, message)
        except Exception as e:
            return {"ok": False, "error": f"resume failed: {e}"}
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or "resume failed", "action": "resume"}
        return {"ok": True, "action": "resumed", "session_id": session_id, "pid": result.get("pid")}

    name = f"task--{(goal_slug or 'no-goal')}--{task_id[:8]}"
    try:
        spawn = _core.spawn_session(
            _core._morning_task_spawn_prompt(goal_name, intent, task_text, status),
            name=name,
        )
    except Exception as e:
        return {"ok": False, "error": f"spawn failed: {e}"}
    if not spawn.get("ok"):
        return {"ok": False, "error": spawn.get("error") or "spawn failed", "action": "spawn"}

    resolved_sid = None
    log_path = spawn.get("log")
    if log_path:
        resolved_sid = _core._morning_resolve_session_id_from_log(log_path)
    if resolved_sid:
        try:
            _store.update_user_tactical(task_id, {"claude_session_id": resolved_sid})
        except Exception:
            pass
    return {
        "ok": True,
        "action": "spawned",
        "session_id": resolved_sid,
        "pid": spawn.get("pid"),
        "log": log_path,
    }


def morning_launch(goal_slug, strategy_id, custom_message=None):
    """Spawn a new Claude session for the strategy, or resume/inject if one
    already exists. Returns a dict describing the action taken.

    When `custom_message` is provided, a resume/inject uses it verbatim
    instead of the default "Still working on..." framing. Ignored for
    fresh spawns (those always get the full goal brief).
    """
    # Lazy import to avoid a cycle at module import time.
    import morning as _morning
    import morning_store as _store

    detail = _morning.get_goal_detail(goal_slug)
    if detail is None:
        return {"ok": False, "error": f"unknown goal: {goal_slug}"}
    strategy = next(
        (s for s in detail.get("strategies", []) if s.get("id") == strategy_id),
        None,
    )
    if strategy is None:
        return {"ok": False, "error": f"unknown strategy: {strategy_id}"}
    if strategy.get("status") == "dropped":
        return {"ok": False, "error": "strategy is dropped"}

    goal_name = detail.get("name") or goal_slug
    intent = detail.get("intent_markdown") or ""
    strategy_text = strategy.get("text") or strategy_id
    session_id = strategy.get("claude_session_id")

    if session_id:
        # Resume into the existing session and inject a message.
        message = (custom_message or "").strip() or _core._morning_resume_framing(goal_name, strategy_text)
        try:
            result = _core.resume_session_headless(session_id, message)
        except Exception as e:  # pragma: no cover — best-effort
            return {"ok": False, "error": f"resume failed: {e}"}
        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error") or "resume_session_headless failed",
                "action": "resume",
            }
        return {
            "ok": True,
            "action": "resumed",
            "session_id": session_id,
            "pid": result.get("pid"),
        }

    # Fresh spawn.
    name = f"{goal_slug}--{strategy_id}"
    try:
        spawn = _core.spawn_session(
            _core._morning_spawn_prompt(goal_name, intent, strategy_text),
            name=name,
        )
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"spawn failed: {e}"}

    if not spawn.get("ok"):
        return {
            "ok": False,
            "error": spawn.get("error") or "spawn_session failed",
            "action": "spawn",
        }

    # Try to resolve the session_id from the spawn log so we can persist it.
    resolved_sid = None
    log_path = spawn.get("log")
    if log_path:
        resolved_sid = _core._morning_resolve_session_id_from_log(log_path)

    saved = False
    if resolved_sid:
        try:
            saved = _store.save_strategy_session_id(goal_slug, strategy_id, resolved_sid)
        except Exception:
            saved = False

    return {
        "ok": True,
        "action": "spawned",
        "pid": spawn.get("pid"),
        "name": name,
        "session_id": resolved_sid,
        "session_id_saved": saved,
    }

