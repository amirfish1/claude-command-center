# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Restart recovery: find sessions a machine restart cut off mid-turn.

A reboot (a laptop update, a crash, a preempted cloud VM) SIGTERMs every
`claude` process. The transcript simply stops, so afterwards the session looks
exactly like one that ended its turn. A session counts as cut off by a restart
when:

  * its last user/assistant record leaves the turn open: a tool call with no
    result, a tool result the model never answered, or a prompt with no reply;
  * it went quiet shortly before a known shutdown. A model answers within a
    few minutes and Claude's Bash tool times out at 10, so most open turns
    must be within SHORT_WINDOW_S of the shutdown; only a subagent call can
    legitimately run for hours (LONG_WINDOW_S). Without this check every job
    that died earlier on its own (API error, timeout kill) would be blamed on
    the restart.

Shutdown times come from a heartbeat file the dashboard touches every minute
(works on macOS and Linux; its last touch before a boot is the shutdown) and,
on Linux, from `journalctl --list-boots`. With neither (first boot after
install), the boot time stands in and the result is marked approximate.

The scan runs once per boot and is persisted to disk keyed by boot time, so
the per-request cost is one small JSON read (see the perf gates in CLAUDE.md).

Engines: Claude (~/.claude/projects), Codex (rollouts under
$CODEX_HOME/sessions/YYYY/MM/DD, only the day dirs inside the window are
listed) and the ACP engines CCC drives (Kimi, Devin: CCC's own normalized
transcript cache). Every entry carries `engine`; its `session_id` is the id
CCC's inject endpoint takes for that engine.

Stdlib-only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECTS_ROOT = Path.home() / ".claude" / "projects"
STATE_DIR = Path.home() / ".claude" / "command-center"
CODEX_ROOT = (Path(os.path.expanduser(os.environ["CODEX_HOME"])) if os.environ.get("CODEX_HOME")
              else Path.home() / ".codex") / "sessions"
ACP_ROOT = STATE_DIR / "acp"
# ACP harness -> prefix of the CCC session id (Devin's conversation row is
# `devincli-<slug>` while the transcript file is named by the raw slug).
ACP_ENGINES = {"kimi": "", "devin": "devincli-"}
STATE_FILE = STATE_DIR / "restart-recovery.json"
HEARTBEAT_FILE = STATE_DIR / "restart-recovery-heartbeat"
SHUTDOWNS_FILE = STATE_DIR / "restart-recovery-shutdowns.json"

HEARTBEAT_S = 60
SHORT_WINDOW_S = 12 * 60
LONG_WINDOW_S = 6 * 3600
_LONG_TOOLS = {"Task", "Agent"}
# Without a known shutdown time, how far before the boot to look.
UNKNOWN_WINDOW_S = 24 * 3600
# A process can outlive the last heartbeat/journal line by a little.
SLACK_S = 2 * HEARTBEAT_S
# Shutdowns (and carried-over entries) older than this are ignored.
MAX_AGE_S = 7 * 24 * 3600
TAIL_BYTES = 256 * 1024
HEAD_BYTES = 64 * 1024
_SCHEMA = 3

_LOCK = threading.Lock()
_BOOT_TIME = None
_MEMO = None  # (state_file, boot_time, payload)
_HEARTBEAT_STARTED = False


def boot_time():
    """Unix time of the current OS boot, or None if it can't be read."""
    global _BOOT_TIME
    if _BOOT_TIME is not None:
        return _BOOT_TIME
    value = None
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("btime "):
                    value = float(line.split()[1])
                    break
    except (OSError, ValueError, IndexError):
        pass
    if value is None and sys.platform == "darwin":
        try:
            out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                                 text=True, timeout=5).stdout
            match = re.search(r"sec\s*=\s*(\d+)", out)
            if match:
                value = float(match.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    _BOOT_TIME = value
    return value


def _records(raw):
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith(b"{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            yield rec


def _blocks(rec):
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _is_bookkeeping_user(rec):
    """User records that are not a real prompt waiting for a reply."""
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return True
    for block in _blocks(rec):
        if block.get("type") != "text":
            continue
        text = str(block.get("text") or "").lstrip()
        if text.startswith(("<command-name>", "<local-command", "<command-message>",
                            "[Request interrupted by user", "Caveat:")):
            return True
    return False


def open_turn(records):
    """Describe the unfinished step if the last turn was left open, else None."""
    last = None
    for rec in records:
        if rec.get("isSidechain"):
            continue
        if rec.get("type") == "user" and _is_bookkeeping_user(rec):
            continue
        if rec.get("type") in ("user", "assistant"):
            last = rec
    if last is None:
        return None
    blocks = _blocks(last)
    if last.get("type") == "assistant":
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if not uses:
            # Text with no tool call is a finished (or at worst ambiguous)
            # reply; never guess "interrupted" from it.
            return None
        tool = uses[-1]
        detail = ""
        inp = tool.get("input") if isinstance(tool.get("input"), dict) else {}
        for key in ("description", "command", "file_path", "pattern", "prompt", "url"):
            if inp.get(key):
                detail = str(inp[key])
                break
        return {"kind": "tool_running", "tool": str(tool.get("name") or "tool"),
                "detail": detail[:160], "at": last.get("timestamp")}
    if any(b.get("type") == "tool_result" for b in blocks):
        return {"kind": "awaiting_model", "tool": "", "detail": "", "at": last.get("timestamp")}
    return {"kind": "prompt_unanswered", "tool": "", "detail": "", "at": last.get("timestamp")}


def _read_tail(path, size):
    with open(path, "rb") as fh:
        if size > TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
            raw = fh.read()
            return raw[raw.find(b"\n") + 1:]
        return fh.read()


def _title(path, tail_records):
    for rec in reversed(tail_records):
        if rec.get("type") in ("custom-title", "summary"):
            text = rec.get("customTitle") or rec.get("summary")
            if text:
                return str(text)[:120]
    try:
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES)
    except OSError:
        head = b""
    for rec in _records(head):
        if rec.get("type") == "user" and not rec.get("isSidechain") and not _is_bookkeeping_user(rec):
            for block in _blocks(rec):
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    return " ".join(str(block["text"]).split())[:120]
    for rec in reversed(tail_records):
        if rec.get("type") == "last-prompt" and rec.get("lastPrompt"):
            return " ".join(str(rec["lastPrompt"]).split())[:120]
    return ""


def inspect(path, mtime, size):
    """Return an entry for this transcript if it was left mid-turn, else None."""
    try:
        records = list(_records(_read_tail(path, size)))
    except OSError:
        return None
    step = open_turn(records)
    if step is None:
        return None
    cwd = ""
    for rec in reversed(records):
        if rec.get("cwd"):
            cwd = str(rec["cwd"])
            break
    return {
        "session_id": Path(path).stem,
        "engine": "claude",
        "path": str(path),
        "cwd": cwd,
        "title": _title(path, records),
        "last_activity": mtime,
        "size": size,
        "step": step,
    }


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def record_heartbeat_shutdown(boot, heartbeat_file=None, shutdowns_file=None):
    """At dashboard start: a heartbeat last touched before this boot marks
    when the previous boot went down. Remember it before overwriting."""
    heartbeat_file = Path(heartbeat_file or HEARTBEAT_FILE)
    shutdowns_file = shutdowns_file or SHUTDOWNS_FILE
    try:
        last = heartbeat_file.stat().st_mtime
    except OSError:
        return None
    if boot is None or last >= boot:
        return None
    known = [float(t) for t in _read_json(shutdowns_file, []) if isinstance(t, (int, float))]
    if not any(abs(t - last) < 1 for t in known):
        known = sorted(known + [last])[-50:]
        _write_json(shutdowns_file, known)
    return last


def _heartbeat_loop():
    while True:
        try:
            HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
            HEARTBEAT_FILE.touch()
        except OSError:
            pass
        time.sleep(HEARTBEAT_S)


def start_heartbeat():
    global _HEARTBEAT_STARTED
    if _HEARTBEAT_STARTED:
        return
    _HEARTBEAT_STARTED = True
    # Before the first touch, and before any scan can run.
    record_heartbeat_shutdown(boot_time())
    threading.Thread(target=_heartbeat_loop, daemon=True,
                     name="ccc-restart-heartbeat").start()


def journal_shutdowns():
    """Last-entry time of every previous boot, from systemd's journal."""
    if not sys.platform.startswith("linux"):
        return []
    try:
        out = subprocess.run(["journalctl", "--list-boots", "--no-pager", "-q"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_boot_list(out)


def parse_boot_list(out):
    """Last-entry times from `journalctl --list-boots` output."""
    found = []
    stamp = r"\w{3} (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})( [A-Z]{2,5}| [+-]\d{4})?"
    for line in out.splitlines():
        stamps = re.findall(stamp, line)
        if len(stamps) < 2:
            continue
        text, zone = stamps[-1][0], (stamps[-1][1] or "").strip()
        try:
            when = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if zone in ("UTC", "GMT"):
            found.append(when.replace(tzinfo=timezone.utc).timestamp())
        else:
            found.append(time.mktime(when.timetuple()))
    return found


def known_shutdowns(boot, now, shutdowns_file=None, journal=None):
    times = [float(t) for t in _read_json(shutdowns_file or SHUTDOWNS_FILE, [])
             if isinstance(t, (int, float))]
    times += journal_shutdowns() if journal is None else list(journal)
    out = []
    for t in sorted(times):
        if t < boot and now - t < MAX_AGE_S and not any(abs(t - o) < 5 * 60 for o in out):
            out.append(t)
    return out


def _codex_detail(payload):
    raw = payload.get("arguments") if payload.get("type") == "function_call" else payload.get("input")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            raw = parsed
    if isinstance(raw, dict):
        for key in ("description", "cmd", "command", "path", "prompt"):
            if raw.get(key):
                value = raw[key]
                return " ".join(value) if isinstance(value, list) else str(value)
        return ""
    return " ".join(str(raw or "").split())


def codex_open_turn(records):
    """Codex rollout: the turn is open when the last task_started has no later
    task_complete / turn_aborted. A tail with no task boundary at all is
    treated as closed (never guess)."""
    step = None
    for rec in records:
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        kind, ptype = rec.get("type"), payload.get("type")
        if kind == "event_msg":
            if ptype == "task_started":
                step = {"kind": "prompt_unanswered", "tool": "", "detail": "", "at": rec.get("timestamp")}
            elif ptype in ("task_complete", "turn_aborted"):
                step = None
            continue
        if kind != "response_item" or step is None:
            continue
        if ptype in ("function_call", "custom_tool_call"):
            step = {"kind": "tool_running", "tool": str(payload.get("name") or "tool"),
                    "detail": _codex_detail(payload)[:160], "at": rec.get("timestamp")}
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            step = {"kind": "awaiting_model", "tool": "", "detail": "", "at": rec.get("timestamp")}
        elif ptype == "message" and payload.get("role") == "assistant":
            # Commentary between tool calls; the model still owes the turn end.
            step = {"kind": "awaiting_model", "tool": "", "detail": "", "at": rec.get("timestamp")}
    return step


def acp_open_turn(records):
    """CCC's ACP transcript cache (Kimi, Devin): every prompt (`user_text`)
    ends with a `result` record; a prompt without one was cut off."""
    step = None
    for rec in records:
        kind = rec.get("type")
        if kind == "user_text":
            step = {"kind": "prompt_unanswered", "tool": "", "detail": "", "at": rec.get("ts")}
        elif kind == "result":
            step = None
        elif kind == "assistant" and step is not None:
            tools = [b for b in rec.get("blocks") or [] if isinstance(b, dict) and b.get("kind") == "tool_use"]
            if tools:
                detail = tools[-1].get("detail") or tools[-1].get("title") or ""
                step = {"kind": "tool_running", "tool": str(tools[-1].get("name") or "tool"),
                        "detail": " ".join(str(detail).split())[:160], "at": rec.get("ts")}
            else:
                step = {"kind": "awaiting_model", "tool": "", "detail": "", "at": rec.get("ts")}
    return step


def _codex_inspect(path, mtime, size):
    try:
        records = list(_records(_read_tail(path, size)))
    except OSError:
        return None
    step = codex_open_turn(records)
    if step is None:
        return None
    sid, cwd, title = "", "", ""
    try:
        with open(path, "rb") as fh:
            head = list(_records(fh.read(HEAD_BYTES)))
    except OSError:
        head = []
    for rec in head:
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        if rec.get("type") == "session_meta":
            sid = str(payload.get("id") or payload.get("session_id") or "")
            cwd = str(payload.get("cwd") or "")
        elif rec.get("type") == "event_msg" and payload.get("type") == "user_message" and not title:
            title = " ".join(str(payload.get("message") or "").split())[:120]
        elif (rec.get("type") == "response_item" and payload.get("type") == "message"
              and payload.get("role") == "user" and not title):
            for part in payload.get("content") or []:
                text = str(part.get("text") or "").strip() if isinstance(part, dict) else ""
                if text and not text.startswith(("<", "# AGENTS.md")):  # injected context, not the prompt
                    title = " ".join(text.split())[:120]
                    break
    if not sid:
        match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", str(path))
        sid = match.group(1) if match else ""
    if not _sid_ok(sid):
        return None
    return {"session_id": sid, "engine": "codex", "path": str(path), "cwd": cwd, "title": title,
            "last_activity": mtime, "size": size, "step": step}


def _acp_inspector(engine, prefix):
    def inspect_acp(path, mtime, size):
        try:
            records = list(_records(_read_tail(path, size)))
        except OSError:
            return None
        step = acp_open_turn(records)
        if step is None:
            return None
        sid = prefix + Path(path).stem
        if not _sid_ok(sid):
            return None
        title = ""
        try:
            with open(path, "rb") as fh:
                head = list(_records(fh.read(HEAD_BYTES)))
        except OSError:
            head = []
        for rec in head:
            if rec.get("type") == "user_text" and str(rec.get("text") or "").strip():
                title = " ".join(str(rec["text"]).split())[:120]
                break
        return {"session_id": sid, "engine": engine, "path": str(path), "cwd": "", "title": title,
                "last_activity": mtime, "size": size, "step": step}
    return inspect_acp


def _claude_files(projects_root):
    try:
        project_dirs = list(os.scandir(projects_root))
    except OSError:
        return
    for project in project_dirs:
        if project.is_dir(follow_symlinks=False):
            yield from _jsonl_entries(project.path)


def _jsonl_entries(directory):
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        if entry.name.endswith(".jsonl"):
            yield entry


def _codex_files(codex_root, lower, upper):
    """Only the YYYY/MM/DD dirs that can hold a rollout written in
    [lower, upper): a rollout lives in the dir of the day it started, so
    look back up to LONG_WINDOW_S + a day before `lower` too."""
    seen = set()
    t = lower - 86400
    while t <= upper + 86400:
        for stamp in {time.strftime("%Y/%m/%d", time.localtime(t)),
                      time.strftime("%Y/%m/%d", time.gmtime(t))}:
            if stamp not in seen:
                seen.add(stamp)
                yield from (e for e in _jsonl_entries(os.path.join(codex_root, stamp))
                            if e.name.startswith("rollout-"))
        t += 86400


def _window(step):
    if step.get("kind") == "tool_running" and step.get("tool") in _LONG_TOOLS:
        return LONG_WINDOW_S
    return SHORT_WINDOW_S


def scan(projects_root, boot, shutdowns, codex_root=None, acp_root=None):
    """Transcripts left mid-turn that went quiet just before a shutdown. One
    stat per transcript; tails are read only for those in the window.
    `codex_root` / `acp_root` None skips that engine."""
    approximate = not shutdowns
    cutoffs = shutdowns or [boot]
    lower = min(cutoffs) - (UNKNOWN_WINDOW_S if approximate else LONG_WINDOW_S)
    sources = [(_claude_files(projects_root), inspect)]
    if codex_root is not None:
        sources.append((_codex_files(codex_root, lower, boot), _codex_inspect))
    if acp_root is not None:
        for engine, prefix in ACP_ENGINES.items():
            sources.append((_jsonl_entries(os.path.join(acp_root, engine)), _acp_inspector(engine, prefix)))
    found = []
    for entries, inspector in sources:
        for entry in entries:
            try:
                st = entry.stat()
            except OSError:
                continue
            if not (lower <= st.st_mtime < boot):
                continue
            item = inspector(entry.path, st.st_mtime, st.st_size)
            if not item or not item.get("session_id"):
                continue
            window = UNKNOWN_WINDOW_S if approximate else _window(item["step"])
            # The first shutdown at or after the last write is the one that
            # stopped it (if it was still running then).
            after = [t for t in cutoffs if t + SLACK_S >= st.st_mtime]
            if not after or after[0] - st.st_mtime > window:
                continue
            item["shutdown_at"] = after[0]
            item["approximate"] = approximate
            found.append(item)
    found.sort(key=lambda e: e["last_activity"], reverse=True)
    return found


def _untouched(entry):
    try:
        st = os.stat(entry["path"])
    except (OSError, KeyError):
        return False
    return st.st_mtime == entry.get("last_activity") and st.st_size == entry.get("size")


def state(projects_root=None, state_file=None, boot=None, now=None, shutdowns=None,
          codex_root=None, acp_root=None):
    """Current list, scanning only when the boot changed since the last scan."""
    global _MEMO
    projects_root = projects_root or PROJECTS_ROOT
    codex_root = codex_root or CODEX_ROOT
    acp_root = acp_root or ACP_ROOT
    state_file = state_file or STATE_FILE
    boot = boot if boot is not None else boot_time()
    now = now if now is not None else time.time()
    if boot is None:
        return {"boot_time": None, "sessions": []}
    with _LOCK:
        if _MEMO and _MEMO[0] == str(state_file) and _MEMO[1] == boot:
            data = _MEMO[2]
        else:
            data = _read_json(state_file, {})
            if not isinstance(data, dict) or data.get("schema") != _SCHEMA:
                data = {}
            if data.get("boot_time") != boot:
                if shutdowns is None:
                    shutdowns = known_shutdowns(boot, now)
                dismissed = set(data.get("dismissed") or [])
                fresh = scan(projects_root, boot, shutdowns, codex_root, acp_root)
                seen = {e["session_id"] for e in fresh}
                # Entries from an earlier boot that nobody dismissed or resumed.
                carried = [e for e in data.get("sessions") or []
                           if e.get("session_id") not in seen
                           and e.get("session_id") not in dismissed
                           and now - float(e.get("last_activity") or 0) < MAX_AGE_S
                           and _untouched(e)]
                data = {"schema": _SCHEMA, "boot_time": boot, "scanned_at": now,
                        "shutdowns": shutdowns, "sessions": fresh + carried,
                        "dismissed": sorted(dismissed & {e["session_id"] for e in carried})}
                _write_json(state_file, data)
            _MEMO = (str(state_file), boot, data)
        dismissed = set(data.get("dismissed") or [])
        # A session that was resumed (written since) is no longer cut off.
        live = [dict(e) for e in data.get("sessions") or []
                if e["session_id"] not in dismissed and _untouched(e)]
    for e in live:
        e.pop("path", None)
        e.pop("size", None)
    return {"boot_time": boot, "sessions": live}


def dismiss(session_ids=None, state_file=None, dismiss_all=False):
    global _MEMO
    state_file = state_file or STATE_FILE
    with _LOCK:
        data = _read_json(state_file, {})
        if not isinstance(data, dict) or not data:
            return 0
        current = {e.get("session_id") for e in data.get("sessions") or []}
        targets = current if dismiss_all else current & set(session_ids or [])
        dismissed = set(data.get("dismissed") or []) | targets
        data["dismissed"] = sorted(dismissed)
        _write_json(state_file, data)
        _MEMO = None
    return len(targets)


def resume_prompt(entry_step, shutdown_at):
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(shutdown_at)) if shutdown_at else "recently"
    step = entry_step or {}
    if step.get("kind") == "tool_running":
        what = "while your %s call was running" % (step.get("tool") or "tool")
        if step.get("detail"):
            what += " (%s)" % step["detail"]
        what += "; it may not have finished or its result is lost"
    elif step.get("kind") == "awaiting_model":
        what = "right after a tool returned, before you replied"
    else:
        what = "before you answered the last message"
    return ("CCC note: the machine restarted at %s and stopped this session mid-turn, %s. "
            "Check the current state of what you were doing (files, processes, partial "
            "writes) before redoing anything, then continue the task." % (when, what))


def _sid_ok(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value) is not None


def handle_api_get(handler):
    handler.send_json({"ok": True, **state()})


def handle_api_post(handler, path):
    try:
        length = int(handler.headers.get("Content-Length", "0") or 0)
        payload = json.loads(handler.rfile.read(length) if 0 < length <= 65536 else b"{}")
    except (ValueError, OSError):
        payload = None
    if not isinstance(payload, dict):
        handler.send_json({"ok": False, "error": "Expected a JSON object."}, 400)
        return True
    if path == "/api/restart-recovery/dismiss":
        if payload.get("all") is True:
            handler.send_json({"ok": True, "dismissed": dismiss(dismiss_all=True)})
            return True
        sid = payload.get("session_id")
        if not _sid_ok(sid):
            handler.send_json({"ok": False, "error": 'Expected {"session_id"} or {"all": true}.'}, 400)
            return True
        handler.send_json({"ok": True, "dismissed": dismiss([sid])})
        return True
    if path == "/api/restart-recovery/prompt":
        sid = payload.get("session_id")
        current = state()
        entry = next((e for e in current["sessions"] if e["session_id"] == sid), None)
        if entry is None:
            handler.send_json({"ok": False, "error": "Not an interrupted session."}, 404)
            return True
        handler.send_json({"ok": True, "text": resume_prompt(entry.get("step"),
                                                             entry.get("shutdown_at"))})
        return True
    return False
