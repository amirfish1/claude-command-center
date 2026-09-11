"""CCC-owned, durable Aider sessions.

Aider's default project ``.aider.chat.history.md`` is intentionally not read:
it has no durable session identity. CCC instead assigns a UUID before each
``aider --message`` run and stores the normalized transcript itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from ccc_server import core as _core


_AIDER_OUTPUT_LIMIT = 24_000
_AIDER_SESSION_LOCKS = {}
_AIDER_SESSION_LOCKS_GUARD = threading.Lock()


def _aider_session_lock(session_id):
    with _AIDER_SESSION_LOCKS_GUARD:
        return _AIDER_SESSION_LOCKS.setdefault(session_id, threading.Lock())


def _aider_sessions_dir():
    return _core.AIDER_SESSIONS_DIR


def _aider_session_path(session_id):
    try:
        sid = str(uuid.UUID(str(session_id)))
    except (ValueError, TypeError, AttributeError):
        return None
    return _aider_sessions_dir() / f"{sid}.jsonl"


def _resolve_aider_bin():
    env_bin = os.environ.get("CCC_AIDER_BIN")
    if env_bin:
        if os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
            return {"available": True, "bin": env_bin, "source": "env"}
        return {
            "available": False, "bin": None, "code": "aider_unavailable",
            "reason": f"CCC_AIDER_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("aider")
    if which_bin:
        return {"available": True, "bin": which_bin, "source": "path"}
    return {
        "available": False, "bin": None, "code": "aider_unavailable",
        "reason": "Aider CLI not found. Install Aider or set CCC_AIDER_BIN.",
    }


def _aider_iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _aider_read_records(path):
    records = []
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        pass
    return records


def _aider_metadata(path):
    for record in _aider_read_records(path)[:4]:
        if record.get("type") == "aider_session_meta":
            return record
    return {}


def _aider_append(path, record):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _aider_user_event(session_id, text):
    return {
        "type": "user", "sessionId": session_id, "timestamp": _aider_iso_now(),
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _aider_assistant_event(session_id, text, model=""):
    message = {
        "id": f"aider-{uuid.uuid4()}", "role": "assistant",
        "content": [{"type": "text", "text": text}],
    }
    if model:
        message["model"] = model
    return {
        "type": "assistant", "sessionId": session_id, "timestamp": _aider_iso_now(),
        "message": message,
    }


def _aider_result_event(session_id, exit_code):
    return {
        "type": "result", "sessionId": session_id, "timestamp": _aider_iso_now(),
        "subtype": "success" if exit_code == 0 else "error",
        "duration_ms": None,
    }


def _aider_finish_turn(entry, transcript_path):
    proc = entry.get("proc")
    try:
        exit_code = proc.wait() if proc is not None else -1
    except Exception:
        exit_code = -1
    _core._cleanup_finished_entry(entry)
    try:
        output = Path(entry.get("log") or "").read_text(
            encoding="utf-8", errors="replace"
        ).strip()
    except OSError:
        output = ""
    if len(output) > _AIDER_OUTPUT_LIMIT:
        output = output[:_AIDER_OUTPUT_LIMIT] + "\n… output truncated by CCC"
    if not output:
        output = (
            "Aider completed without text output."
            if exit_code == 0 else f"Aider exited with code {exit_code}."
        )
    session_id = entry["session_id"]
    with _aider_session_lock(session_id):
        _aider_append(transcript_path, _aider_assistant_event(
            session_id, output, entry.get("model") or "",
        ))
        _aider_append(transcript_path, _aider_result_event(session_id, exit_code))


def _aider_start_turn(session_id, text, *, cwd, repo_path, name, model="", parent_session_id=None, env=None):
    resolved = _resolve_aider_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    transcript_path = _aider_session_path(session_id)
    if transcript_path is None:
        return {"ok": False, "error": "invalid Aider session id"}
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_dir = _core.repo_log_dir(repo_path)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"aider-{session_id[:8]}-{timestamp}.log"
    cmd = [resolved["bin"], "--message", text, "--yes-always", "--no-pretty"]
    if model:
        cmd.extend(["--model", model])
    log_fh = open(log_path, "w", encoding="utf-8")
    # `env`, when given, is merged over the inherited process environment --
    # used by the BYOK layer (ccc_server/byok.py) to hand Aider a provider
    # API key for this one spawn without touching global state.
    popen_env = {**os.environ, **env} if env else None
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
            cwd=cwd, start_new_session=True, env=popen_env,
        )
    except (FileNotFoundError, OSError) as exc:
        log_fh.close()
        return {"ok": False, "error": str(exc), "code": "aider_launch_failed", "via": "aider"}
    entry = {
        "pid": proc.pid, "name": name, "log": str(log_path), "prompt": text[:200],
        "started": timestamp, "proc": proc, "log_fh": log_fh, "fifo": None,
        "stdin_fd": None, "engine": "aider", "session_id": session_id,
        "cwd": cwd, "repo_path": repo_path, "model": model or "",
        "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid, name=name, log_path=log_path, cwd=cwd, spawned_at=timestamp,
        command_summary=text[:200], fifo=None, engine="aider", session_id=session_id,
        repo_path=repo_path, model=model, parent_session_id=parent_session_id,
    )
    threading.Thread(
        target=_aider_finish_turn, args=(entry, transcript_path), daemon=True,
        name=f"ccc-aider-{session_id[:8]}",
    ).start()
    return _core._finalize_spawn_response(
        {"ok": True, "pid": proc.pid, "log": str(log_path), "via": "aider"}, entry,
        {"cwd": cwd, "repo_path": repo_path}, wait_for_session_id=False,
    )


def spawn_session_aider(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None, env=None):
    """Start an Aider one-shot run with a CCC-owned durable session UUID.

    ``env``, when given, is merged over the inherited process environment --
    used by the BYOK layer to hand this one spawn a provider API key.
    """
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    if not prompt:
        return {"ok": False, "error": "missing prompt"}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    session_name = _core._slugify(name or prompt) or "aider"
    worktree_path = worktree_branch = None
    if worktree:
        try:
            worktree_path, worktree_branch = _core._create_worktree_for_spawn(spawn_cwd, session_name)
            spawn_cwd = worktree_path
        except RuntimeError as exc:
            return {"ok": False, "error": f"worktree creation failed: {exc}"}
    session_id = str(uuid.uuid4())
    transcript_path = _aider_session_path(session_id)
    model_to_use = _core._spawn_model_for_engine("aider", model)
    with _aider_session_lock(session_id):
        if not _aider_append(transcript_path, {
            "type": "aider_session_meta", "session_id": session_id,
            "engine": "aider", "cwd": spawn_cwd, "repo_path": ctx["repo_path"],
            "name": session_name, "model": model_to_use or "", "created_at": _aider_iso_now(),
            "parent_session_id": parent_session_id or "",
        }):
            return {"ok": False, "error": "could not create Aider session transcript"}
        _aider_append(transcript_path, _aider_user_event(session_id, prompt))
    result = _aider_start_turn(
        session_id, prompt, cwd=spawn_cwd, repo_path=ctx["repo_path"], name=session_name,
        model=model_to_use, parent_session_id=parent_session_id, env=env,
    )
    if result.get("ok") and worktree_path:
        result["worktree_path"] = worktree_path
        result["worktree_branch"] = worktree_branch
    return result


def _is_aider_session(session_id):
    return _aider_session_path(session_id) is not None and _aider_session_path(session_id).is_file()


def _aider_session_cwd(session_id):
    return _aider_metadata(_aider_session_path(session_id) or "").get("cwd") or None


def _aider_rows(repo_path=None, include_old=True, repo_only=True, limit=None):
    try:
        paths = sorted(_aider_sessions_dir().glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    if limit:
        paths = paths[:limit]
    if repo_only:
        repo_path = _core.resolve_repo_path(repo_path)
    cutoff = _core._session_scan_cutoff_ts(include_old)
    rows = []
    for path in paths:
        meta = _aider_metadata(path)
        sid = meta.get("session_id")
        if not sid:
            continue
        cwd = meta.get("cwd") or ""
        if repo_only and cwd and not _core._codex_cwd_matches_repo(cwd, Path(repo_path), {}):
            continue
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if not include_old and cutoff and modified < cutoff:
            continue
        events = _parse_aider_conversation(sid).get("events") or []
        first = next((e.get("text", "") for e in events if e.get("type") == "user_text"), "")
        assistants = [e for e in events if e.get("type") == "assistant"]
        last = ""
        if assistants:
            last = "\n".join(b.get("text", "") for b in assistants[-1].get("blocks", []) if b.get("kind") == "text")
        rows.append({
            "id": sid, "session_id": sid, "source": "aider", "engine": "aider",
            "timestamp": "", "branch": "", "git_branch": "", "first_message": first[:200],
            "display_name": meta.get("name") or first[:80] or "Aider session", "ai_title": None,
            "name_overridden": False, "last_prompt": first[:200], "size": path.stat().st_size,
            "modified": modified, "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)),
            "mtime": modified, "jsonl_path": str(path), "folder_label": _core._resolve_dir_case(cwd) if cwd else "Aider",
            "folder_path": cwd, "worktree_label": None, "session_cwd": cwd,
            "session_cwd_exists": bool(cwd and Path(cwd).is_dir()), "session_cwd_is_worktree": bool(cwd and (Path(cwd) / ".git").is_file()),
            "worktree_dirty": False, "effective_branch": None, "effective_kind": None,
            "has_edit": False, "has_commit": False, "has_push": False, "last_edit_pos": 0,
            "last_commit_pos": 0, "last_push_pos": 0, "last_event_type": "result" if assistants else "user",
            "pending_tool": None, "pending_file": None, "pending_tool_ts": 0,
            "last_assistant_text": last, "tail_issue_number": None, "tail_pr_number": None,
            "tail_pr_url": None, "pr_state": None, "session_state": _core._parse_session_state(last),
            "archived": False, "trashed": False, "verified": False, "pinned_repo": False,
            "last_interacted": None, "is_live": any(s.get("engine") == "aider" and s.get("session_id") == sid and _core._poll_spawn_entry(s) is None for s in _core._spawned_sessions),
            "spawn_pid": None, "parent_session_id": meta.get("parent_session_id") or "", "needs_approval": False,
            "needs_approval_message": "", "model": meta.get("model") or "", "reasoning_effort": "",
        })
    return rows


def find_aider_conversations(repo_path=None, include_old=True, repo_only=True, progress=None, limit=None, resolve_pr_states=True, resolve_worktree_dirty=True):
    rows = _aider_rows(repo_path=repo_path, include_old=include_old, repo_only=repo_only, limit=limit)
    if progress:
        progress("aider", state="done", count=len(rows), total=len(rows), detail=f"{len(rows)} Aider session(s) ready.")
    return rows


def _parse_aider_conversation(session_id, after_line=0):
    path = _aider_session_path(session_id)
    if path is None or not path.is_file():
        return {"events": [], "last_line": 0}
    events = []
    line = 0
    for record in _aider_read_records(path):
        if record.get("type") == "aider_session_meta":
            continue
        line += 1
        parsed = _core._parse_conversation_event(record, line)
        if parsed:
            events.append(parsed)
    return {"events": [e for e in events if e["line"] > after_line], "last_line": line}


def resume_session_aider(session_id, text):
    """Append a new durable turn and run `aider --message` in the same session."""
    path = _aider_session_path(session_id)
    meta = _aider_metadata(path or "")
    text = _core._strip_ccc_session_state_instruction(text)
    if path is None or not path.is_file() or not meta:
        return {"ok": False, "error": "unknown Aider session"}
    if not text:
        return {"ok": False, "error": "missing session_id or text"}
    cwd = meta.get("cwd") or str(Path.home())
    if not Path(cwd).is_dir():
        cwd = str(Path.home())
    repo_path = meta.get("repo_path") or _core._git_toplevel_for_existing_dir(cwd) or cwd
    with _aider_session_lock(session_id):
        _aider_append(path, _aider_user_event(session_id, text))
    result = _aider_start_turn(
        session_id, text, cwd=cwd, repo_path=repo_path,
        name=meta.get("name") or f"resume-aider-{session_id[:8]}",
        model=(meta.get("model") or ""), parent_session_id=meta.get("parent_session_id") or "",
    )
    if result.get("ok"):
        result["resumed"] = True
    return result
