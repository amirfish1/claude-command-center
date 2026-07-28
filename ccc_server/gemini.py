# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 34332-35851).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from ccc_server.antigravity import (
    _antigravity_app_conversation_path,
    _antigravity_cli_conversation_path,
    _antigravity_transcript_path,
    _antigravity_transcript_paths,
    _is_antigravity_session,
    _is_kilo_session,
)
from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Gemini CLI integration
# ---------------------------------------------------------------------------

ANTIGRAVITY_HOME = _core.GEMINI_HOME / "antigravity"
ANTIGRAVITY_BRAIN = ANTIGRAVITY_HOME / "brain"
ANTIGRAVITY_CONVERSATIONS = ANTIGRAVITY_HOME / "conversations"
ANTIGRAVITY_CLI_HOME = _core.GEMINI_HOME / "antigravity-cli"
ANTIGRAVITY_CLI_SETTINGS = ANTIGRAVITY_CLI_HOME / "settings.json"
ANTIGRAVITY_CLI_BRAIN = ANTIGRAVITY_CLI_HOME / "brain"
ANTIGRAVITY_CLI_CONVERSATIONS = ANTIGRAVITY_CLI_HOME / "conversations"
ANTIGRAVITY_MAIN_LOG = Path.home() / "Library" / "Logs" / "Antigravity" / "main.log"
ANTIGRAVITY_APP_LS_SERVICE = "exa.language_server_pb.LanguageServerService"
# AgyHub stores conversation summaries (incl. the AI-generated title) here
# as a stream of length-prefixed protobuf messages. Each top-level record is
# `{ string uuid; SummaryDetail detail; }` and SummaryDetail begins with
# `string title;` — see _load_antigravity_summary_titles below.
ANTIGRAVITY_SUMMARIES_PROTO = ANTIGRAVITY_HOME / "agyhub_summaries_proto.pb"
GEMINI_CONTEXT_LIMIT = 1_000_000
CURSOR_HOME = Path.home() / ".cursor"
CURSOR_PROJECTS_ROOT = CURSOR_HOME / "projects"
CURSOR_LOCAL_BIN = Path.home() / ".local" / "bin" / "cursor-agent"
CURSOR_CONTEXT_LIMIT = 0
_CURSOR_META_VERSION = 3
CURSOR_APP_BUNDLE_CANDIDATES = (
    Path("/Applications/Cursor.app/Contents/Resources/app/bin/cursor-agent"),
    Path("/Applications/Cursor.app/Contents/Resources/cursor-agent"),
    Path.home() / "Applications" / "Cursor.app" / "Contents" / "Resources" / "app" / "bin" / "cursor-agent",
    Path.home() / "Applications" / "Cursor.app" / "Contents" / "Resources" / "cursor-agent",
)


_antigravity_summary_cache = {"mtime": 0, "titles": {}}
_antigravity_cli_settings_lock = threading.Lock()


_ANTIGRAVITY_MODEL_LABELS = {
    "gemini-3-5-pro-high": "Gemini 3.5 Pro (High)",
    "gemini-3-5-pro-medium": "Gemini 3.5 Pro (Medium)",
    "gemini-3-5-pro-low": "Gemini 3.5 Pro (Low)",
    "gemini-3-5-pro": "Gemini 3.5 Pro (High)",
    "gemini-3-5-flash-high": "Gemini 3.5 Flash (High)",
    "gemini-3-5-flash-medium": "Gemini 3.5 Flash (Medium)",
    "gemini-3-1-pro-high": "Gemini 3.1 Pro (High)",
    "gemini-3-1-pro-low": "Gemini 3.1 Pro (Low)",
    "gemini-3-1-pro": "Gemini 3.1 Pro (High)",
    "claude-sonnet-4-6-thinking": "Claude Sonnet 4.6 (Thinking)",
    "claude-opus-4-6-thinking": "Claude Opus 4.6 (Thinking)",
    "gpt-oss-120b-medium": "GPT-OSS 120B (Medium)",
}

_ANTIGRAVITY_EMBEDDED_MESSAGE_RE = re.compile(
    r"^\[Message\]\s+timestamp=(?P<timestamp>\S+)\s+sender=(?P<sender>\S+)\s+"
    r"priority=(?P<priority>\S+)\s+content=(?P<content>.*)\s*$",
    re.S,
)


def _antigravity_read_varint(buf, pos):
    val = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7f) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
        if shift >= 64:
            break
    return val, pos


def _load_antigravity_summary_titles():
    """Return {session_id: ai-generated title} from agyhub_summaries_proto.pb.

    Antigravity (Gemini's desktop coding agent) writes a per-session title
    into a protobuf summaries file. The schema isn't public, but the wire
    format is stable enough to walk by hand:

      record         := 0x0A <varint-len> <record-bytes>
      record-bytes   := 0x0A <uuid-len> <uuid> 0x12 <varint-len> <submsg>
      submsg         := 0x0A <title-len> <title-utf8> [other fields...]

    Cached by file mtime so the cost is a single parse per change.
    """
    try:
        mtime = _core.ANTIGRAVITY_SUMMARIES_PROTO.stat().st_mtime
    except OSError:
        return {}
    if _antigravity_summary_cache["mtime"] == mtime:
        return _antigravity_summary_cache["titles"]
    try:
        data = _core.ANTIGRAVITY_SUMMARIES_PROTO.read_bytes()
    except OSError:
        return _antigravity_summary_cache["titles"]
    titles = {}
    pos = 0
    while pos < len(data):
        # Top-level record opens with field 1, wire-type 2 (length-delimited).
        if data[pos] != 0x0A:
            pos += 1
            continue
        pos += 1
        rec_len, pos = _antigravity_read_varint(data, pos)
        record = data[pos:pos + rec_len]
        pos += rec_len
        rp = 0
        sid = None
        submsg = None
        while rp < len(record):
            tag = record[rp]
            rp += 1
            if tag == 0x0A:  # field 1: UUID string
                ulen, rp = _antigravity_read_varint(record, rp)
                sid = record[rp:rp + ulen].decode("utf-8", "replace")
                rp += ulen
            elif tag == 0x12:  # field 2: SummaryDetail submessage
                slen, rp = _antigravity_read_varint(record, rp)
                submsg = record[rp:rp + slen]
                rp += slen
            else:
                # Skip unknown wire types defensively; we only need fields 1 & 2.
                break
        if not sid or not submsg or submsg[0] != 0x0A:
            continue
        tlen, tp = _antigravity_read_varint(submsg, 1)
        title = submsg[tp:tp + tlen].decode("utf-8", "replace").strip()
        if title:
            titles[sid] = title
    _antigravity_summary_cache["mtime"] = mtime
    _antigravity_summary_cache["titles"] = titles
    return titles


def _resolve_gemini_bin():
    """Locate a usable Gemini CLI binary.

    Priority order mirrors Codex:
      1. $CCC_GEMINI_BIN when set and executable.
      2. `shutil.which("gemini")`.
      3. Common user-install locations launchd often omits from PATH.
    """
    env_bin = os.environ.get("CCC_GEMINI_BIN")
    if env_bin:
        expanded = os.path.expanduser(env_bin)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return {"available": True, "bin": expanded, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "code": "gemini_unavailable",
            "reason": f"CCC_GEMINI_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("gemini")
    if which_bin:
        return {"available": True, "bin": which_bin, "source": "path"}
    for candidate in _core._iter_common_cli_candidates("gemini"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return {"available": True, "bin": str(candidate), "source": "candidate"}
    return {
        "available": False,
        "bin": None,
        "code": "gemini_unavailable",
        "reason": "Gemini CLI not found. Install Gemini CLI or set CCC_GEMINI_BIN.",
    }


def _resolve_antigravity_bin():
    """Locate the Antigravity AGY CLI binary.

    Official installs place the executable at ~/.local/bin/agy. Try the
    environment override first so users can point CCC at pre-release or
    nonstandard installs without relying on launchd's PATH.
    """
    env_bin = os.environ.get("CCC_ANTIGRAVITY_BIN")
    if env_bin:
        expanded = os.path.expanduser(env_bin)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return {"available": True, "bin": expanded, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "code": "antigravity_unavailable",
            "reason": f"CCC_ANTIGRAVITY_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    for cmd in ("agy", "antigravity"):
        which_bin = shutil.which(cmd)
        if which_bin:
            return {"available": True, "bin": which_bin, "source": "path", "command": cmd}
        for candidate in _core._iter_common_cli_candidates(cmd):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return {"available": True, "bin": str(candidate), "source": "candidate", "command": cmd}
    return {
        "available": False,
        "bin": None,
        "code": "antigravity_unavailable",
        "reason": "Antigravity CLI not found. Install AGY CLI or set CCC_ANTIGRAVITY_BIN.",
    }


def _resolve_cursor_bin():
    """Locate a usable Cursor Agent binary.

    Priority order:
      1. $CCC_CURSOR_BIN when set and executable.
      2. `shutil.which("cursor-agent")`.
      3. ~/.local/bin/cursor-agent.
      4. Known Cursor.app bundle locations, when present.
    """
    env_bin = os.environ.get("CCC_CURSOR_BIN")
    if env_bin:
        expanded = os.path.expanduser(env_bin)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return {"available": True, "bin": expanded, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "code": "cursor_unavailable",
            "reason": f"CCC_CURSOR_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("cursor-agent")
    if which_bin:
        return {"available": True, "bin": which_bin, "source": "path"}
    if _core.CURSOR_LOCAL_BIN.is_file() and os.access(_core.CURSOR_LOCAL_BIN, os.X_OK):
        return {"available": True, "bin": str(_core.CURSOR_LOCAL_BIN), "source": "candidate"}
    for candidate in _core.CURSOR_APP_BUNDLE_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return {"available": True, "bin": str(candidate), "source": "bundle"}
    return {
        "available": False,
        "bin": None,
        "code": "cursor_unavailable",
        "reason": "Cursor Agent CLI not found. Install Cursor, install cursor-agent, or set CCC_CURSOR_BIN.",
    }


def _resolve_kilo_bin():
    """Locate a usable Kilo Code CLI binary.

    Priority order:
      1. $CCC_KILO_BIN (env override) — if set and executable.
      2. `shutil.which("kilo")` — picks up Homebrew / npm-global.

    Returns a dict so the caller and the availability endpoint can share
    one shape:
      {available: True,  bin: "<abs path>", source: "env|path"}
      {available: False, reason: "<human readable>", bin: None}
    """
    env_bin = os.environ.get("CCC_KILO_BIN")
    if env_bin:
        if os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
            return {"available": True, "bin": env_bin, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "code": "kilo_unavailable",
            "reason": f"CCC_KILO_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("kilo")
    if which_bin:
        return {"available": True, "bin": which_bin, "source": "path"}
    return {
        "available": False,
        "bin": None,
        "code": "kilo_unavailable",
        "reason": (
            "Kilo Code CLI not found. Install Kilo Code, "
            "`npm install -g @kilocode/cli`, or set CCC_KILO_BIN."
        ),
    }


def _antigravity_command_words(resolved):
    words = [resolved["bin"]]
    raw_args = (os.environ.get("CCC_ANTIGRAVITY_ARGS") or "").strip()
    if raw_args:
        try:
            words.extend(shlex.split(raw_args))
        except ValueError:
            pass
    return words


def _antigravity_shell_command(resolved):
    return " ".join(_core._shell_quote(word) for word in _antigravity_command_words(resolved))


def _antigravity_model_settings_label(model):
    text = str(model or "").strip()
    if not text:
        return ""
    key = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return _ANTIGRAVITY_MODEL_LABELS.get(key, text)


def _read_antigravity_cli_settings():
    try:
        with open(_core.ANTIGRAVITY_CLI_SETTINGS, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}, None
    except json.JSONDecodeError as exc:
        return {}, f"Antigravity CLI settings.json is not valid JSON: {exc}"
    except OSError as exc:
        return {}, f"Could not read Antigravity CLI settings.json: {exc}"
    if not isinstance(data, dict):
        return {}, "Antigravity CLI settings.json must contain a JSON object"
    return data, None


def _write_antigravity_cli_settings(settings):
    try:
        _core.ANTIGRAVITY_CLI_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        tmp = _core.ANTIGRAVITY_CLI_SETTINGS.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, _core.ANTIGRAVITY_CLI_SETTINGS)
    except OSError as exc:
        return f"Could not write Antigravity CLI settings.json: {exc}"
    return None


def _antigravity_cli_configured_model():
    settings, error = _read_antigravity_cli_settings()
    if error:
        return ""
    return str(settings.get("model") or "").strip()


def _set_antigravity_cli_model(model):
    label = _antigravity_model_settings_label(model)
    if not label:
        return {"ok": True, "model": ""}
    with _antigravity_cli_settings_lock:
        settings, error = _read_antigravity_cli_settings()
        if error:
            return {"ok": False, "error": error, "code": "antigravity_settings_invalid"}
        settings["model"] = label
        error = _write_antigravity_cli_settings(settings)
        if error:
            return {"ok": False, "error": error, "code": "antigravity_settings_write_failed"}
    return {"ok": True, "model": label}


def _antigravity_has_arg(words, *names):
    for word in (words or []):
        text = str(word)
        if text in names:
            return True
        if any(text.startswith(name + "=") for name in names):
            return True
    return False


_ANTIGRAVITY_PRINT_TIMEOUT_DEFAULT = "2h"


def _antigravity_print_timeout():
    """Go-duration timeout passed to every `agy -p` run via --print-timeout.

    The AGY CLI's print mode gives up after --print-timeout (CLI default 5m)
    and exits with "Error: timeout waiting for response", killing healthy
    spawns mid-task (OPS-84). Override with $CCC_ANTIGRAVITY_PRINT_TIMEOUT;
    values that don't parse as a Go duration fall back to the default.
    """
    value = (os.environ.get("CCC_ANTIGRAVITY_PRINT_TIMEOUT") or "").strip()
    if value and _parse_go_duration_seconds(value) is not None:
        return value
    return _ANTIGRAVITY_PRINT_TIMEOUT_DEFAULT


def _parse_go_duration_seconds(text):
    """Parse a Go duration string ("2h", "5m0s", "90s") to seconds, else None."""
    text = str(text or "").strip()
    if not text or not re.fullmatch(r"(\d+(?:\.\d+)?(?:ms|h|m|s))+", text):
        return None
    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", text):
        total += float(amount) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total


def _antigravity_print_timeout_seconds():
    return _parse_go_duration_seconds(_antigravity_print_timeout()) or 2 * 3600


def _antigravity_arg_values(words, name):
    values = []
    words = list(words or [])
    for idx, word in enumerate(words):
        text = str(word)
        if text == name and idx + 1 < len(words):
            values.append(str(words[idx + 1]))
        elif text.startswith(name + "="):
            values.append(text.split("=", 1)[1])
    return values


def _antigravity_add_dirs(cmd, user_args, dirs):
    seen = set()
    for raw in _antigravity_arg_values(user_args, "--add-dir"):
        try:
            seen.add(str(Path(os.path.expanduser(raw)).resolve(strict=False)))
        except OSError:
            seen.add(str(raw))
    for raw in dirs:
        if not raw:
            continue
        try:
            resolved = Path(os.path.expanduser(str(raw))).resolve(strict=False)
        except OSError:
            continue
        key = str(resolved)
        if key in seen:
            continue
        cmd.extend(["--add-dir", key])
        seen.add(key)


def _pasted_image_parent_dirs(text):
    dirs = []
    seen = set()
    for image_path in _core._extract_pasted_image_paths(text or ""):
        parent = str(Path(image_path).parent)
        if parent not in seen:
            dirs.append(parent)
            seen.add(parent)
    return dirs


def _gemini_tmp_root():
    return _core.GEMINI_HOME / "tmp"


_gemini_chat_paths_cache = {"ts": 0.0, "paths": []}
_GEMINI_PATHS_TTL = 5.0


def _gemini_chat_paths():
    # Directory walk cached for a few seconds: this is hit per-participant on
    # group-chat reads and per-session on live-activity polls, and the chat
    # listing changes slowly.
    now = time.time()
    cache = _gemini_chat_paths_cache
    root = _gemini_tmp_root()
    # Root participates in the key: GEMINI_HOME can be rebound (tests, config
    # reload) and a time-only cache would serve paths from the old root.
    if now - cache["ts"] < _GEMINI_PATHS_TTL and cache.get("root") == root:
        return cache["paths"]
    if not root.is_dir():
        cache.update({"ts": now, "paths": [], "root": root})
        return []
    paths = []
    try:
        for project_dir in root.iterdir():
            chats = project_dir / "chats"
            if not chats.is_dir():
                continue
            try:
                # Older Gemini CLI wrote one JSON object per chat (session-*.json);
                # newer builds write line-delimited logs (session-*.jsonl). Pick up
                # both so recent sessions don't silently vanish from the board.
                paths.extend(p for p in chats.glob("session-*.json") if p.is_file())
                paths.extend(p for p in chats.glob("session-*.jsonl") if p.is_file())
            except OSError:
                continue
    except OSError:
        cache.update({"ts": now, "paths": [], "root": root})
        return []
    try:
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        paths.sort(key=lambda p: str(p), reverse=True)
    cache.update({"ts": now, "paths": paths, "root": root})
    return paths


def _parse_gemini_jsonl(text):
    """Reconstruct the legacy single-object chat shape from a line-delimited
    Gemini chat log.

    Newer Gemini CLI writes session-*.jsonl: the first line is the session
    header (sessionId/projectHash/lastUpdated/...), `{"$set": {...}}` lines patch
    header fields incrementally (e.g. lastUpdated), and the remaining lines are
    message records. Downstream code expects the old shape — a dict with a
    top-level `messages` list — so normalise to that here."""
    header = None
    messages = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        patch = obj.get("$set")
        if isinstance(patch, dict):
            if header is not None:
                header.update(patch)
            continue
        if header is None and obj.get("sessionId"):
            header = obj
            continue
        messages.append(obj)
    if header is None:
        return None
    header = dict(header)
    header["messages"] = messages
    return header


def _load_gemini_chat(path):
    p = Path(path)
    try:
        text = p.read_text()
    except OSError:
        return None
    if p.suffix == ".jsonl":
        return _parse_gemini_jsonl(text)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Some builds emit line-delimited chats under a .json-ish name; fall
        # back to JSONL parsing before giving up.
        data = _parse_gemini_jsonl(text)
    return data if isinstance(data, dict) else None


# path -> (mtime, sessionId). Resolving a gemini session used to re-read and
# JSON-parse EVERY chat file on disk, once per caller — the dominant cost of
# group-chat opens (per participant) and a big chunk of live-activity polls.
# Caching the sessionId by (path, mtime) turns those full re-parses into a stat
# + dict lookup; only changed files are re-read.
_gemini_sessionid_index = {}


def _gemini_sessionid_for_path(path):
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return None
    key = str(path)
    cached = _gemini_sessionid_index.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    data = _load_gemini_chat(path)
    sid = data.get("sessionId") if isinstance(data, dict) else None
    _gemini_sessionid_index[key] = (mtime, sid)
    return sid


def _gemini_project_dir_for_chat(path):
    try:
        return Path(path).parent.parent
    except (TypeError, ValueError):
        return None


def _gemini_project_root_for_chat(path):
    project_dir = _gemini_project_dir_for_chat(path)
    if not project_dir:
        return ""
    root_file = project_dir / ".project_root"
    try:
        return root_file.read_text().strip()
    except OSError:
        return ""


def _resolve_gemini_chat_path(session_id):
    if not session_id:
        return None
    short = session_id.split("-", 1)[0]
    paths = _gemini_chat_paths()
    # Filename embeds the leading UUID segment; check likely matches first.
    likely = [p for p in paths if short and short in p.name]
    for p in likely + [p for p in paths if p not in likely]:
        if _gemini_sessionid_for_path(p) == session_id:
            return p
    return None


def _is_gemini_session(session_id):
    return bool(_resolve_gemini_chat_path(session_id))


def _iso_ts_epoch(ts):
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _gemini_message_text(msg):
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return ""


def _gemini_tool_args(call):
    args = call.get("args") if isinstance(call, dict) else {}
    return args if isinstance(args, dict) else {}


def _gemini_tool_command(call):
    args = _gemini_tool_args(call)
    cmd = args.get("command") or ""
    return cmd if isinstance(cmd, str) else ""


def _gemini_tool_name(call):
    return (call.get("name") or call.get("displayName") or "tool").rsplit(".", 1)[-1]


def _gemini_tool_detail(call):
    args = _gemini_tool_args(call)
    cmd = _gemini_tool_command(call)
    if cmd:
        return args.get("description") or _core._shell_command_preview(cmd)
    return args.get("description") or call.get("description") or ""


def _gemini_tool_output(call):
    chunks = []
    result = call.get("result") if isinstance(call, dict) else None
    if isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                continue
            response = (((item.get("functionResponse") or {}).get("response") or {}))
            out = response.get("output") or response.get("error") or ""
            if isinstance(out, str) and out:
                chunks.append(out)
    elif isinstance(result, str):
        chunks.append(result)
    return "\n".join(chunks)


def _extract_gemini_session_id_from_log(log_path):
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
                if ev.get("type") == "init" and ev.get("session_id"):
                    return ev["session_id"]
    except OSError:
        return None
    return None


def _gemini_spawn_pid_by_session_id():
    out = {}
    for s in _core._spawned_sessions:
        if s.get("engine") != "gemini":
            continue
        sid = s.get("session_id") or s.get("resumed_sid") or _extract_gemini_session_id_from_log(s.get("log"))
        if sid:
            if not s.get("session_id"):
                s["session_id"] = sid
                _core._update_spawn_session_id_in_registry(s.get("pid"), sid)
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


def _extract_gemini_stream_tail_meta(log_path):
    meta = {
        "last_event_type": None,
        "last_meaningful_ts": 0,
        "pending_tool": None,
        "pending_file": None,
        "model": None,
    }
    pending = set()
    if not log_path:
        return meta
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
                ts_epoch = _iso_ts_epoch(ev.get("timestamp"))
                ev_type = ev.get("type")
                if ev.get("model"):
                    meta["model"] = ev.get("model") or meta["model"]
                if ev_type == "message":
                    role = ev.get("role")
                    if role == "user":
                        meta["last_event_type"] = "user"
                    elif role == "assistant":
                        meta["last_event_type"] = "assistant"
                    if ts_epoch:
                        meta["last_meaningful_ts"] = ts_epoch
                elif ev_type == "tool_use":
                    tool_id = ev.get("tool_id") or ""
                    if tool_id:
                        pending.add(tool_id)
                    name = ev.get("tool_name") or "tool"
                    params = ev.get("parameters") if isinstance(ev.get("parameters"), dict) else {}
                    detail = params.get("description") or params.get("command") or ""
                    meta["pending_tool"] = name.rsplit(".", 1)[-1]
                    meta["pending_file"] = detail[:80] if isinstance(detail, str) else None
                    meta["last_event_type"] = "assistant"
                    if ts_epoch:
                        meta["last_meaningful_ts"] = ts_epoch
                elif ev_type == "tool_result":
                    tool_id = ev.get("tool_id") or ""
                    if tool_id in pending:
                        pending.discard(tool_id)
                    if not pending:
                        meta["pending_tool"] = None
                        meta["pending_file"] = None
                    meta["last_event_type"] = "assistant"
                    if ts_epoch:
                        meta["last_meaningful_ts"] = ts_epoch
                elif ev_type == "result":
                    meta["last_event_type"] = "result"
                    meta["pending_tool"] = None
                    meta["pending_file"] = None
                    if ts_epoch:
                        meta["last_meaningful_ts"] = ts_epoch
                    stats = ev.get("stats") if isinstance(ev.get("stats"), dict) else {}
                    models = stats.get("models") if isinstance(stats.get("models"), dict) else {}
                    if models:
                        meta["model"] = next(reversed(models.keys()))
    except OSError:
        pass
    return meta


def _extract_gemini_tail_meta(path):
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return {}
    cached = _core._conv_meta_cache.get(str(path))
    if cached and cached.get("mtime") == mtime and cached.get("engine") == "gemini":
        return cached
    data = _load_gemini_chat(path)
    if not data:
        return {}
    meta = {
        "engine": "gemini",
        "mtime": mtime,
        "first_message": None,
        "last_prompt": None,
        "last_assistant_text": None,
        "last_event_type": None,
        "last_meaningful_ts": 0,
        "pending_tool": None,
        "pending_file": None,
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
        "cwd": _gemini_project_root_for_chat(path),
        "model": None,
        "latest_input_tokens": 0,
        "context_limit": GEMINI_CONTEXT_LIMIT,
    }
    pr_url_re = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d{1,7})")
    messages = data.get("messages") if isinstance(data.get("messages"), list) else []
    for pos, msg in enumerate(messages, start=1):
        if not isinstance(msg, dict):
            continue
        ts_epoch = _iso_ts_epoch(msg.get("timestamp"))
        if ts_epoch:
            meta["last_meaningful_ts"] = ts_epoch
        mtype = msg.get("type")
        if mtype == "user":
            text = _gemini_message_text(msg).strip()
            if text:
                meta["first_message"] = meta["first_message"] or text
                meta["last_prompt"] = text
            meta["last_event_type"] = "user"
            continue
        if mtype != "gemini":
            continue
        text = _gemini_message_text(msg).strip()
        if text:
            meta["last_assistant_text"] = text
            meta.update(_core._extract_codex_summary_signals(text, pr_url_re))
        meta["model"] = msg.get("model") or meta["model"]
        meta["last_event_type"] = "assistant"
        usage = _gemini_token_usage_from_message(msg)
        if usage and usage.get("input_tokens"):
            meta["latest_input_tokens"] = usage["input_tokens"]
        for call in msg.get("toolCalls") or []:
            if not isinstance(call, dict):
                continue
            name = _gemini_tool_name(call)
            detail = _gemini_tool_detail(call)
            meta["pending_tool"] = name
            meta["pending_file"] = detail[:80] if isinstance(detail, str) else None

            # Detect session signals (edit/commit/push) from Gemini tools.
            # Built-in tools like write_file/replace are edits; shell-based
            # tools are parsed via _codex_command_signals.
            if name in ("write_file", "replace", "patch", "edit_file"):
                meta["has_edit"] = True
                meta["last_edit_pos"] = pos

            cmd = _gemini_tool_command(call)
            if cmd:
                signals = _core._codex_command_signals(cmd)
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
                output = _gemini_tool_output(call)
                if signals["pr"] and output:
                    mp = pr_url_re.search(output)
                    if mp:
                        meta["tail_pr_number"] = int(mp.group(2))
                        meta["tail_pr_url"] = (
                            "https://github.com/" + mp.group(1) + "/pull/" + mp.group(2)
                        )
            if call.get("status") in ("success", "cancelled", "error"):
                meta["pending_tool"] = None
                meta["pending_file"] = None
    if not meta["last_meaningful_ts"]:
        meta["last_meaningful_ts"] = _iso_ts_epoch(data.get("lastUpdated")) or mtime
    with _core._conv_meta_cache_lock:
        _core._conv_meta_cache[str(path)] = meta
        _core._conv_meta_cache_dirty = True
    return meta


def _gemini_activity_fields_from_tail(tail, live):
    fields = {
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
    if not live or not tail:
        return fields
    ts = tail.get("last_meaningful_ts") or 0
    pending_tool = tail.get("pending_tool")
    if pending_tool:
        fields.update({
            "sidecar_status": "active",
            "sidecar_tool": pending_tool,
            "sidecar_file": tail.get("pending_file"),
            "sidecar_ts": ts,
            "sidecar_in_flight": True,
        })
        return fields
    if tail.get("last_event_type") in ("user", "assistant"):
        fields.update({
            "sidecar_status": "active",
            "sidecar_tool": "Thinking",
            "sidecar_file": None,
            "sidecar_ts": ts,
            "sidecar_in_flight": True,
        })
    return fields


_git_branch_cache = {}  # cwd -> (head_mtime, branch)
_git_branch_cache_lock = threading.Lock()


def _git_branch_for_cwd(cwd):
    # Hot-path on /api/sessions: every gemini/antigravity row asks for the
    # branch of its cwd, and the gemini+antigravity scanners alone fire ~50
    # git subprocesses per poll (~0.5s combined). Cache the answer keyed off
    # `.git/HEAD`'s mtime so a checkout invalidates the entry the next time
    # we look. Falls back to the bare subprocess if the HEAD file can't be
    # stat'd (detached worktree, weird permission).
    if not cwd:
        return ""
    cwd_s = str(cwd)
    # Invalidation key — prefer .git/HEAD's mtime; for a worktree (.git is a
    # *file*) stat .git itself; for a non-git dir fall back to the cwd dir's
    # own mtime so even the empty "" answer is cacheable. Creating a `.git`
    # later bumps the parent dir's mtime, so a dir that becomes a repo
    # self-invalidates. Without a key for these last two cases, every
    # worktree row AND every non-git gemini/antigravity cwd re-forked
    # `git rev-parse` on every poll — a subprocess per row.
    try:
        head_path = Path(cwd_s) / ".git" / "HEAD"
        if head_path.exists():
            head_mtime = head_path.stat().st_mtime
        else:
            git_path = Path(cwd_s) / ".git"
            if git_path.exists():
                head_mtime = git_path.stat().st_mtime
            else:
                head_mtime = Path(cwd_s).stat().st_mtime
    except OSError:
        head_mtime = 0.0
    with _git_branch_cache_lock:
        cached = _git_branch_cache.get(cwd_s)
        if cached and cached[0] == head_mtime and head_mtime > 0:
            return cached[1]
    if not Path(cwd_s).is_dir():
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", cwd_s, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    branch = ""
    if out.returncode == 0:
        branch = (out.stdout or "").strip()
        branch = "" if branch == "HEAD" else branch
    # Cache positive AND negative ("" for non-git dirs) results — the whole
    # point is to stop re-forking for cwds that will never be git repos.
    if head_mtime > 0:
        with _git_branch_cache_lock:
            _git_branch_cache[cwd_s] = (head_mtime, branch)
    return branch


def find_gemini_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    paths = _gemini_chat_paths()
    if not paths:
        return []
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
    spawn_by_sid = _gemini_spawn_pid_by_session_id()
    git_top_cache = {}
    out = []
    scanned = 0
    for path in paths:
        if limit and scanned >= int(limit):
            break
        data = _load_gemini_chat(path)
        if not data:
            continue
        sid = data.get("sessionId") or ""
        if not sid:
            continue
        scanned += 1
        tail = _extract_gemini_tail_meta(path) or {}
        cwd = tail.get("cwd") or _gemini_project_root_for_chat(path)
        pinned = repo_pins.get(sid)
        pinned_repo = False
        if repo_only:
            if pinned and pinned != repo_path:
                continue
            if pinned == repo_path:
                pinned_repo = True
            elif not _core._codex_cwd_matches_repo(cwd, repo_path_obj, git_top_cache):
                continue
        try:
            st = Path(path).stat()
        except OSError:
            continue
        modified = tail.get("last_meaningful_ts") or _iso_ts_epoch(data.get("lastUpdated")) or st.st_mtime
        freshness = max(modified, last_interactions.get(sid) or 0)
        if not include_old and sid not in spawn_by_sid and cutoff > 0 and freshness < cutoff:
            continue
        if not include_old and max_rows > 0 and len(out) >= max_rows:
            continue
        first_message = (tail.get("first_message") or "").strip()
        display_name = (
            name_overrides.get(sid)
            or (first_message[:80] if first_message else None)
            or "Gemini session"
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
            folder_label = "Gemini"
        _wt_worktree_label = None
        _wt_idx = folder_label.find("-wt-")
        if _wt_idx > 0:
            _wt_worktree_label = folder_label[_wt_idx + 4:]
            folder_label = folder_label[:_wt_idx]
        spawn_info = spawn_by_sid.get(sid) or {}
        spawn_pid = spawn_info.get("pid")
        spawn_alive = bool(spawn_info.get("alive"))
        activity_tail = tail
        if spawn_alive and spawn_info.get("log"):
            stream_tail = _extract_gemini_stream_tail_meta(spawn_info.get("log"))
            if stream_tail:
                activity_tail = {**tail, **{k: v for k, v in stream_tail.items() if v not in (None, "", 0)}}
        gemini_activity = _gemini_activity_fields_from_tail(activity_tail, spawn_alive)
        branch = tail.get("tail_branch") or _git_branch_for_cwd(effective_cwd)
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "gemini",
            "engine": "gemini",
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
            "last_event_type": activity_tail.get("last_event_type") or tail.get("last_event_type"),
            "pending_tool": activity_tail.get("pending_tool") or tail.get("pending_tool"),
            "pending_file": activity_tail.get("pending_file") or tail.get("pending_file"),
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
            "is_live": spawn_alive,
            "spawn_pid": spawn_pid,
            "parent_session_id": spawn_info.get("parent_session_id") or "",
            **gemini_activity,
            "needs_approval": False,
            "needs_approval_message": "",
            "question_waiting": False,
            "question_text": "",
            "question_header": "",
            "question_preamble": "",
            "question_options": [],
            "question_option_details": [],
            "model": tail.get("model") or "",
            "reasoning_effort": "",
            "latest_input_tokens": tail.get("latest_input_tokens") or 0,
            "context_limit": tail.get("context_limit") or GEMINI_CONTEXT_LIMIT,
        })
    seen_ids = {c.get("id") for c in out}
    antigravity_spawn_by_sid = _core._antigravity_spawn_pid_by_session_id()
    antigravity_summary_titles = _load_antigravity_summary_titles()
    for log_path in _core._antigravity_cli_log_paths(repo_path if repo_only else None):
        if limit and scanned >= int(limit):
            break
        meta = _core._antigravity_cli_log_meta(log_path)
        sid = meta.get("session_id") or ""
        if not sid or sid in seen_ids:
            continue
        transcript = _antigravity_transcript_path(sid)
        if transcript and transcript.is_file():
            continue
        cli_conversation = _antigravity_cli_conversation_path(sid)
        if not cli_conversation or not cli_conversation.is_file():
            continue
        scanned += 1
        spawn_info = antigravity_spawn_by_sid.get(sid) or {}
        cwd = meta.get("cwd") or spawn_info.get("cwd") or ""
        pinned = repo_pins.get(sid)
        pinned_repo = False
        if repo_only:
            if pinned and pinned != repo_path:
                continue
            if pinned == repo_path:
                pinned_repo = True
            elif not _core._codex_cwd_matches_repo(cwd, repo_path_obj, git_top_cache):
                continue
        try:
            st = cli_conversation.stat()
        except OSError:
            try:
                st = log_path.stat()
            except OSError:
                continue
        try:
            log_mtime = log_path.stat().st_mtime
        except OSError:
            log_mtime = st.st_mtime
        modified = max(st.st_mtime, log_mtime, last_interactions.get(sid) or 0)
        if not include_old and cutoff > 0 and modified < cutoff:
            continue
        if not include_old and max_rows > 0 and len(out) >= max_rows:
            continue
        effective_cwd = cwd or (repo_path if repo_only else "")
        try:
            cwd_exists = bool(effective_cwd and Path(effective_cwd).is_dir())
        except OSError:
            cwd_exists = False
        folder_path = pinned or effective_cwd or ""
        if folder_path:
            _git_root = _core._find_git_root(folder_path)
            folder_label = _core._resolve_dir_case(_git_root or folder_path)
        else:
            folder_label = "Antigravity CLI"
        spawn_pid = spawn_info.get("pid")
        spawn_alive = bool(spawn_info.get("alive"))
        is_live = spawn_alive if spawn_pid else (time.time() - modified) < _core._ANTIGRAVITY_LIVE_WINDOW_S
        first_message = (spawn_info.get("prompt") or "").strip()
        ai_title = antigravity_summary_titles.get(sid)
        display_name = (
            name_overrides.get(sid)
            or ai_title
            or (first_message[:80] if first_message else None)
            or _core._antigravity_log_display_name(log_path)
        )
        branch = _git_branch_for_cwd(effective_cwd)
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "antigravity",
            "engine": "antigravity",
            "timestamp": "",
            "branch": branch,
            "git_branch": branch,
            "first_message": first_message[:200],
            "display_name": display_name,
            "ai_title": ai_title or None,
            "name_overridden": bool(name_overrides.get(sid)),
            "last_prompt": first_message[:200],
            "size": st.st_size,
            "modified": modified,
            "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)),
            "mtime": modified,
            "jsonl_path": "",
            "log_path": str(log_path),
            "folder_label": folder_label,
            "folder_path": folder_path,
            "worktree_label": None,
            "session_cwd": effective_cwd,
            "session_cwd_exists": cwd_exists,
            "session_cwd_is_worktree": bool(effective_cwd and (Path(effective_cwd) / ".git").is_file()),
            "worktree_dirty": (
                _core._worktree_dirty_cached(effective_cwd, modified)
                if resolve_worktree_dirty and effective_cwd else False
            ),
            "effective_branch": branch or None,
            "effective_kind": None,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_edit_pos": 0,
            "last_commit_pos": 0,
            "last_push_pos": 0,
            "last_event_type": "user",
            "pending_tool": None,
            "pending_file": None,
            "last_assistant_text": "",
            "tail_issue_number": None,
            "tail_pr_number": None,
            "tail_pr_url": None,
            "pr_state": None,
            "session_state": None,
            "archived": sid in archived_set,
            "trashed": sid in trashed_set,
            "verified": sid in verified_set,
            "pinned_repo": pinned_repo,
            "last_interacted": last_interactions.get(sid),
            "is_live": is_live,
            "spawn_pid": spawn_pid,
            "can_headless_resume": True,
            "can_app_resume": False,
            **_core._antigravity_activity_fields_from_tail({}, is_live),
            "needs_approval": False,
            "needs_approval_message": "",
            "model": meta.get("model") or spawn_info.get("model") or "",
            "reasoning_effort": "",
        })
        seen_ids.add(sid)
    if resolve_pr_states:
        _core._prime_pr_states(c.get("tail_pr_url") for c in out)
        for c in out:
            if c.get("tail_pr_url"):
                c["pr_state"] = _core._get_pr_state(c["tail_pr_url"])
    out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
    if progress:
        progress(
            "gemini",
            state="done",
            count=len(out),
            total=scanned,
            detail=f"{len(out)} Gemini session card(s) ready.",
        )
    return out


def _gemini_token_usage_from_message(msg):
    tokens = msg.get("tokens") if isinstance(msg, dict) else {}
    if not isinstance(tokens, dict):
        return None
    input_tokens = _core._codex_int(tokens.get("input"))
    cached = _core._codex_int(tokens.get("cached"))
    output = _core._codex_int(tokens.get("output"))
    thoughts = _core._codex_int(tokens.get("thoughts"))
    tool = _core._codex_int(tokens.get("tool"))
    total = _core._codex_int(tokens.get("total"))
    if not any((input_tokens, cached, output, thoughts, tool, total)):
        return None
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_output_tokens": thoughts,
        "tool_tokens": tool,
        "total_tokens": total,
    }


def _parse_gemini_conversation(session_id, after_line=0):
    path = _resolve_gemini_chat_path(session_id)
    data = _load_gemini_chat(path) if path else None
    if not data:
        return {"events": [], "last_line": 0}
    events = []
    line_num = 0
    messages = data.get("messages") if isinstance(data.get("messages"), list) else []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        ts = msg.get("timestamp") or ""
        mtype = msg.get("type")
        if mtype == "user":
            line_num += 1
            if line_num > after_line:
                text = _gemini_message_text(msg)
                if text:
                    events.append({"line": line_num, "ts": ts, "type": "user_text", "text": text, "images": []})
            continue
        if mtype != "gemini":
            continue
        text = _gemini_message_text(msg).strip()
        if text:
            line_num += 1
            if line_num > after_line:
                events.append({
                    "line": line_num,
                    "ts": ts,
                    "type": "assistant",
                    "message_id": msg.get("id") or f"gemini-{line_num}",
                    "model": msg.get("model") or "",
                    "blocks": [{"kind": "text", "text": text}],
                })
        for call in msg.get("toolCalls") or []:
            if not isinstance(call, dict):
                continue
            line_num += 1
            if line_num > after_line:
                detail = _gemini_tool_detail(call)
                if isinstance(detail, str) and len(detail) > 200:
                    detail = detail[:200] + "..."
                events.append({
                    "line": line_num,
                    "ts": call.get("timestamp") or ts,
                    "type": "assistant",
                    "message_id": f"gemini-tool-{line_num}",
                    "model": msg.get("model") or "",
                    "blocks": [{
                        "kind": "tool_use",
                        "name": _gemini_tool_name(call),
                        "detail": detail or "",
                        "id": call.get("id", ""),
                    }],
                })
            output = _gemini_tool_output(call)
            if output:
                line_num += 1
                if line_num > after_line:
                    if len(output) > 800:
                        output = output[:800] + "\n..."
                    events.append({
                        "line": line_num,
                        "ts": call.get("timestamp") or ts,
                        "type": "tool_result",
                        "text": output,
                        "tool_use_id": call.get("id", ""),
                        "is_error": call.get("status") == "error",
                    })
        usage = _gemini_token_usage_from_message(msg)
        if usage:
            line_num += 1
            if line_num > after_line:
                events.append({
                    "line": line_num,
                    "ts": ts,
                    "type": "result",
                    "duration_ms": "?",
                    "token_usage": usage,
                })
    return {"events": events, "last_line": line_num}


def _extract_gemini_usage(session_id):
    empty = {
        "latest_input_tokens": 0,
        "peak_input_tokens": 0,
        "total_output_tokens": 0,
        "total_input_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "model": "",
        "context_limit": GEMINI_CONTEXT_LIMIT,
        "cost_usd": 0.0,
        "cost_breakdown_usd": {"input": 0.0, "cache_creation": 0.0,
                               "cache_read": 0.0, "output": 0.0},
    }
    path = _resolve_gemini_chat_path(session_id)
    data = _load_gemini_chat(path) if path else None
    if not data:
        return empty
    latest = 0
    peak = 0
    total_in = 0
    total_cached = 0
    total_out = 0
    model = ""
    for msg in data.get("messages") or []:
        if not isinstance(msg, dict) or msg.get("type") != "gemini":
            continue
        if msg.get("model"):
            model = msg.get("model")
        usage = _gemini_token_usage_from_message(msg)
        if not usage:
            continue
        window = usage["input_tokens"]
        if window:
            latest = window
            peak = max(peak, window)
        total_cached += usage["cached_input_tokens"]
        total_in += max(usage["input_tokens"] - usage["cached_input_tokens"], 0)
        total_out += usage["output_tokens"] + usage["reasoning_output_tokens"] + usage.get("tool_tokens", 0)
    return {
        **empty,
        "latest_input_tokens": latest,
        "peak_input_tokens": peak,
        "total_output_tokens": total_out,
        "total_input_tokens": total_in,
        "total_cache_read_tokens": total_cached,
        "model": model,
        "override": _core._get_session_override(session_id),
    }


def _extract_gemini_timeline(session_id):
    path = _resolve_gemini_chat_path(session_id)
    data = _load_gemini_chat(path) if path else None
    if not data:
        return {"events": [], "total_turns": 0}
    events = []
    turn = 0
    for msg in data.get("messages") or []:
        if not isinstance(msg, dict) or msg.get("type") != "gemini":
            continue
        turn += 1
        ts = msg.get("timestamp") or ""
        for call in msg.get("toolCalls") or []:
            if not isinstance(call, dict):
                continue
            cmd = _gemini_tool_command(call)
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
            if not kind:
                continue
            output = _gemini_tool_output(call)
            entry = {
                "kind": kind,
                "turn": turn,
                "ts": call.get("timestamp") or ts,
                "subject": subject,
                "success": call.get("status") == "success",
            }
            if kind == "commit":
                m = _core._TIMELINE_COMMIT_RESULT_RE.search(output)
                if m:
                    entry["sha"] = m.group(1)
                    if not entry.get("subject"):
                        entry["subject"] = m.group(2).strip()[:200]
            elif kind == "pr":
                m = _core._TIMELINE_PR_NUMBER_FROM_URL_RE.search(output)
                if m:
                    entry["pr_number"] = int(m.group(1))
            events.append(entry)
    return {"events": events, "total_turns": turn}

