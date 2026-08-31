"""Extracted from server.py (originally lines 29760-30570).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from pathlib import Path
import re
import time

from ccc_server import core as _core

def find_codex_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    rows = _core._codex_fetch_threads(limit=limit)
    if not rows:
        return []
    if repo_only:
        repo_path = _core.resolve_repo_path(repo_path)
        repo_path_obj = Path(repo_path)
    try:
        repo_pins = _core._load_repo_pins()
    except Exception:
        repo_pins = {}
    try:
        name_overrides = _core._load_session_name_overrides()
    except Exception:
        name_overrides = {}
    try:
        archived_set, trashed_set = _core._load_conversation_lifecycle_sets()
    except Exception:
        archived_set, trashed_set = set(), set()
    try:
        verified_set = set(_core._load_verified_conversations())
    except Exception:
        verified_set = set()
    try:
        last_interactions = _core._load_last_interactions()
    except Exception:
        last_interactions = {}

    cutoff = _core._session_scan_cutoff_ts(include_old)
    max_rows = _core._session_scan_file_limit(include_old)
    spawn_by_sid = _core._codex_spawn_pid_by_thread_id()
    # One read of the spawn-edge table for the whole scan; per-row lookups are
    # O(1) dict hits (perf gate: no per-row DB work). Lets a spawned agent nest
    # under its parent in the Current-sessions tree. (CCC-298)
    codex_parent_by_child = _core._codex_spawn_parent_by_child()
    # Durable CCC-spawn parent links (survive spawn-registry pruning). One
    # read per scan; O(1) dict hits per row. (CCC-465)
    codex_durable_parents = _core._load_codex_parent_links()
    # One batched, cached read of the codex goals sqlite for the whole scan —
    # per-row lookups are O(1) dict hits (perf gate: no per-row DB work).
    goals_by_sid = _core._codex_goals_snapshot()
    git_top_cache = {}
    out = []
    scanned = 0
    for row in rows:
        sid = row.get("id") or ""
        if not sid:
            continue
        cwd = row.get("cwd") or ""
        pinned = repo_pins.get(sid)
        pinned_repo = False
        if repo_only:
            if pinned and pinned != repo_path:
                continue
            if pinned == repo_path:
                pinned_repo = True
            elif not _core._codex_cwd_matches_repo(cwd, repo_path_obj, git_top_cache):
                continue
        path = _core._codex_rollout_path_from_row(row)
        if not path or not path.is_file():
            continue
        scanned += 1
        try:
            st = path.stat()
        except OSError:
            continue
        tail = _core._extract_codex_tail_meta(path) or {}
        cwd = tail.get("cwd") or cwd
        modified = (
            tail.get("last_meaningful_ts")
            or _core._codex_ts_seconds(row, "updated")
            or st.st_mtime
        )
        freshness = max(modified, last_interactions.get(sid) or 0)
        if (
            not include_old
            and not (spawn_by_sid.get(sid) or {}).get("alive")
            and cutoff > 0
            and freshness < cutoff
        ):
            continue
        if not include_old and max_rows > 0 and len(out) >= max_rows:
            continue
        row_first_message = _core._strip_ccc_session_state_instruction(
            _core._strip_host_system_instruction(row.get("first_user_message"))
        ).strip()
        tail_first_message = _core._strip_ccc_session_state_instruction(
            _core._strip_host_system_instruction(tail.get("first_message"))
        ).strip()
        first_message = row_first_message or tail_first_message
        title = _core._strip_ccc_session_state_instruction(
            _core._strip_host_system_instruction(row.get("title")).strip()
        ).strip()
        # Codex sometimes stores the verbatim first user message as the
        # "title" when the prompt is too short to summarize. Only treat the
        # title as AI-generated when it actually differs from first_message
        # — otherwise the ✨ glyph would show on rows where Codex did no
        # summarization.
        # The row is about to be emitted into the session list -- for a
        # just-spawned session this is the moment the sidebar can stop saying
        # "spawning", so it is the mark that matches what the user sees.
        # Persist only on the transition. This runs per Codex row on a hot
        # list path, so writing the file every pass would be a per-row fsync;
        # mark() returns True exactly once, when the mark is first recorded.
        if _core._spawn_timeline_mark(sid, "row_in_session_list"):
            _core._spawn_timeline_save()
        codex_ai_title = title if (title and title != first_message) else None
        # Codex's SQLite `title` is the raw first user message when the
        # prompt was too short to summarize — for annotation prompts that
        # can be many kilobytes, so clamp before it becomes the row title.
        # Codex assigns spawned agents a short random codename (agent_nickname,
        # e.g. "Euclid") that tells the user nothing about the work. Prefer a
        # meaningful name: Codex's own (often AI-summarized) title, then the
        # original ask (first user message) — the same fallback unnamed Claude
        # sessions use — and only fall back to the codename when there's nothing
        # else to show. (CCC-296)
        display_name = _core._codex_display_name(
            row,
            override=name_overrides.get(sid),
            title=title,
            first_message=first_message,
        )
        agent_task_name = _core._codex_agent_task_label(row)
        # Keep the sidebar's defensive title cap, but retain Codex's complete
        # generated title for the roomier status rail.  When Codex merely
        # copied the opening prompt into `title`, use the compact row title so
        # an annotation-sized prompt cannot fill the rail.
        status_rail_title = title if title and title != first_message else display_name
        branch = row.get("git_branch") or ""
        tail_branch = tail.get("tail_branch") or ""
        tail_worktree_path = tail.get("tail_worktree_path") or ""
        # Prefer the FIRST cwd candidate that still exists on disk. Without
        # this, a tail-extracted worktree path that has since been deleted
        # (deleted worktree, moved repo) would land in `session_cwd` and
        # Launch would try `cd '/.../no-such-worktree' && codex resume …`
        # — which fails and leaves the user in their home dir.
        effective_cwd = _core._first_existing_dir(
            tail_worktree_path, cwd, pinned
        ) or tail_worktree_path or cwd
        try:
            cwd_exists = bool(effective_cwd and Path(effective_cwd).is_dir())
        except OSError:
            cwd_exists = False
        folder_path = pinned or cwd or effective_cwd or ""
        if folder_path:
            _git_root = _core._find_git_root(folder_path)
            folder_label = _core._resolve_dir_case(_git_root or folder_path)
        else:
            folder_label = "Codex"
        _wt_worktree_label = None
        _wt_idx = folder_label.find("-wt-")
        if _wt_idx > 0:
            _wt_worktree_label = folder_label[_wt_idx + 4:]
            folder_label = folder_label[:_wt_idx]
        spawn_info = spawn_by_sid.get(sid) or {}
        spawn_pid = spawn_info.get("pid")
        spawn_alive = bool(spawn_info.get("alive"))
        codex_activity = _core._codex_activity_fields_from_tail(tail, spawn_alive)
        app_activity = _core._codex_app_server_activity_fields(sid)
        if app_activity.get("sidecar_status") and not _core._codex_app_activity_superseded_by_tail(app_activity, tail):
            codex_activity.update(app_activity)
        codex_stale_tool = _core._codex_stale_tool_fields(tail)
        needs_approval = bool(tail.get("needs_approval") or codex_activity.get("needs_approval"))
        needs_approval_message = (
            tail.get("needs_approval_message")
            or codex_activity.get("needs_approval_message")
            or ""
        )
        _goal = goals_by_sid.get(sid) or {}
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "codex",
            "engine": "codex",
            "thread_source": row.get("thread_source") or "",
            "timestamp": "",
            "branch": branch,
            "git_branch": branch,
            "first_message": first_message[:200],
            "display_name": display_name,
            "agent_task_name": agent_task_name,
            "status_rail_title": status_rail_title,
            "ai_title": codex_ai_title,
            "name_overridden": bool(name_overrides.get(sid)),
            "last_prompt": (tail.get("last_prompt") or "")[:200],
            "size": st.st_size,
            "modified": modified,
            "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)),
            "mtime": modified,
            "jsonl_path": str(path),
            "folder_label": folder_label,
            "folder_path": folder_path,
            "worktree_label": _wt_worktree_label,
            "session_cwd": effective_cwd,
            "session_cwd_exists": cwd_exists,
            "session_cwd_is_worktree": bool(
                tail_worktree_path or (effective_cwd and (Path(effective_cwd) / ".git").is_file())
            ),
            "worktree_dirty": (
                _core._worktree_dirty_cached(effective_cwd, modified)
                if resolve_worktree_dirty and effective_cwd else False
            ),
            "effective_branch": tail_branch or None,
            "effective_kind": "worktree" if tail_worktree_path else None,
            "has_edit": tail.get("has_edit", False),
            "has_commit": tail.get("has_commit", False),
            "has_push": tail.get("has_push", False),
            "last_edit_pos": tail.get("last_edit_pos", 0),
            "last_commit_pos": tail.get("last_commit_pos", 0),
            "last_push_pos": tail.get("last_push_pos", 0),
            "last_event_type": tail.get("last_event_type"),
            "pending_tool": tail.get("pending_tool"),
            "pending_file": tail.get("pending_file"),
            "pending_tool_ts": tail.get("pending_tool_ts") or 0,
            "last_assistant_text": tail.get("last_assistant_text"),
            "tail_issue_number": None,
            "tail_pr_number": tail.get("tail_pr_number"),
            "tail_pr_url": tail.get("tail_pr_url"),
            "pr_state": None,
            "session_state": _core._parse_session_state(tail.get("last_assistant_text")),
            "archived": sid in archived_set or bool(row.get("archived")),
            "trashed": sid in trashed_set or bool(row.get("trashed")),
            "verified": sid in verified_set,
            "pinned_repo": pinned_repo,
            "last_interacted": last_interactions.get(sid),
            "is_live": spawn_alive,
            "spawn_pid": spawn_pid,
            # Prefer the Codex spawn-edge parent so a spawned sub-agent nests
            # under the thread that spawned it in the Current-sessions tree;
            # fall back to the durable CCC-spawn parent link (survives
            # registry pruning), then the live spawn-registry parent. (CCC-298, CCC-465)
            "parent_session_id": (
                codex_parent_by_child.get(sid)
                or codex_durable_parents.get(sid)
                or spawn_info.get("parent_session_id")
                or ""
            ),
            **codex_activity,
            **codex_stale_tool,
            "needs_approval": needs_approval,
            "needs_approval_message": needs_approval_message,
            "model": row.get("model") or tail.get("model") or "",
            "reasoning_effort": row.get("reasoning_effort") or "",
            "latest_input_tokens": tail.get("latest_input_tokens") or 0,
            "lifetime_tokens": tail.get("lifetime_tokens") or 0,
            "context_limit": tail.get("context_limit") or 0,
            **_core._token_optimizer_quality_for_session(sid),
            "goal": _goal.get("objective") or "",
            "goal_status": _goal.get("status") or "",
        })
    if resolve_pr_states:
        _core._prime_pr_states(c.get("tail_pr_url") for c in out)
        for c in out:
            if c.get("tail_pr_url"):
                c["pr_state"] = _core._get_pr_state(c["tail_pr_url"])
    out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
    if progress:
        progress(
            "codex",
            state="done",
            count=len(out),
            total=scanned,
            detail=f"{len(out)} Codex thread card(s) ready.",
        )
    return out


def _codex_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _codex_token_usage_from_event(ev):
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    if payload.get("type") != "token_count":
        return None
    info = payload.get("info") or {}
    if not isinstance(info, dict):
        return None
    usage = info.get("last_token_usage") or info.get("total_token_usage") or {}
    if not isinstance(usage, dict) or not usage:
        return None
    return {
        "input_tokens": _codex_int(usage.get("input_tokens")),
        "cached_input_tokens": _codex_int(usage.get("cached_input_tokens")),
        "output_tokens": _codex_int(usage.get("output_tokens")),
        "reasoning_output_tokens": _codex_int(usage.get("reasoning_output_tokens")),
        "total_tokens": _codex_int(usage.get("total_tokens")),
    }


def _attach_codex_token_usage(events, usage):
    """Apply a trailing Codex token_count to its preceding assistant row.

    Codex writes ``token_count`` after ``agent_message``. The existing parser
    retained that data for ``task_complete``, which leaves the visible message
    row without the shared token chip.
    """
    if not isinstance(usage, dict):
        return False
    # A compaction turn's token_count is the ONLY report of the rebuilt context
    # size, and it lands right after the `compacted` record. Hand it to the
    # boundary row rather than to an assistant message from the turn before.
    tail = events[-1] if events else None
    if (
        isinstance(tail, dict)
        and tail.get("type") == "system"
        and tail.get("subtype") == "compact_boundary"
    ):
        compact = tail.get("compact")
        if isinstance(compact, dict) and not compact.get("post_tokens"):
            post = _codex_compact_post_tokens_from_usage(usage)
            if post:
                compact["post_tokens"] = post
                return True
    for event in reversed(events):
        if event.get("type") != "assistant":
            continue
        tokens_in = _codex_int(usage.get("input_tokens"))
        tokens_cached = _codex_int(usage.get("cached_input_tokens"))
        tokens_out = _codex_int(usage.get("output_tokens"))
        tokens_thinking = _codex_int(usage.get("reasoning_output_tokens"))
        if not (tokens_in or tokens_cached or tokens_out or tokens_thinking):
            return False
        event["tokens_in"] = tokens_in
        event["tokens_cached"] = tokens_cached
        event["tokens_out"] = tokens_out + tokens_thinking
        if tokens_thinking:
            event["tokens_thinking"] = tokens_thinking
        event["token_usage"] = dict(usage)
        return True
    return False


def _codex_turn_meta_from_event(ev):
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    if ev.get("type") != "turn_context" or not isinstance(payload, dict):
        return None
    collaboration = payload.get("collaboration_mode")
    if not isinstance(collaboration, dict):
        collaboration = {}
    settings = collaboration.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}
    model = payload.get("model") or settings.get("model") or ""
    effort = (
        payload.get("effort")
        or payload.get("reasoning_effort")
        or settings.get("reasoning_effort")
        or ""
    )
    out = {}
    if model:
        out["model"] = str(model)
    if effort:
        out["reasoning_effort"] = str(effort)
    return out or None


def _apply_codex_turn_meta(parsed, codex_turn_meta):
    if not parsed or not isinstance(codex_turn_meta, dict):
        return parsed
    if codex_turn_meta.get("model") and not parsed.get("model"):
        parsed["model"] = codex_turn_meta.get("model")
    if codex_turn_meta.get("reasoning_effort") and not parsed.get("reasoning_effort"):
        parsed["reasoning_effort"] = codex_turn_meta.get("reasoning_effort")
    return parsed


_CODEX_IN_APP_BROWSER_CONTEXT_RE = re.compile(
    r"^\s*<in-app-browser-context(?P<attrs>\s+[^>]*)?>(?P<body>.*?)</in-app-browser-context>\s*",
    re.IGNORECASE | re.DOTALL,
)
_CODEX_IN_APP_BROWSER_CONTEXT_SOURCE_RE = re.compile(
    r'''\bsource\s*=\s*["'](?P<source>[^"']+)["']''', re.IGNORECASE,
)


def _extract_codex_in_app_browser_context(text):
    """Split Codex Desktop's injected browser state from the typed request."""
    text = str(text or "")
    match = _CODEX_IN_APP_BROWSER_CONTEXT_RE.match(text)
    if not match:
        return text, None
    source_match = _CODEX_IN_APP_BROWSER_CONTEXT_SOURCE_RE.search(match.group("attrs") or "")
    context = {
        "source": (source_match.group("source").strip() if source_match else "in-app-browser"),
        "text": match.group("body").strip(),
    }
    return text[match.end():].strip(), context


def _codex_usage_delta_from_event(ev, previous_totals=None):
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    if payload.get("type") != "token_count":
        return None, previous_totals
    info = payload.get("info") or {}
    if not isinstance(info, dict):
        return None, previous_totals
    last_usage = info.get("last_token_usage")
    total_usage = info.get("total_token_usage")
    if isinstance(last_usage, dict) and last_usage:
        usage = last_usage
    elif isinstance(total_usage, dict) and total_usage:
        prev = previous_totals if isinstance(previous_totals, dict) else {}
        usage = {
            "input_tokens": max(
                _codex_int(total_usage.get("input_tokens"))
                - _codex_int(prev.get("input_tokens")),
                0,
            ),
            "cached_input_tokens": max(
                _codex_int(total_usage.get("cached_input_tokens"))
                - _codex_int(prev.get("cached_input_tokens")),
                0,
            ),
            "output_tokens": max(
                _codex_int(total_usage.get("output_tokens"))
                - _codex_int(prev.get("output_tokens")),
                0,
            ),
            "reasoning_output_tokens": max(
                _codex_int(total_usage.get("reasoning_output_tokens"))
                - _codex_int(prev.get("reasoning_output_tokens")),
                0,
            ),
            "total_tokens": max(
                _codex_int(total_usage.get("total_tokens"))
                - _codex_int(prev.get("total_tokens")),
                0,
            ),
        }
    else:
        return None, previous_totals
    next_totals = total_usage if isinstance(total_usage, dict) else previous_totals
    return {
        "input_tokens": _codex_int(usage.get("input_tokens")),
        "cached_input_tokens": _codex_int(usage.get("cached_input_tokens")),
        "output_tokens": _codex_int(usage.get("output_tokens")),
        "reasoning_output_tokens": _codex_int(usage.get("reasoning_output_tokens")),
        "total_tokens": _codex_int(usage.get("total_tokens")),
    }, next_totals


def _codex_compact_post_tokens_from_usage(usage):
    """Rebuilt-context size off a post-compaction `token_count`, else 0.

    Codex zeroes every per-turn field on the compaction turn and reports the
    new context size in `total_tokens` only, so a reading with input_tokens
    set is a NORMAL turn (a pre-compact size), not a post-compact one.
    """
    if not isinstance(usage, dict):
        return 0
    if _codex_int(usage.get("input_tokens")):
        return 0
    return _codex_int(usage.get("total_tokens"))


def _is_compact_boundary_event(event):
    return (
        isinstance(event, dict)
        and event.get("type") == "system"
        and event.get("subtype") == "compact_boundary"
    )


def _merge_codex_compact_boundary_events(events):
    """Collapse the pair of rows one Codex compaction produces into one.

    Codex records a compaction as a top-level `compacted` record (carrying the
    replacement history) AND an `event_msg`/`context_compacted` marker, with
    the token_count that reports the rebuilt size in between. The parser emits
    a boundary for each - the first knows the pre-compact size, the second the
    post-compact one - so merge adjacent boundaries into a single row. Both
    records are emitted because they are not perfectly paired in practice: a
    handful of rollouts carry the top-level record with no marker after it.

    Then adopt the compaction turn's own duration off the `result` row that
    follows, which is Codex's own task_started-to-task_complete measurement.
    """
    merged = []
    for event in events:
        if _is_compact_boundary_event(event) and merged and _is_compact_boundary_event(merged[-1]):
            prev = merged[-1].get("compact")
            cur = event.get("compact")
            if isinstance(prev, dict) and isinstance(cur, dict):
                for key in ("pre_tokens", "post_tokens", "duration_ms"):
                    prev[key] = max(_codex_int(prev.get(key)), _codex_int(cur.get(key)))
                if not prev.get("trigger"):
                    prev["trigger"] = cur.get("trigger") or ""
            continue
        merged.append(event)
    for idx, event in enumerate(merged):
        if not _is_compact_boundary_event(event):
            continue
        compact = event.get("compact")
        if not isinstance(compact, dict) or compact.get("duration_ms"):
            continue
        following = merged[idx + 1] if idx + 1 < len(merged) else None
        if isinstance(following, dict) and following.get("type") == "result":
            compact["duration_ms"] = _codex_int(following.get("duration_ms"))
    return merged


def _parse_codex_event(ev, line_num, token_usage=None, codex_turn_meta=None):
    ev_type = ev.get("type", "")
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    ptype = payload.get("type", "")
    ts = _core._codex_event_timestamp(ev)
    if ev_type == "compacted" or (
        ev_type == "event_msg" and ptype in ("compacted", "context_compacted")
    ):
        # Neither of Codex's two compaction records ever became a transcript
        # row, so a Codex compaction left no record card at all while the
        # Claude path has shown one for ages. Emit the same `compact_boundary`
        # shape (`compact: {pre_tokens, post_tokens, duration_ms, trigger}`)
        # the renderer already reads. `token_usage` is the most recent
        # token_count: the pre-compact one at the `compacted` record, the
        # post-compact one at the `context_compacted` marker that follows it.
        # `_merge_codex_compact_boundary_events` folds the pair together.
        return {
            "line": line_num,
            "ts": ts,
            "type": "system",
            "subtype": "compact_boundary",
            "engine": "codex",
            "compact": {
                "trigger": "manual",
                "pre_tokens": (
                    _codex_int(token_usage.get("input_tokens"))
                    if isinstance(token_usage, dict) else 0
                ),
                "post_tokens": _codex_compact_post_tokens_from_usage(token_usage),
                "duration_ms": 0,
            },
        }
    if ev_type == "event_msg":
        if ptype == "user_message":
            text = _core._strip_ccc_session_state_instruction(payload.get("message") or "")
            text = _core._strip_mode3_instruction(text)
            text, ambient_context = _extract_codex_in_app_browser_context(text)
            images = []
            if text or images or ambient_context:
                result = {"line": line_num, "ts": ts, "type": "user_text", "text": text, "images": images}
                if ambient_context:
                    result["ambient_context"] = ambient_context
                return _apply_codex_turn_meta(result, codex_turn_meta)
        if ptype == "agent_message":
            text = (payload.get("message") or "").strip()
            text, artifact, artifact_error = _core._extract_presentation_artifact(text)
            if text or artifact is not None or artifact_error:
                result = {
                    "line": line_num,
                    "ts": ts,
                    "type": "assistant",
                    "message_id": f"codex-{line_num}",
                    "blocks": ([{"kind": "text", "text": text}] if text else []),
                }
                if artifact is not None:
                    result["presentation_artifact"] = artifact
                if artifact_error:
                    result["presentation_artifact_error"] = artifact_error
                return _apply_codex_turn_meta(result, codex_turn_meta)
        if ptype == "task_complete":
            text = (payload.get("last_agent_message") or payload.get("message") or "").strip()
            result = {
                "line": line_num,
                "ts": ts,
                "type": "result",
                "duration_ms": payload.get("duration_ms", "?"),
            }
            if not text:
                result["no_agent_output"] = True
            if token_usage:
                result["token_usage"] = token_usage
            return result
        if ptype == "item_completed":
            # Newer Codex threads (e.g. multi-agent mode) stop emitting the
            # classic "user_message"/"agent_message" event_msg pair entirely
            # and wrap text in a nested `item` instead. UserMessage/AgentMessage
            # are handled below for that reason. CommandExecution/McpToolCall/
            # FileChange/Reasoning items are dual-emitted as `response_item`
            # records (custom_tool_call, reasoning, ...) that already render
            # via the branches further down, so they're deliberately skipped
            # here to avoid duplicate tool-call cards. SubAgentActivity/
            # CollabAgentToolCall are multi-agent-only bookkeeping (a root
            # thread spawning/messaging its own sub-agent threads) with no
            # response_item equivalent, so they get their own synthetic system
            # rows instead of being silently dropped.
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            itype = item.get("type")
            if itype == "UserMessage":
                parts = [
                    c["text"] for c in (item.get("content") or [])
                    if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)
                ]
                text = "\n\n".join(p.strip() for p in parts if p.strip()).strip()
                text = _core._strip_ccc_session_state_instruction(text)
                text = _core._strip_mode3_instruction(text)
                text, ambient_context = _extract_codex_in_app_browser_context(text)
                if not text and not ambient_context:
                    return None
                result = {"line": line_num, "ts": ts, "type": "user_text", "text": text, "images": []}
                if ambient_context:
                    result["ambient_context"] = ambient_context
                return _apply_codex_turn_meta(result, codex_turn_meta)
            if itype == "AgentMessage":
                parts = [
                    c["text"] for c in (item.get("content") or [])
                    if isinstance(c, dict) and c.get("type") == "Text" and isinstance(c.get("text"), str)
                ]
                text = "\n\n".join(p.strip() for p in parts if p.strip()).strip()
                text, artifact, artifact_error = _core._extract_presentation_artifact(text)
                if not text and artifact is None and not artifact_error:
                    return None
                result = {
                    "line": line_num,
                    "ts": ts,
                    "type": "assistant",
                    "message_id": f"codex-{line_num}",
                    "blocks": ([{"kind": "text", "text": text}] if text else []),
                }
                if artifact is not None:
                    result["presentation_artifact"] = artifact
                if artifact_error:
                    result["presentation_artifact_error"] = artifact_error
                return _apply_codex_turn_meta(result, codex_turn_meta)
            if itype == "SubAgentActivity":
                kind = str(item.get("kind") or "").strip()
                agent_path = str(item.get("agent_path") or "").strip()
                agent_thread_id = str(item.get("agent_thread_id") or "").strip()
                label = agent_path or (agent_thread_id[:8] if agent_thread_id else "sub-agent")
                verb = {"started": "Spawned sub-agent", "completed": "Sub-agent finished",
                        "failed": "Sub-agent failed"}.get(kind, "Sub-agent " + (kind or "activity"))
                return {
                    "line": line_num, "ts": ts, "type": "system", "subtype": "codex_subagent",
                    "kind": "subagent_" + (kind or "activity"), "agent_path": agent_path,
                    "agent_thread_id": agent_thread_id, "text": f"{verb}: {label}",
                }
            if itype == "CollabAgentToolCall":
                tool = str(item.get("tool") or "tool").strip()
                status = str(item.get("status") or "").strip()
                receivers = item.get("receiver_thread_ids") or item.get("receiver_agents") or []
                receiver_label = ", ".join(str(r) for r in receivers if r) if isinstance(receivers, list) else ""
                text = f"Agent {tool}" + (f" -> {receiver_label}" if receiver_label else "")
                if status:
                    text += f" ({status})"
                return {
                    "line": line_num, "ts": ts, "type": "system", "subtype": "codex_subagent",
                    "kind": "collab_" + tool, "tool": tool, "status": status, "text": text,
                }
            return None
        return None
    if ev_type != "response_item":
        return None
    if ptype == "reasoning":
        # Reasoning summaries render as thinking teasers in webui panes.
        parts = []
        for item in payload.get("summary") or []:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        text = "\n\n".join(p.strip() for p in parts if p.strip()).strip()
        if not text:
            return None
        return {
            "line": line_num,
            "ts": ts,
            "type": "assistant",
            "message_id": f"codex-reasoning-{line_num}",
            "blocks": [{"kind": "thinking", "text": text}],
        }
    if ptype in ("function_call", "custom_tool_call"):
        name = payload.get("name") or "tool"
        is_custom_tool = ptype == "custom_tool_call"
        raw_input = payload.get("input") if isinstance(payload.get("input"), str) else ""
        args = {} if is_custom_tool else _core._codex_args(payload.get("arguments"))
        # Structured update_plan calls render as the plan card (kimi-web
        # TodoList look) in webui panes instead of a tool row.
        if not is_custom_tool and _core._codex_tool_name(name) == "update_plan":
            plan = args.get("plan")
            if isinstance(plan, list) and plan:
                entries = []
                for item in plan:
                    if not isinstance(item, dict):
                        continue
                    step = str(item.get("step") or item.get("content") or "").strip()
                    if step:
                        entries.append({
                            "content": step[:300],
                            "status": str(item.get("status") or "pending"),
                        })
                if entries:
                    return {
                        "line": line_num,
                        "ts": ts,
                        "type": "assistant",
                        "message_id": f"codex-plan-{line_num}",
                        "blocks": [{"kind": "plan", "entries": entries}],
                    }
        # Custom-tool update_plan (JS body). Pure plan calls render as just
        # the plan card; mixed bodies (e.g. Promise.all batching exec_command
        # + update_plan in one call) keep their tool row and get the plan
        # card prepended as an extra block below.
        custom_plan_entries = (
            _core._codex_custom_tool_plan_entries(raw_input)
            if is_custom_tool and "tools.update_plan" in raw_input else []
        )
        if custom_plan_entries and _core._codex_custom_tool_kind(raw_input, name) == "update_plan":
            return {
                "line": line_num,
                "ts": ts,
                "type": "assistant",
                "message_id": f"codex-plan-{line_num}",
                "blocks": [{"kind": "plan", "entries": custom_plan_entries}],
            }
        detail = (
            _core._codex_custom_tool_detail(raw_input, name)
            if is_custom_tool else _core._codex_tool_detail(name, args)
        )
        if isinstance(detail, str) and len(detail) > 200:
            detail = detail[:200] + "..."
        block = {
            "kind": "tool_use",
            "name": _core._codex_custom_tool_kind(raw_input, name) if is_custom_tool else _core._codex_tool_name(name),
            "detail": detail or "",
            "id": payload.get("call_id") or payload.get("id") or "",
        }
        approval_required = _core._codex_tool_requires_approval(name, args, raw_input)
        approval_message = _core._codex_tool_approval_message(args, raw_input)
        if approval_required:
            block["approval_required"] = True
            block["approval_message"] = approval_message
        command_text = (
            _core._codex_custom_tool_command(raw_input)
            if is_custom_tool else _core._codex_tool_command(name, args)
        )
        if command_text:
            redacted_command = _core._redacted_shell_command_text(command_text, max_len=12000)
            if redacted_command and (
                "\n" in redacted_command
                or len(redacted_command) > 160
                or re.sub(r"\s+", " ", redacted_command).strip() != (detail or "")
            ):
                block["command"] = redacted_command
                here = _core._extract_shell_heredoc(command_text)
                block["command_kind"] = _core._shell_script_label(here.get("head", "")) if here else "Shell command"
        blocks = [block]
        if custom_plan_entries:
            blocks.insert(0, {"kind": "plan", "entries": custom_plan_entries})
        return {
            "line": line_num,
            "ts": ts,
            "type": "assistant",
            "message_id": f"codex-tool-{line_num}",
            "blocks": blocks,
        }
    if ptype in ("function_call_output", "custom_tool_call_output"):
        output = payload.get("output") or ""
        images = []
        if isinstance(output, list):
            # Structured output (e.g. screenshots). Inlining the base64 here
            # balloons the payload to tens of MB and stalls the open, so pull
            # images out as lazy (line, idx) refs — same scheme the claude
            # parser uses — and /api/conv-image fetches them on demand.
            parts = []
            for item in output:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    it = item.get("type")
                    url = item.get("image_url") or ""
                    if it == "input_image" and isinstance(url, str) and url.startswith("data:") and "base64," in url:
                        mt = url[5:].split(";", 1)[0] or "image/png"
                        images.append({"kind": "base64", "media_type": mt,
                                       "line": line_num, "idx": len(images)})
                    elif isinstance(item.get("text"), str):
                        parts.append(item["text"])
            output = "\n".join(parts)
        if not isinstance(output, str):
            output = str(output)
        if len(output) > 800:
            output = output[:800] + "\n..."
        result = {
            "line": line_num,
            "ts": ts,
            "type": "tool_result",
            "text": output,
            "tool_use_id": payload.get("call_id", ""),
            "is_error": False,
        }
        if images:
            result["images"] = images
        return result
    return None


_pending_resume_queue: dict = {}   # session_id → [text, ...]
# (session_id, text) -> {"queued_at": epoch, "reason": str}. Why a message is
# waiting and since when, so the UI can say "queued behind the running turn"
# instead of an ambiguous "sending". Entries are dropped when the queue for
# that session drains; a missing entry just means "no extra detail".
