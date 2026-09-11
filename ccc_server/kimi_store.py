"""Extracted from server.py (originally lines 40167-40767).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from pathlib import Path
import json
import os
import time

from ccc_server import core as _core

# ===========================================================================
# Kimi on-disk session store (~/.kimi-code) — read-only discovery.
#
# Kimi keeps a global index at $KIMI_CODE_HOME/session_index.jsonl
# ({sessionId, sessionDir, workDir} per line) and per-session metadata in
# <sessionDir>/state.json (title, lastPrompt, createdAt/updatedAt). The
# event-sourced transcript (agents/*/wire.jsonl) is undocumented; the head
# parse below is defensive and treats unknown event shapes as skippable.
# ===========================================================================

_KIMI_INDEX_CACHE = {"key": None, "rows": {}}
_KIMI_WIRE_HEAD_MAX_LINES = 40


def _kimi_code_home():
    raw = os.environ.get("KIMI_CODE_HOME", "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return Path.home() / ".kimi-code"


def _kimi_session_index():
    """sid -> {"session_dir", "work_dir"} from session_index.jsonl
    (mtime-keyed cache; missing/unreadable file -> {})."""
    path = _kimi_code_home() / "session_index.jsonl"
    try:
        st = path.stat()
    except OSError:
        return {}
    key = (st.st_mtime_ns, st.st_size)
    if _KIMI_INDEX_CACHE["key"] == key:
        return _KIMI_INDEX_CACHE["rows"]
    rows = {}
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = rec.get("sessionId")
                if sid:
                    rows[str(sid)] = {
                        "session_dir": str(rec.get("sessionDir") or ""),
                        "work_dir": str(rec.get("workDir") or ""),
                    }
    except OSError:
        return {}
    _KIMI_INDEX_CACHE["key"] = key
    _KIMI_INDEX_CACHE["rows"] = rows
    return rows


def _canonical_kimi_session_id(session_id):
    """Return Kimi's indexed ``session_<uuid>`` id for a bare UUID alias."""
    sid = str(session_id or "").strip()
    if not sid or sid.startswith("session_"):
        return sid
    canonical = f"session_{sid}"
    return canonical if canonical in _core._kimi_session_index() else sid


def _kimi_state_meta(session_dir):
    """Read <sessionDir>/state.json. Defensive: any failure -> {}."""
    if not session_dir:
        return {}
    try:
        with (Path(session_dir) / "state.json").open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _kimi_wire_head(session_dir):
    """First user prompt, main wire path, and model alias from
    agents/main/wire.jsonl.

    The wire format is undocumented (observed: turn.prompt carries
    input=[{type:text,text}]; config.update carries modelAlias, llm.request
    confirms it). Anything unexpected just leaves that field empty.
    """
    wire = Path(session_dir) / "agents" / "main" / "wire.jsonl" if session_dir else None
    info = {"first_prompt": "", "wire_path": str(wire) if wire else "", "model": ""}
    if wire is None or not wire.is_file():
        return info
    try:
        with wire.open() as f:
            for i, line in enumerate(f):
                if i >= _KIMI_WIRE_HEAD_MAX_LINES:
                    break
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")
                if etype == "config.update":
                    alias = str(ev.get("modelAlias") or "").strip()
                    if alias:
                        # Keep the LAST alias in the window: a session created
                        # with the default model and immediately re-configured
                        # (CCC spawn, kimi -m) records k3 then the real model
                        # within the first lines.
                        info["model"] = alias
                elif etype == "turn.prompt" and not info["first_prompt"]:
                    blocks = ev.get("input")
                    if isinstance(blocks, list):
                        text = "".join(
                            str(b.get("text") or "") for b in blocks
                            if isinstance(b, dict) and b.get("type") == "text"
                        ).strip()
                        if text:
                            info["first_prompt"] = text
    except OSError:
        pass
    return info


_KIMI_WIRE_TAIL_BYTES = 65536
_KIMI_WIRE_TAIL_CACHE = {}  # wire path -> ((mtime_ns, size), meta)
_KIMI_WIRE_USAGE_CACHE = {}  # wire path -> incremental usage scan state


try:
    _KIMI_CONTEXT_LIMIT = int(os.environ.get("CCC_KIMI_CONTEXT_LIMIT", "256000") or "256000")
except ValueError:
    _KIMI_CONTEXT_LIMIT = 256000


def _kimi_wire_usage_meta(session_dir):
    """Cumulative per-turn token usage AND the latest context-window size,
    without re-reading old rows.

    Incrementally scans agents/main/wire.jsonl (resumable via a cached
    (inode, offset) checkpoint, same shape as the tail-meta cache above) so
    repeat polls only cost the bytes appended since the last read. Mirrors
    the parsing `_extract_kimi_usage` does on the full file, but keeps it
    cheap enough to run on every kimi row in the archive/list build:
    `context.update_token_count` carries the live context size the Kimi TUI
    itself shows; when a session never emits one (older wire format), the
    latest `usage.record` turn window is the closest estimate.

    Returns {"lifetime_tokens": int, "latest_input_tokens": int}.
    """
    wire = Path(session_dir) / "agents" / "main" / "wire.jsonl" if session_dir else None
    empty = {"lifetime_tokens": 0, "latest_input_tokens": 0}
    if wire is None:
        return empty
    try:
        st = wire.stat()
    except OSError:
        return empty
    cache_key = str(wire)
    cached = _KIMI_WIRE_USAGE_CACHE.get(cache_key) or {}
    can_resume = (
        cached.get("inode") == st.st_ino
        and st.st_size >= cached.get("offset", 0)
    )
    offset = cached.get("offset", 0) if can_resume else 0
    total = cached.get("total", 0) if can_resume else 0
    latest_from_count = cached.get("latest_from_count", 0) if can_resume else 0
    latest_from_step = cached.get("latest_from_step", 0) if can_resume else 0
    try:
        with wire.open("rb") as handle:
            if offset:
                handle.seek(offset)
            while True:
                line_start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    offset = line_start
                    break
                offset = handle.tell()
                if (
                    b'"usage.record"' not in raw
                    and b'"usage"' not in raw
                    and b'"context.update_token_count"' not in raw
                ):
                    continue
                try:
                    record = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                rtype = record.get("type")
                if rtype == "context.update_token_count":
                    latest_from_count = _core._codex_int(record.get("tokenCount"))
                    continue
                if rtype != "usage.record":
                    continue
                if record.get("usageScope") not in (None, "turn"):
                    continue
                usage = record.get("usage")
                if not isinstance(usage, dict):
                    continue
                total += sum(_core._codex_int(usage.get(key)) for key in (
                    "inputOther",
                    "inputCacheRead",
                    "inputCacheCreation",
                    "output",
                ))
                latest_from_step = sum(_core._codex_int(usage.get(key)) for key in (
                    "inputOther",
                    "inputCacheRead",
                    "inputCacheCreation",
                ))
    except OSError:
        pass
    if len(_KIMI_WIRE_USAGE_CACHE) > 512:
        _KIMI_WIRE_USAGE_CACHE.clear()
    _KIMI_WIRE_USAGE_CACHE[cache_key] = {
        "inode": st.st_ino,
        "offset": offset,
        "total": total,
        "latest_from_count": latest_from_count,
        "latest_from_step": latest_from_step,
    }
    return {
        "lifetime_tokens": total,
        "latest_input_tokens": latest_from_count or latest_from_step,
    }


def _kimi_wire_lifetime_tokens(session_dir):
    """Back-compat wrapper — see _kimi_wire_usage_meta."""
    return _kimi_wire_usage_meta(session_dir)["lifetime_tokens"]


def _kimi_wire_tail_meta(session_dir):
    """Last-turn shape from the tail of agents/main/wire.jsonl.

    The kimi analogue of _extract_codex_tail_meta: a bounded tail read
    ((mtime,size)-keyed cache, so repeat polls cost one stat) answering "did
    the last turn finish, and is a tool call still dangling?" The wire is
    event-sourced (docs/kimi-code-reference.md §5): a finished turn always
    ends in step.end (finishReason end_turn / cancelled / …) or turn.cancel.
    step.end with finishReason "tool_use" only closes a STEP — the agent
    loop continues — so it still reads mid-turn.
    """
    meta = {"last_event_type": None, "pending_tool": None, "mid_turn": False, "wire_mtime": 0.0}
    wire = Path(session_dir) / "agents" / "main" / "wire.jsonl" if session_dir else None
    if wire is None:
        return meta
    try:
        st = wire.stat()
    except OSError:
        return meta
    meta["wire_mtime"] = st.st_mtime
    key = (st.st_mtime_ns, st.st_size)
    cache_key = str(wire)
    cached = _KIMI_WIRE_TAIL_CACHE.get(cache_key)
    if cached and cached[0] == key:
        return dict(cached[1])
    try:
        with wire.open("rb") as f:
            if st.st_size > _KIMI_WIRE_TAIL_BYTES:
                f.seek(-_KIMI_WIRE_TAIL_BYTES, os.SEEK_END)
            raw = f.read(_KIMI_WIRE_TAIL_BYTES)
    except OSError:
        return meta
    pending = {}
    last_type = None
    mid_turn = False
    for line in raw.decode("utf-8", "replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue  # the window's first line is usually truncated
        etype = ev.get("type")
        if etype == "turn.prompt":
            pending.clear()
            last_type = "user"
            mid_turn = True
        elif etype == "turn.cancel":
            pending.clear()
            last_type = "result"
            mid_turn = False
        elif etype == "context.append_message":
            if (ev.get("message") or {}).get("role") == "user":
                last_type = "user"
                mid_turn = True
        elif etype == "context.append_loop_event":
            loop = ev.get("event") or {}
            ltype = loop.get("type")
            if ltype == "step.begin":
                mid_turn = True
            elif ltype == "content.part":
                if (loop.get("part") or {}).get("type") in ("text", "think"):
                    last_type = "assistant"
            elif ltype == "tool.call":
                tid = str(loop.get("toolCallId") or "")
                if tid:
                    pending[tid] = str(loop.get("name") or "tool")
                last_type = "assistant"
            elif ltype == "tool.result":
                pending.pop(str(loop.get("toolCallId") or ""), None)
                last_type = "assistant"
            elif ltype == "step.end":
                finish = str(loop.get("finishReason") or "end_turn")
                if finish == "tool_use":
                    # Only a step closed — the agent loop continues.
                    mid_turn = True
                    last_type = "assistant"
                else:
                    pending.clear()
                    last_type = "result"
                    mid_turn = False
        # llm.*, usage.record, config.update and observability rows carry no
        # turn-shape signal — skipped by design.
    meta["last_event_type"] = last_type
    meta["pending_tool"] = list(pending.values())[-1] if pending else None
    meta["mid_turn"] = bool(mid_turn or pending or last_type in ("user", "assistant"))
    if len(_KIMI_WIRE_TAIL_CACHE) > 512:
        _KIMI_WIRE_TAIL_CACHE.clear()
    _KIMI_WIRE_TAIL_CACHE[cache_key] = (key, dict(meta))
    return meta


def _kimi_stale_tool_fields(tail_meta, acp_active=False, now=None, threshold_s=None):
    """Stale-tool fields for a kimi session — same contract as
    _codex_stale_tool_fields, so the existing row "Stuck" pill / stuck chip
    and the pane's stuck card light up with no client-side engine special
    cases. Mid-turn means the ACP snapshot says a turn is active OR the wire
    tail shows an unfinished turn; "stale" means the wire (appended to
    throughout a live turn) has had no output for CCC_STALE_TOOL_SEC
    (default 900s)."""
    threshold = _core._stale_tool_threshold_s() if threshold_s is None else max(0.0, float(threshold_s))
    fields = {
        "stale_tool_call": False,
        "stale_tool_age_s": 0,
        "stale_tool_threshold_s": int(threshold),
    }
    if not tail_meta:
        return fields
    if not (acp_active or tail_meta.get("mid_turn")):
        return fields
    ts = tail_meta.get("wire_mtime") or 0
    if not ts:
        return fields
    try:
        age = max(0.0, float(now if now is not None else time.time()) - float(ts))
    except (TypeError, ValueError):
        return fields
    fields["stale_tool_age_s"] = int(age)
    fields["stale_tool_call"] = bool(threshold > 0 and age >= threshold)
    return fields


def _restamp_kimi_row_tail_fields(row):
    """Serve-time refresh of a kimi row's wire-tail fields, IN PLACE.

    The archive corpus signature covers kimi at directory granularity (per-
    turn wire.jsonl appends deliberately do NOT bust the build cache), so
    build-time stamps alone would freeze last_event_type / stale_tool_call
    until an unrelated rebuild. Recompute from the (mtime,size)-keyed tail
    cache on every rehydrate — one stat per kimi row when nothing changed.
    """
    sid = row.get("session_id") or row.get("id")
    idx = _core._kimi_session_index().get(sid) or {}
    tail_meta = _core._kimi_wire_tail_meta(idx.get("session_dir"))
    usage_meta = _kimi_wire_usage_meta(idx.get("session_dir"))
    row["lifetime_tokens"] = usage_meta["lifetime_tokens"]
    if usage_meta["latest_input_tokens"]:
        row["latest_input_tokens"] = usage_meta["latest_input_tokens"]
    row.setdefault("context_limit", _KIMI_CONTEXT_LIMIT)
    row["last_event_type"] = tail_meta.get("last_event_type")
    if tail_meta.get("wire_mtime"):
        row["last_event_ts"] = tail_meta["wire_mtime"]
    pending = tail_meta.get("pending_tool")
    row["pending_tool"] = pending
    row["pending_tool_ts"] = tail_meta.get("wire_mtime") if pending else 0
    acp_active = False
    try:
        with _core._ACP_LOCK:
            _core._acp_load_state("kimi")
            state = _core._acp_session("kimi", sid)
            acp_active = bool(state and state.get("status") == "active")
            waiting = bool(state and state.get("pending_permissions"))
    except Exception:
        waiting = False
    if waiting or row.get("needs_approval"):
        # Parked on a human approval is "waiting", never "stuck".
        row.update(_core._kimi_stale_tool_fields(None))
    else:
        row.update(_core._kimi_stale_tool_fields(tail_meta, acp_active=acp_active))


def find_kimi_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    """Session rows for the Kimi engine (source/engine "kimi").

    Merges CCC-attached ACP sessions (live status, model) with Kimi's own
    on-disk index (TUI sessions included), newest first.
    """
    if not _core._acp_harness_enabled("kimi"):
        return []
    rows = []
    repo_path_obj = Path(repo_path).resolve() if repo_path else None
    git_top_cache = {}
    with _core._ACP_LOCK:
        _core._acp_load_state("kimi")
        attached = dict(_core._ACP_SESSION_STATE.get("kimi") or {})
    index = _core._kimi_session_index()
    try:
        pins = _core._load_repo_pins()
    except Exception:
        pins = {}
    try:
        name_overrides = _core._load_session_name_overrides()
    except Exception:
        name_overrides = {}
    try:
        auto_titled = _core._auto_titled_session_ids()
    except Exception:
        auto_titled = {}
    try:
        archived_set, trashed_set = _core._load_conversation_lifecycle_sets()
    except Exception:
        archived_set, trashed_set = set(), set()
    try:
        verified_set = set(_core._load_verified_conversations())
    except Exception:
        verified_set = set()
    # Hoisted out of the row loop below — one read each, never per session.
    try:
        session_overrides = _core._load_session_overrides()
    except Exception:
        session_overrides = {}
    try:
        spawn_registry_by_sid = _core._spawn_registry_entries_by_session(engine="kimi")
    except Exception:
        spawn_registry_by_sid = {}
    now = time.time()
    max_age_days = int(os.environ.get("CCC_MAX_CONV_AGE_DAYS", "30") or "30")
    cutoff = 0 if include_old else now - (max_age_days * 86400)

    sids = set(index) | set(attached)
    for sid in sids:
        idx = index.get(sid) or {}
        acp = attached.get(sid) or {}
        meta = _kimi_state_meta(idx.get("session_dir"))
        cwd = (
            (acp.get("cwd") or "").strip()
            or (meta.get("workDir") or "").strip()
            or (idx.get("work_dir") or "").strip()
        )
        pinned = pins.get(sid) or ""
        if repo_only and repo_path_obj is not None:
            if pinned and pinned != str(repo_path_obj):
                continue
            if not pinned:
                if not cwd or not _core._codex_cwd_matches_repo(cwd, repo_path_obj, git_top_cache):
                    continue
        modified = 0.0
        for cand in (
            _core._iso_to_epoch(meta.get("updatedAt")),
            _core._iso_to_epoch(meta.get("createdAt")),
            acp.get("updated_at") or 0,
        ):
            if cand:
                modified = max(modified, float(cand))
        if not modified:
            try:
                modified = (Path(idx.get("session_dir") or "") / "state.json").stat().st_mtime
            except OSError:
                modified = 0.0
        # Kimi records each completed conversation turn in wire.jsonl, while
        # state.json can retain an older timestamp. Use the newest durable
        # activity source so session cards do not report a stale age.
        try:
            modified = max(
                modified,
                (Path(idx.get("session_dir") or "") / "agents" / "main" / "wire.jsonl").stat().st_mtime,
            )
        except OSError:
            pass
        if cutoff and modified and modified < cutoff:
            continue
        title = _core._strip_f2_retrieval_prompt(
            _core._strip_ccc_kimi_goal_prefix(
                _core._strip_ccc_session_state_instruction(str(meta.get("title") or "")).strip()
            )
        )
        last_prompt = _core._strip_f2_retrieval_prompt(
            _core._strip_ccc_kimi_goal_prefix(
                _core._strip_ccc_session_state_instruction(str(meta.get("lastPrompt") or "")).strip()
            )
        )
        wire_info = _kimi_wire_head(idx.get("session_dir"))
        first_message = _core._strip_f2_retrieval_prompt(
            _core._strip_ccc_kimi_goal_prefix(wire_info["first_prompt"])
        ) or last_prompt
        wire_path = wire_info["wire_path"]
        display_name = (
            name_overrides.get(sid)
            or _core._truncate_session_name(title)
            or (auto_titled.get(sid) if auto_titled else None)
            or (first_message[:80] if first_message else None)
            or (title[:80] if title else "Kimi session")
        )
        effective_cwd = _core._first_existing_dir(cwd, pinned) or cwd
        try:
            cwd_exists = bool(effective_cwd and Path(effective_cwd).is_dir())
        except OSError:
            cwd_exists = False
        folder_path = pinned or cwd or effective_cwd or ""
        if folder_path:
            _git_root = _core._find_git_root(folder_path)
            folder_label = _core._resolve_dir_case(_git_root or folder_path)
        else:
            folder_label = "Kimi"
        _wt_worktree_label = None
        _wt_idx = folder_label.find("-wt-")
        if _wt_idx > 0:
            _wt_worktree_label = folder_label[_wt_idx + 4:]
            folder_label = folder_label[:_wt_idx]
        status = (acp.get("status") or "").strip()
        row = {
            "id": sid,
            "session_id": sid,
            "source": "kimi",
            "engine": "kimi",
            "timestamp": "",
            "branch": "",
            "git_branch": "",
            "first_message": first_message[:200],
            "original_ask": first_message,
            "display_name": display_name,
            "ai_title": title or None,
            "name_overridden": bool(name_overrides.get(sid)),
            "last_prompt": (last_prompt or first_message)[:200],
            "size": 0,
            "modified": modified,
            "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)) if modified else "",
            "mtime": modified,
            "jsonl_path": wire_path,
            "folder_label": folder_label,
            "folder_path": folder_path,
            "worktree_label": _wt_worktree_label,
            "session_cwd": effective_cwd,
            "session_cwd_exists": cwd_exists,
            "acp": bool(acp),
            "archived": sid in archived_set,
            "trashed": sid in trashed_set,
            "verified": sid in verified_set,
            # Live ACP value wins; the wire's initial config.update alias
            # covers never-attached (TUI / WT-spawned) sessions.
            "model": acp.get("model") or wire_info["model"],
            # Kimi's thinking effort. ACP does not report it back, so the
            # value CCC spawned or last picked is the only source.
            "reasoning_effort": _core._conv_row_reasoning_effort(
                sid, session_overrides, spawn_registry_by_sid.get(sid),
            ),
        }
        if status:
            row["status"] = "running" if status == "active" else "idle"
        if acp:
            # Lets the kanban swap a spawning-* placeholder for this card.
            row["spawn_pid"] = sid
            if acp.get("pending_permissions"):
                row["needs_approval"] = True
                row["needs_approval_message"] = "Kimi is waiting for a tool approval"
        # Wire-tail turn shape: last_event_type feeds the row's "done" chip
        # (result + recent), a dangling tool names the Stuck pill, and the
        # stale fields light the same stuck indicators codex rows use.
        tail_meta = _core._kimi_wire_tail_meta(idx.get("session_dir"))
        usage_meta = _kimi_wire_usage_meta(idx.get("session_dir"))
        row["lifetime_tokens"] = usage_meta["lifetime_tokens"]
        row["latest_input_tokens"] = usage_meta["latest_input_tokens"]
        row["context_limit"] = _KIMI_CONTEXT_LIMIT
        row["last_event_type"] = tail_meta.get("last_event_type")
        if tail_meta.get("wire_mtime"):
            row["last_event_ts"] = tail_meta["wire_mtime"]
        if tail_meta.get("pending_tool"):
            row["pending_tool"] = tail_meta["pending_tool"]
            row["pending_tool_ts"] = tail_meta["wire_mtime"]
        if row.get("needs_approval"):
            # Parked on a human approval is "waiting", never "stuck".
            row.update(_core._kimi_stale_tool_fields(None))
        else:
            row.update(_core._kimi_stale_tool_fields(tail_meta, acp_active=(status == "active")))
        rows.append(row)
    rows.sort(key=lambda r: r.get("modified") or 0, reverse=True)
    if limit:
        rows = rows[:limit]
    return rows

