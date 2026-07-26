"""Extracted from server.py (originally lines 35853-37258).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import uuid

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Cursor Agent integration
# ---------------------------------------------------------------------------

_CURSOR_USER_QUERY_RE = re.compile(
    r"<user_query>\s*(.*?)\s*</user_query>",
    re.IGNORECASE | re.DOTALL,
)


def _cursor_project_slug(path):
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        resolved = Path(str(path or ""))
    return re.sub(r"[^A-Za-z0-9]", "-", str(resolved).lstrip("/"))


def _cursor_cwd_from_project_slug(slug):
    slug = str(slug or "").strip()
    if not slug:
        return ""
    try:
        for repo in _core._known_repo_paths():
            if _cursor_project_slug(repo) == slug:
                return repo
    except Exception:
        pass
    decoded = _core._decode_project_slug("-" + slug)
    if decoded:
        try:
            return str(decoded.resolve())
        except (OSError, RuntimeError, ValueError):
            return str(decoded)
    naive = "/" + slug.replace("-", "/")
    try:
        p = Path(naive)
        if p.is_dir():
            return str(p.resolve())
    except (OSError, RuntimeError, ValueError):
        pass
    return ""


def _cursor_project_slug_from_transcript(path):
    try:
        return Path(path).parents[2].name
    except (IndexError, TypeError, ValueError):
        return ""


def _cursor_cwd_from_transcript_path(path):
    return _cursor_cwd_from_project_slug(_cursor_project_slug_from_transcript(path))


def _cursor_transcript_paths():
    root = _core.CURSOR_PROJECTS_ROOT
    if not root.is_dir():
        return []
    paths = []
    try:
        for path in root.glob("*/agent-transcripts/*/*.jsonl"):
            if path.is_file():
                paths.append(path)
    except OSError:
        return []
    try:
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        paths.sort(key=lambda p: str(p), reverse=True)
    return paths


def _cursor_transcript_path(session_id):
    if not session_id:
        return None
    sid = str(session_id).strip()
    root = _core.CURSOR_PROJECTS_ROOT
    if not sid or not root.is_dir():
        return None
    paths = []
    try:
        exact = list(root.glob(f"*/agent-transcripts/{sid}/{sid}.jsonl"))
        fallback = list(root.glob(f"*/agent-transcripts/{sid}/*.jsonl"))
        paths = [p for p in (exact + fallback) if p.is_file()]
    except OSError:
        paths = []
    if not paths:
        return None
    try:
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        paths.sort(key=lambda p: str(p), reverse=True)
    return paths[0]


def _is_cursor_session(session_id):
    path = _core._cursor_transcript_path(session_id)
    return bool(path and path.is_file())


def _cursor_chat_id_from_path(path):
    try:
        p = Path(path)
        return p.stem or p.parent.name
    except (TypeError, ValueError):
        return ""


def _cursor_content_blocks(ev):
    if not isinstance(ev, dict):
        return []
    msg = ev.get("message")
    content = msg.get("content") if isinstance(msg, dict) and "content" in msg else ev.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if isinstance(ev.get("text"), str):
        return [{"type": "text", "text": ev.get("text")}]
    return []


def _cursor_event_role(ev):
    if not isinstance(ev, dict):
        return ""
    msg = ev.get("message")
    return ev.get("role") or (msg.get("role") if isinstance(msg, dict) else "") or ""


def _cursor_message_text(ev):
    parts = []
    for block in _cursor_content_blocks(ev):
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text = _cursor_visible_text(block.get("text"))
            if text:
                parts.append(text)
    return "\n".join(p for p in parts if p)


def _cursor_text_is_redacted_placeholder(text):
    return isinstance(text, str) and text.strip().lower() == "[redacted]"


def _cursor_visible_text(text):
    if not isinstance(text, str):
        return ""
    lines = [line for line in text.splitlines() if not _cursor_text_is_redacted_placeholder(line)]
    return "\n".join(lines).strip()


def _cursor_user_text(text):
    if not isinstance(text, str):
        return ""
    match = _CURSOR_USER_QUERY_RE.search(text)
    if match:
        text = match.group(1)
    return _core._strip_ccc_session_state_instruction(text).strip()


def _cursor_event_epoch(ev):
    ts = (ev or {}).get("timestamp") or (ev or {}).get("created_at") or (ev or {}).get("time") or ""
    return _core._iso_ts_epoch(ts)


def _cursor_event_timestamp(ev):
    return (ev or {}).get("timestamp") or (ev or {}).get("created_at") or (ev or {}).get("time") or ""


def _cursor_tool_name(block):
    return (block.get("name") or block.get("tool_name") or block.get("displayName") or "tool").rsplit(".", 1)[-1]


def _cursor_tool_args(block):
    args = block.get("input")
    if not isinstance(args, dict):
        args = block.get("args")
    return args if isinstance(args, dict) else {}


def _cursor_tool_command(block):
    args = _cursor_tool_args(block)
    cmd = args.get("command") or args.get("cmd") or args.get("script") or ""
    return cmd if isinstance(cmd, str) else ""


def _cursor_tool_workdir(block):
    args = _cursor_tool_args(block)
    cwd = args.get("cwd") or args.get("workdir") or args.get("workspace") or ""
    return cwd if isinstance(cwd, str) else ""


def _cursor_tool_detail(block):
    args = _cursor_tool_args(block)
    cmd = _cursor_tool_command(block)
    if cmd:
        return _core._shell_command_activity_label(cmd, max_len=1200)
    for key in (
        "file_path", "target_file", "path", "notebook_path", "directory",
        "pattern", "query", "glob_pattern", "description", "prompt", "message",
    ):
        value = args.get(key)
        if isinstance(value, str) and value:
            return _core._prompt_fragment(value, 240)
    for raw in _core._ffc_flatten_strings(args):
        if isinstance(raw, str) and raw:
            return _core._prompt_fragment(raw, 240)
    return ""


_cursor_tail_resume = {}  # path -> {offset, pos, pending_tool, meta, meta_version}


def _extract_cursor_tail_meta(path):
    try:
        path = Path(path)
        st = path.stat()
    except (OSError, TypeError, ValueError):
        return {}
    mtime = st.st_mtime
    size = st.st_size
    spath = str(path)
    cached = _core._conv_meta_cache.get(spath)
    if (
        cached
        and cached.get("mtime") == mtime
        and cached.get("engine") == "cursor"
        and cached.get("meta_version") == _core._CURSOR_META_VERSION
    ):
        return cached

    # Incremental resume from the last byte offset — cursor transcripts are
    # append-only JSONL, so a live session's growing file is parsed only over
    # its newly-appended lines instead of from the top each poll. See
    # _codex_tail_resume for the rationale.
    with _core._conv_meta_cache_lock:
        resume = _cursor_tail_resume.get(spath)
    if (
        resume
        and resume.get("meta_version") == _core._CURSOR_META_VERSION
        and size >= resume.get("offset", 0)
    ):
        meta = resume["meta"]
        pending_tool = resume["pending_tool"]
        pos = resume["pos"]
        start_offset = resume["offset"]
    else:
        meta = {
            "engine": "cursor",
            "meta_version": _core._CURSOR_META_VERSION,
            "mtime": mtime,
            "first_message": None,
            "last_meaningful_ts": 0,
            "last_prompt": None,
            "last_assistant_text": None,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "pending_tool_ts": 0,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_edit_pos": 0,
            "last_commit_pos": 0,
            "last_push_pos": 0,
            "tail_pr_number": None,
            "tail_pr_url": None,
            "tail_branch": None,
            "tail_worktree_path": None,
            "has_external_cd": False,
            "cwd": _cursor_cwd_from_transcript_path(path),
            "model": None,
        }
        pending_tool = False
        pos = 0
        start_offset = 0
    pr_url_re = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d{1,7})")
    try:
        with open(path, "rb") as f:
            if start_offset:
                f.seek(start_offset)
            while True:
                raw = f.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    break
                start_offset += len(raw)
                pos += 1
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_epoch = _cursor_event_epoch(ev)
                if ts_epoch:
                    meta["last_meaningful_ts"] = ts_epoch
                if isinstance(ev.get("cwd"), str) and ev.get("cwd").strip():
                    meta["cwd"] = ev.get("cwd").strip()
                if isinstance(ev.get("workspace"), str) and ev.get("workspace").strip():
                    meta["cwd"] = ev.get("workspace").strip()
                if isinstance(ev.get("model"), str) and ev.get("model").strip():
                    meta["model"] = ev.get("model").strip()
                role = _cursor_event_role(ev)
                if role == "user":
                    text = _cursor_user_text(_cursor_message_text(ev))
                    if text:
                        meta["first_message"] = meta["first_message"] or text
                        meta["last_prompt"] = text
                    meta["last_event_type"] = "user"
                    meta["pending_tool"] = None
                    meta["pending_file"] = None
                    meta["pending_tool_ts"] = 0
                    pending_tool = False
                    continue
                if role != "assistant":
                    if pending_tool:
                        meta["pending_tool"] = None
                        meta["pending_file"] = None
                        meta["pending_tool_ts"] = 0
                        pending_tool = False
                    continue
                text = _cursor_message_text(ev).strip()
                if text:
                    meta["last_assistant_text"] = text
                    meta.update(_core._extract_codex_summary_signals(text, pr_url_re))
                meta["last_event_type"] = "assistant"
                saw_tool_use = False
                for block in _cursor_content_blocks(ev):
                    if block.get("type") != "tool_use":
                        continue
                    saw_tool_use = True
                    name = _cursor_tool_name(block)
                    detail = _cursor_tool_detail(block)
                    meta["pending_tool"] = name
                    meta["pending_file"] = detail[:80] if isinstance(detail, str) else None
                    meta["pending_tool_ts"] = ts_epoch or meta.get("last_meaningful_ts") or mtime
                    pending_tool = True
                    lname = name.lower()
                    if lname in (
                        "edit", "write", "writefile", "write_file", "replace",
                        "replace_file", "apply_patch", "notebookedit",
                    ):
                        meta["has_edit"] = True
                        meta["last_edit_pos"] = pos
                    cmd = _cursor_tool_command(block)
                    if cmd:
                        signals = _core._codex_command_signals(
                            cmd,
                            base_cwd=_cursor_tool_workdir(block) or meta.get("cwd"),
                        )
                        if signals["edit"]:
                            meta["has_edit"] = True
                            meta["last_edit_pos"] = pos
                        if signals["commit"]:
                            meta["has_commit"] = True
                            meta["last_commit_pos"] = pos
                        if signals["push"]:
                            meta["has_push"] = True
                            meta["last_push_pos"] = pos
                        if signals["external_cd"]:
                            meta["has_external_cd"] = True
                        if signals.get("worktree_path"):
                            meta["tail_worktree_path"] = signals["worktree_path"]
                        if signals.get("worktree_branch"):
                            meta["tail_branch"] = signals["worktree_branch"]
                # Cursor emits one JSONL line per tool call; a follow-up line
                # with only assistant text means the turn finished.
                if text and not saw_tool_use:
                    meta["pending_tool"] = None
                    meta["pending_file"] = None
                    meta["pending_tool_ts"] = 0
                    pending_tool = False
            end_offset = start_offset
    except OSError:
        return {}

    meta["mtime"] = mtime
    if not meta.get("last_meaningful_ts"):
        meta["last_meaningful_ts"] = mtime
    with _core._conv_meta_cache_lock:
        _core._conv_meta_cache[spath] = meta
        _cursor_tail_resume[spath] = {
            "meta_version": _core._CURSOR_META_VERSION,
            "offset": end_offset,
            "pos": pos,
            "pending_tool": pending_tool,
            "meta": meta,
        }
        _core._conv_meta_cache_dirty = True
    return meta


def _cursor_activity_fields_from_tail(tail, live):
    # Cursor JSONLs don't carry per-event timestamps, so the codex stale-
    # tool check can't honestly compute "how long has this tool been
    # pending" — pending_tool_ts gets the file's mtime as a fallback,
    # which keeps refreshing as the JSONL appends more lines. The result
    # was that a finished cursor session would keep showing "▶ Bash …"
    # in the sidebar pill long after the user closed the cursor app.
    # Use file idleness as the actual signal: if the JSONL hasn't been
    # written to in the configured window, treat the session as idle and
    # drop the in-flight pill regardless of the dangling pending_tool.
    try:
        idle_threshold = float(os.environ.get("CCC_CURSOR_IDLE_SEC", "60"))
    except (TypeError, ValueError):
        idle_threshold = 60.0
    if live and tail and idle_threshold > 0:
        mtime = float(tail.get("mtime") or 0)
        if mtime > 0 and (time.time() - mtime) > idle_threshold:
            return {
                "sidecar_status": None,
                "sidecar_has_writes": False,
                "sidecar_tool": None,
                "sidecar_file": None,
                "sidecar_ts": 0,
                "sidecar_in_flight": False,
                "question_waiting": False,
                "question_text": "",
                "question_header": "",
                "question_preamble": "",
                "question_options": [],
                "question_option_details": [],
            }
    return _core._codex_activity_fields_from_tail(tail, live)


def _extract_cursor_chat_id_from_log(log_path):
    if not log_path:
        return None
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for key in ("chatId", "chat_id", "session_id", "conversationId", "conversation_id"):
                    sid = ev.get(key)
                    if isinstance(sid, str) and _core._SESSION_UUID_RE.match(sid):
                        return sid
                nested = ev.get("chat") or ev.get("conversation")
                if isinstance(nested, dict):
                    sid = nested.get("id")
                    if isinstance(sid, str) and _core._SESSION_UUID_RE.match(sid):
                        return sid
    except OSError:
        return None
    return None


def _cursor_session_id_for_spawn_entry(entry):
    if not isinstance(entry, dict):
        return None
    cwd = entry.get("cwd") or entry.get("repo_path") or ""
    if not cwd:
        return None
    slug = _cursor_project_slug(cwd)
    try:
        candidates = [
            p for p in _core.CURSOR_PROJECTS_ROOT.glob(f"{slug}/agent-transcripts/*/*.jsonl")
            if p.is_file()
        ]
    except OSError:
        candidates = []
    if not candidates:
        return None
    started = _core._spawn_registry_entry_epoch(entry)
    prompt = re.sub(
        r"\s+",
        " ",
        _core._strip_ccc_session_state_instruction(entry.get("prompt") or entry.get("command_summary") or "").strip(),
    ).lower()
    matched = []
    for path in candidates:
        try:
            st = path.stat()
        except OSError:
            continue
        if started and st.st_mtime < started - 120:
            continue
        tail = _extract_cursor_tail_meta(path) or {}
        first = re.sub(r"\s+", " ", (tail.get("first_message") or "").strip()).lower()
        if prompt and first and not (first.startswith(prompt[:80]) or prompt.startswith(first[:80])):
            continue
        matched.append((st.st_mtime, path))
    if not matched:
        return None
    matched.sort(key=lambda item: item[0], reverse=True)
    return _cursor_chat_id_from_path(matched[0][1])


def _cursor_spawn_pid_by_session_id():
    out = {}
    for s in _core._spawned_sessions:
        if s.get("engine") != "cursor":
            continue
        sid = (
            s.get("session_id")
            or s.get("resumed_sid")
            or _extract_cursor_chat_id_from_log(s.get("log"))
            or _cursor_session_id_for_spawn_entry(s)
        )
        if sid:
            if not s.get("session_id"):
                s["session_id"] = sid
                _core._update_spawn_session_id_in_registry(s.get("pid"), sid)
            _ensure_cursor_session_visible(sid, spawn_entry=s)
            if sid not in out:
                try:
                    alive = _core._poll_spawn_entry(s) is None
                except Exception:
                    alive = False
                out[sid] = {
                    "pid": s.get("pid"),
                    "alive": alive,
                    "log": s.get("log"),
                    "cwd": s.get("cwd") or "",
                    "repo_path": s.get("repo_path") or "",
                    "spawned_at": s.get("started") or "",
                    "prompt": s.get("prompt") or "",
                    "model": s.get("model") or "",
                    "parent_session_id": s.get("parent_session_id") or "",
                }
    return out


def _get_cursor_app_support_dir():
    import sys
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Cursor"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Cursor"
        return home / "AppData" / "Roaming" / "Cursor"
    else:
        config = os.environ.get("XDG_CONFIG_HOME")
        if config:
            return Path(config) / "Cursor"
        return home / ".config" / "Cursor"


def _find_cursor_workspace_db_and_id(cwd):
    import urllib.parse
    import sys
    import uuid
    app_support_dir = _get_cursor_app_support_dir()
    workspace_storage_dir = app_support_dir / "User" / "workspaceStorage"
    if not workspace_storage_dir.is_dir():
        try:
            workspace_storage_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None, None
        
    try:
        target_path = Path(cwd).expanduser().resolve()
    except Exception:
        target_path = Path(cwd)

    for p in workspace_storage_dir.iterdir():
        if not p.is_dir():
            continue
        ws_json_path = p / "workspace.json"
        if not ws_json_path.is_file():
            continue
        try:
            with open(ws_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            folder_uri = data.get("folder")
            if folder_uri:
                parsed = urllib.parse.urlparse(urllib.parse.unquote(folder_uri))
                path_str = parsed.path
                if sys.platform == "win32" and path_str.startswith("/") and len(path_str) > 2 and path_str[2] == ":":
                    path_str = path_str[1:]
                decoded_path = Path(path_str).resolve(strict=False)
                if decoded_path == target_path:
                    db_path = p / "state.vscdb"
                    return db_path, p.name
        except Exception:
            pass
            
    # Not found, create one so Cursor picks it up
    try:
        new_uuid = uuid.uuid4().hex
        new_p = workspace_storage_dir / new_uuid
        new_p.mkdir(parents=True, exist_ok=True)
        folder_uri = target_path.as_uri()
        with open(new_p / "workspace.json", "w", encoding="utf-8") as f:
            json.dump({"folder": folder_uri}, f)
        db_path = new_p / "state.vscdb"
        return db_path, new_uuid
    except Exception:
        return None, None


def _register_composer_in_workspace_db(db_path, sid, title, created_at, updated_at=None):
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
            row = conn.execute("SELECT value FROM ItemTable WHERE key = 'composer.composerData'").fetchone()
            existing = {}
            row_is_bytes = False
            if row and row[0]:
                val = row[0]
                if isinstance(val, bytes):
                    val = val.decode("utf-8")
                    row_is_bytes = True
                try:
                    existing = json.loads(val)
                except Exception:
                    pass
            
            all_composers = existing.get("allComposers")
            if not isinstance(all_composers, list):
                all_composers = []
            
            found_idx = -1
            for idx, c in enumerate(all_composers):
                if isinstance(c, dict) and c.get("composerId") == sid:
                    found_idx = idx
                    break
            
            composer_entry = {
                "type": "head",
                "composerId": sid,
                "name": title or "New Agent",
                "createdAt": created_at or int(time.time() * 1000),
                "lastUpdatedAt": updated_at or int(time.time() * 1000),
                "unifiedMode": "agent",
                "forceMode": "edit",
                "hasUnreadMessages": False,
                "totalLinesAdded": 0,
                "totalLinesRemoved": 0,
                "hasBlockingPendingActions": False,
                "isArchived": False,
                "isDraft": False,
                "isWorktree": False,
                "isSpec": False,
                "isBestOfNSubcomposer": False,
                "numSubComposers": 0,
                "referencedPlans": []
            }
            
            if found_idx >= 0:
                old_entry = all_composers[found_idx]
                if isinstance(old_entry, dict):
                    composer_entry.update({
                        "name": title or old_entry.get("name") or "New Agent",
                        "createdAt": old_entry.get("createdAt") or created_at or int(time.time() * 1000),
                        "lastUpdatedAt": updated_at or old_entry.get("lastUpdatedAt") or int(time.time() * 1000),
                        "unifiedMode": old_entry.get("unifiedMode") or "agent",
                        "forceMode": old_entry.get("forceMode") or "edit",
                        "isArchived": old_entry.get("isArchived", False),
                    })
                all_composers[found_idx] = composer_entry
            else:
                all_composers.append(composer_entry)
            
            payload = dict(existing)
            payload["allComposers"] = all_composers
            if "selectedComposerIds" not in payload:
                payload["selectedComposerIds"] = [sid]
            if "lastFocusedComposerIds" not in payload:
                payload["lastFocusedComposerIds"] = [sid]
            payload["hasMigratedComposerData"] = True
            payload["hasMigratedMultipleComposers"] = True
            
            new_val_str = json.dumps(payload, ensure_ascii=False)
            conn.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES ('composer.composerData', ?)",
                (new_val_str.encode("utf-8") if row_is_bytes else new_val_str,)
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        print(f"  [cursor-visible] failed to register composer {sid} in workspace db: {e}")
        return False


def _register_composer_in_global_db(global_db_path, workspace_id, sid, title, created_at, updated_at=None):
    try:
        conn = sqlite3.connect(str(global_db_path), timeout=2.0)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
            row = conn.execute("SELECT value FROM ItemTable WHERE key = 'composer.composerHeaders'").fetchone()
            existing_list = []
            row_is_bytes = False
            if row and row[0]:
                val = row[0]
                if isinstance(val, bytes):
                    val = val.decode("utf-8")
                    row_is_bytes = True
                try:
                    existing_list = json.loads(val)
                except Exception:
                    pass
            
            if not isinstance(existing_list, list):
                existing_list = []
            
            found_idx = -1
            for idx, h in enumerate(existing_list):
                if isinstance(h, dict) and h.get("composerId") == sid:
                    found_idx = idx
                    break
            
            header_entry = {
                "type": "head",
                "composerId": sid,
                "name": title or "New Agent",
                "createdAt": created_at or int(time.time() * 1000),
                "lastUpdatedAt": updated_at or int(time.time() * 1000),
                "unifiedMode": "agent",
                "forceMode": "edit",
                "hasUnreadMessages": False,
                "totalLinesAdded": 0,
                "totalLinesRemoved": 0,
                "hasBlockingPendingActions": False,
                "isArchived": False,
                "isDraft": False,
                "isWorktree": False,
                "isSpec": False,
                "isBestOfNSubcomposer": False,
                "numSubComposers": 0,
                "referencedPlans": [],
                "workspaceIdentifier": {"id": workspace_id}
            }
            
            if found_idx >= 0:
                old_entry = existing_list[found_idx]
                if isinstance(old_entry, dict):
                    header_entry.update({
                        "name": title or old_entry.get("name") or "New Agent",
                        "createdAt": old_entry.get("createdAt") or created_at or int(time.time() * 1000),
                        "lastUpdatedAt": updated_at or old_entry.get("lastUpdatedAt") or int(time.time() * 1000),
                        "unifiedMode": old_entry.get("unifiedMode") or "agent",
                        "forceMode": old_entry.get("forceMode") or "edit",
                        "isArchived": old_entry.get("isArchived", False),
                        "workspaceIdentifier": old_entry.get("workspaceIdentifier") or {"id": workspace_id}
                    })
                existing_list[found_idx] = header_entry
            else:
                existing_list.append(header_entry)
            
            new_val_str = json.dumps(existing_list, ensure_ascii=False)
            conn.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES ('composer.composerHeaders', ?)",
                (new_val_str.encode("utf-8") if row_is_bytes else new_val_str,)
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        print(f"  [cursor-visible] failed to register composer {sid} in global db: {e}")
        return False


def _ensure_cursor_session_visible(session_id, spawn_entry=None):
    """Create or refresh Cursor's workspace metadata/store.db for a CLI session."""
    sid = str(session_id or "").strip()
    if not sid or not _core._SESSION_UUID_RE.match(sid):
        return False

    cwd = None
    if spawn_entry:
        cwd = spawn_entry.get("cwd") or spawn_entry.get("repo_path")
    if not cwd:
        path = _core._cursor_transcript_path(sid)
        if path:
            tail = _extract_cursor_tail_meta(path) or {}
            cwd = tail.get("cwd") or _cursor_cwd_from_transcript_path(path)
    if not cwd:
        return False

    try:
        resolved = Path(cwd).expanduser().resolve(strict=False)
    except Exception:
        resolved = Path(str(cwd))
    project_hash = hashlib.md5(str(resolved).encode("utf-8")).hexdigest()
    
    db_dir = Path.home() / ".cursor" / "chats" / project_hash / sid
    db_path = db_dir / "store.db"

    title = None
    if spawn_entry:
        title = spawn_entry.get("name")
    if not title:
        path = _core._cursor_transcript_path(sid)
        if path:
            tail = _extract_cursor_tail_meta(path) or {}
            title = tail.get("first_message") or tail.get("title")
    if not title and spawn_entry:
        title = spawn_entry.get("prompt")
    if not title:
        title = "New Agent"

    if title:
        title = title.split("\n")[0].strip()
        if len(title) > 120:
            title = title[:120] + "..."

    created_at = None
    if spawn_entry:
        started_str = spawn_entry.get("started")
        if started_str:
            try:
                t_struct = time.strptime(started_str, "%Y%m%dT%H%M%S")
                created_at = int(time.mktime(t_struct) * 1000)
            except Exception:
                pass
    if not created_at:
        created_at = int(time.time() * 1000)

    updated_at = None
    try:
        path = _core._cursor_transcript_path(sid)
        if path and path.is_file():
            updated_at = int(path.stat().st_mtime * 1000)
    except OSError:
        pass
    if not updated_at:
        updated_at = created_at


    try:
        db_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=1.0)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS blobs (id TEXT PRIMARY KEY, data BLOB)")
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            
            row = conn.execute("SELECT value FROM meta WHERE key = '0'").fetchone()
            existing = {}
            if row and row[0]:
                try:
                    existing = json.loads(bytes.fromhex(row[0]).decode("utf-8"))
                except Exception:
                    try:
                        existing = json.loads(row[0])
                    except Exception:
                        pass
            
            payload = dict(existing)
            payload.update({
                "agentId": sid,
                "name": title or existing.get("name") or "New Agent",
                "createdAt": created_at or existing.get("createdAt") or int(time.time() * 1000),
                "mode": existing.get("mode") or "default",
                "isRunEverything": existing.get("isRunEverything") if "isRunEverything" in existing else True
            })
            if "latestRootBlobId" not in payload:
                payload["latestRootBlobId"] = ""
                
            json_str = json.dumps(payload, ensure_ascii=False)
            hex_str = json_str.encode("utf-8").hex()
            
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('0', ?)",
                (hex_str,)
            )
            conn.commit()
        finally:
            conn.close()

        # Integrate with Cursor IDE (desktop app) workspace and global databases
        ws_db_path, ws_id = _find_cursor_workspace_db_and_id(cwd)
        if ws_db_path and ws_id:
            _register_composer_in_workspace_db(ws_db_path, sid, title, created_at, updated_at=updated_at)
            global_db_path = _get_cursor_app_support_dir() / "User" / "globalStorage" / "state.vscdb"
            if global_db_path.is_file():
                _register_composer_in_global_db(global_db_path, ws_id, sid, title, created_at, updated_at=updated_at)

        return True
    except Exception as e:
        print(f"  [cursor-visible] failed to make {sid} visible: {e}")
        return False


def backfill_cursor_sidebar_visibility(days=None, repo_paths=None, now=None, max_logs=2000):
    """Create Cursor Agent sidebar metadata/store.db for recent CCC-spawned Cursor sessions."""
    if days is None:
        days = 7
    try:
        now = float(now if now is not None else time.time())
    except (TypeError, ValueError):
        now = time.time()
    cutoff = now - (float(days) * 86400.0)
    ids = []
    seen_ids = set()
    entries_by_sid = {}

    def add_sid(sid, entry=None):
        sid = str(sid or "").strip()
        if not sid or not _core._SESSION_UUID_RE.match(sid):
            return
        if entry and sid not in entries_by_sid:
            entries_by_sid[sid] = entry
        if sid not in seen_ids:
            seen_ids.add(sid)
            ids.append(sid)

    try:
        registry_entries = _core._load_spawn_registry()
    except Exception:
        registry_entries = []
    for entry in registry_entries:
        engine = entry.get("engine")
        if engine != "cursor":
            continue
        entry_epoch = _core._spawn_registry_entry_epoch(entry)
        if entry_epoch and entry_epoch < cutoff:
            continue
        log_path = entry.get("log")
        if log_path and not entry_epoch:
            try:
                if Path(log_path).expanduser().stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
        if not entry_epoch and not log_path:
            continue
        sid = entry.get("session_id") or entry.get("resumed_sid")
        if not sid and log_path:
            sid = _extract_cursor_chat_id_from_log(log_path) or _cursor_session_id_for_spawn_entry(entry)
        if sid:
            add_sid(sid, entry)
            
    # Also discover directly from agent-transcripts directory
    try:
        paths = _cursor_transcript_paths()
        for path in paths:
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                sid = _cursor_chat_id_from_path(path)
                if sid:
                    add_sid(sid)
            except OSError:
                pass
    except Exception:
        pass

    updated = 0
    already_visible = 0
    skipped = 0
    for sid in ids:
        cwd = None
        entry = entries_by_sid.get(sid)
        if entry:
            cwd = entry.get("cwd") or entry.get("repo_path")
        if not cwd:
            path = _core._cursor_transcript_path(sid)
            if path:
                tail = _extract_cursor_tail_meta(path) or {}
                cwd = tail.get("cwd") or _cursor_cwd_from_transcript_path(path)
        if not cwd:
            skipped += 1
            continue

        try:
            resolved = Path(cwd).expanduser().resolve(strict=False)
        except Exception:
            resolved = Path(str(cwd))
        project_hash = hashlib.md5(str(resolved).encode("utf-8")).hexdigest()
        db_path = Path.home() / ".cursor" / "chats" / project_hash / sid / "store.db"
        
        before_exists = db_path.is_file()
        ok = _ensure_cursor_session_visible(sid, spawn_entry=entry)
        if ok:
            if before_exists:
                already_visible += 1
            else:
                updated += 1
        else:
            skipped += 1

    return {
        "ok": True,
        "days": days,
        "found": len(ids),
        "updated": updated,
        "already_visible": already_visible,
        "skipped": skipped,
    }


def _cursor_sidebar_visibility_backfill_once():
    try:
        result = backfill_cursor_sidebar_visibility()
    except Exception as e:
        print(f"  [cursor-sidebar] backfill skipped ({e})")
        return
    if result.get("updated"):
        print(
            "  [cursor-sidebar] marked "
            f"{result['updated']} recent CCC Cursor session(s) as IDE-visible"
        )



def find_cursor_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    paths = _cursor_transcript_paths()
    if not paths:
        return []
    # The same Cursor session id can appear under more than one project dir
    # (e.g. a numeric workspace id AND a path-slug dir each hold an
    # agent-transcripts/<sid>/<sid>.jsonl). Without dedup the list shows the
    # user two identical "Cursor session" rows for one session — and both open
    # the same transcript. Scan newest-first and keep one row per sid (see
    # seen_sids below), mirroring _cursor_transcript_path()'s newest-mtime
    # pick so the list and the opened transcript never disagree.
    try:
        paths = sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass
    repo_path_obj = None
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
    spawn_by_sid = _cursor_spawn_pid_by_session_id()
    git_top_cache = {}
    out = []
    scanned = 0
    seen_sids = set()
    for path in paths:
        if limit and scanned >= int(limit):
            break
        sid = _cursor_chat_id_from_path(path)
        if not sid:
            continue
        # One row per session id, chosen by mtime order alone. The same Cursor
        # session id can appear under more than one project dir — either two
        # copies of one logical session, or (Cursor reuses ids across
        # workspaces) two genuinely distinct conversations that collide on the
        # id. Either way the sidebar/archive must surface a single row. Paths
        # are sorted newest-first, so register the sid here — before the repo
        # filter and the tail parse — so the survivor is identical across every
        # repo view and we never parse a duplicate transcript. Doing the dedup
        # after the repo filter would let two copies that both match a repo
        # (e.g. a pinned sid) each fall through into separate rows.
        if sid in seen_sids:
            continue
        seen_sids.add(sid)
        scanned += 1
        try:
            st = path.stat()
        except OSError:
            continue
        tail = _extract_cursor_tail_meta(path) or {}
        spawn_info = spawn_by_sid.get(sid) or {}
        cwd = tail.get("cwd") or spawn_info.get("cwd") or _cursor_cwd_from_transcript_path(path)
        pinned = repo_pins.get(sid)
        pinned_repo = False
        if repo_only:
            if pinned and pinned != repo_path:
                continue
            if pinned == repo_path:
                pinned_repo = True
            elif not _core._codex_cwd_matches_repo(cwd, repo_path_obj, git_top_cache):
                continue
        modified = tail.get("last_meaningful_ts") or st.st_mtime
        if spawn_info.get("log"):
            try:
                modified = max(modified, Path(spawn_info["log"]).stat().st_mtime)
            except OSError:
                pass
        freshness = max(modified, last_interactions.get(sid) or 0)
        if not include_old and sid not in spawn_by_sid and cutoff > 0 and freshness < cutoff:
            continue
        if not include_old and max_rows > 0 and len(out) >= max_rows:
            continue
        first_message = (tail.get("first_message") or "").strip()
        display_name = (
            name_overrides.get(sid)
            or (first_message[:80] if first_message else None)
            or "Cursor session"
        )
        tail_worktree_path = tail.get("tail_worktree_path") or ""
        effective_cwd = tail_worktree_path or cwd
        try:
            cwd_exists = bool(effective_cwd and Path(effective_cwd).is_dir())
        except OSError:
            cwd_exists = False
        folder_path = pinned or cwd or effective_cwd or ""
        if folder_path:
            _git_root = _core._find_git_root(folder_path)
            folder_label = _core._resolve_dir_case(_git_root or folder_path)
        else:
            folder_label = "Cursor"
        _wt_worktree_label = None
        _wt_idx = folder_label.find("-wt-")
        if _wt_idx > 0:
            _wt_worktree_label = folder_label[_wt_idx + 4:]
            folder_label = folder_label[:_wt_idx]
        spawn_pid = spawn_info.get("pid")
        spawn_alive = bool(spawn_info.get("alive"))
        is_live = spawn_alive
        activity_tail = tail
        cursor_activity = _cursor_activity_fields_from_tail(activity_tail, is_live)
        pending_tool = tail.get("pending_tool") if is_live else None
        pending_file = tail.get("pending_file") if is_live else None
        branch = tail.get("tail_branch") or _core._git_branch_for_cwd(effective_cwd)
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "cursor",
            "engine": "cursor",
            "timestamp": "",
            "branch": branch,
            "git_branch": branch,
            "first_message": first_message[:200],
            "display_name": display_name,
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
            "effective_branch": tail.get("tail_branch") or None,
            "effective_kind": "worktree" if tail_worktree_path else None,
            "has_edit": tail.get("has_edit", False),
            "has_commit": tail.get("has_commit", False),
            "has_push": tail.get("has_push", False),
            "last_edit_pos": tail.get("last_edit_pos", 0),
            "last_commit_pos": tail.get("last_commit_pos", 0),
            "last_push_pos": tail.get("last_push_pos", 0),
            "last_event_type": tail.get("last_event_type"),
            "pending_tool": pending_tool,
            "pending_file": pending_file,
            "last_assistant_text": tail.get("last_assistant_text"),
            "tail_issue_number": None,
            "tail_pr_number": tail.get("tail_pr_number"),
            "tail_pr_url": tail.get("tail_pr_url"),
            "pr_state": None,
            "session_state": _core._parse_session_state(tail.get("last_assistant_text")),
            "archived": sid in archived_set,
            "trashed": sid in trashed_set,
            "verified": sid in verified_set,
            "pinned_repo": pinned_repo,
            "last_interacted": last_interactions.get(sid),
            "is_live": is_live,
            "spawn_pid": spawn_pid,
            "parent_session_id": spawn_info.get("parent_session_id") or "",
            **cursor_activity,
            "needs_approval": False,
            "needs_approval_message": "",
            "model": tail.get("model") or spawn_info.get("model") or "",
            "reasoning_effort": "",
        })
    if resolve_pr_states:
        _core._prime_pr_states(c.get("tail_pr_url") for c in out)
        for c in out:
            if c.get("tail_pr_url"):
                c["pr_state"] = _core._get_pr_state(c["tail_pr_url"])
    out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
    if progress:
        progress(
            "cursor",
            state="done",
            count=len(out),
            total=scanned,
            detail=f"{len(out)} Cursor session card(s) ready.",
        )
    return out


def _parse_cursor_event(ev, line_num, usage_map=None):
    role = _cursor_event_role(ev)
    ts = _cursor_event_timestamp(ev)
    if role == "user":
        text = _cursor_user_text(_cursor_message_text(ev))
        if text:
            return {"line": line_num, "ts": ts, "type": "user_text", "text": text, "images": []}
        return None
    if role == "assistant":
        blocks = []
        for block in _cursor_content_blocks(ev):
            btype = block.get("type")
            if btype == "text":
                text = _cursor_visible_text(block.get("text") or "")
                if text:
                    blocks.append({"kind": "text", "text": text})
            elif btype == "tool_use":
                detail = _cursor_tool_detail(block)
                if isinstance(detail, str) and len(detail) > 1200:
                    detail = detail[:1200] + "..."
                out = {
                    "kind": "tool_use",
                    "name": _cursor_tool_name(block),
                    "detail": detail or "",
                    "id": block.get("id", "") or block.get("tool_use_id", ""),
                }
                command_text = _cursor_tool_command(block)
                if command_text:
                    redacted_command = _core._redacted_shell_command_text(command_text, max_len=12000)
                    if redacted_command and (
                        "\n" in redacted_command
                        or len(redacted_command) > 160
                        or re.sub(r"\s+", " ", redacted_command).strip() != (detail or "")
                    ):
                        out["command"] = redacted_command
                        here = _core._extract_shell_heredoc(command_text)
                        out["command_kind"] = _core._shell_script_label(here.get("head", "")) if here else "Shell command"
                blocks.append(out)
        if blocks:
            return {
                "line": line_num,
                "ts": ts,
                "type": "assistant",
                "message_id": f"cursor-{line_num}",
                "blocks": blocks,
            }
    if role in ("tool", "tool_result"):
        text = _cursor_message_text(ev)
        if text:
            if len(text) > 800:
                text = text[:800] + "\n..."
            return {
                "line": line_num,
                "ts": ts,
                "type": "tool_result",
                "text": text,
                "tool_use_id": ev.get("tool_use_id") or ev.get("id") or "",
                "is_error": bool(ev.get("is_error") or ev.get("error")),
            }
    return None


def _extract_cursor_usage(session_id):
    empty = {
        "latest_input_tokens": 0,
        "peak_input_tokens": 0,
        "total_output_tokens": 0,
        "total_input_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "model": "",
        "context_limit": _core.CURSOR_CONTEXT_LIMIT,
        "cost_usd": 0.0,
        "cost_breakdown_usd": {"input": 0.0, "cache_creation": 0.0,
                               "cache_read": 0.0, "output": 0.0},
        "engine": "cursor",
        "override": _core._get_session_override(session_id),
    }
    path = _core._cursor_transcript_path(session_id)
    tail = _extract_cursor_tail_meta(path) if path else {}
    spawned = _core._spawn_registry_entry_for_session(session_id, "cursor") or {}
    return {**empty, "model": (tail or {}).get("model") or spawned.get("model") or ""}


def _extract_cursor_timeline(session_id):
    path = _core._cursor_transcript_path(session_id)
    if not path:
        return {"events": [], "total_turns": 0}
    events = []
    turn = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _cursor_event_role(ev) != "assistant":
                    continue
                turn += 1
                ts = _cursor_event_timestamp(ev)
                for block in _cursor_content_blocks(ev):
                    if block.get("type") != "tool_use":
                        continue
                    cmd = _cursor_tool_command(block)
                    if not cmd:
                        continue
                    kind = None
                    subject = ""
                    if _core._TIMELINE_PR_CREATE_RE.search(cmd):
                        kind = "pr"
                        m = _core._TIMELINE_PR_TITLE_RE.search(cmd)
                        if m:
                            subject = m.group(1)
                    elif _core._TIMELINE_PUSH_RE.search(cmd):
                        kind = "push"
                    elif _core._TIMELINE_COMMIT_RE.search(cmd):
                        kind = "commit"
                        m = _core._TIMELINE_COMMIT_MSG_RE.search(cmd)
                        if m:
                            subject = m.group(1)
                    if kind:
                        events.append({
                            "kind": kind,
                            "turn": turn,
                            "ts": ts,
                            "subject": subject,
                            "success": None,
                        })
    except OSError:
        return {"events": [], "total_turns": 0}
    return {"events": events, "total_turns": turn}


def _extract_files_from_cursor_conversation(session_id):
    path = _core._cursor_transcript_path(session_id)
    if not path:
        return {"count": 0, "truncated": False, "groups": {}}
    seen = {}
    truncated = False

    def consider(target, kind, line):
        nonlocal truncated
        truncated = _core._ffc_consider_file_target(seen, target, kind, line, truncated)

    def consider_text(text, line):
        if not isinstance(text, str) or not text:
            return
        for target, kind in _core._ffc_iter_targets(text):
            consider(target, kind, line)

    line_num = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_num += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                consider_text(_cursor_message_text(ev), line_num)
                for block in _cursor_content_blocks(ev):
                    if block.get("type") != "tool_use":
                        continue
                    args = _cursor_tool_args(block)
                    for key in ("file_path", "target_file", "path", "notebook_path"):
                        value = args.get(key)
                        if isinstance(value, str) and value.startswith("/"):
                            consider(value, "path", line_num)
                    for raw in _core._ffc_flatten_strings(args):
                        consider_text(raw, line_num)
    except OSError:
        pass

    groups = {}
    for row in seen.values():
        groups.setdefault(row["category"], []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: r["first_line"])
    return {"count": len(seen), "truncated": truncated, "groups": groups}

