"""Extracted from server.py (originally lines 37378-39574).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import collections
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time

import server as _core

# ---------------------------------------------------------------------------
# Hermes conversation ingestion (read-only).
#
# Hermes keeps canonical history in ~/.hermes/state.db. Older JSONL exports are
# legacy, so CCC reads SQLite directly and folds parent_session_id continuation
# chains into one visible conversation row per lineage leaf.
#
# TODO(hermes-search): merge Hermes messages_fts into /api/search-history so
# Hermes full-text hits surface alongside the claude-index results. Row/list and
# transcript viewing are wired first because they do not require changing the
# cross-provider search result contract.
# ---------------------------------------------------------------------------

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
HERMES_STATE_DB = Path(
    os.environ.get("CCC_HERMES_STATE_DB")
    or os.environ.get("HERMES_STATE_DB")
    or (HERMES_HOME / "state.db")
).expanduser()
HERMES_GATEWAY_SESSIONS = Path(
    os.environ.get("CCC_HERMES_GATEWAY_SESSIONS")
    or (HERMES_HOME / "sessions" / "sessions.json")
).expanduser()
HERMES_WHATSAPP_DIR = Path(
    os.environ.get("CCC_HERMES_WHATSAPP_DIR")
    or (HERMES_HOME / "whatsapp")
).expanduser()
HERMES_WHATSAPP_BRIDGE_LOG = Path(
    os.environ.get("CCC_HERMES_WHATSAPP_BRIDGE_LOG")
    or (HERMES_WHATSAPP_DIR / "bridge.log")
).expanduser()
HERMES_CHUCK_PENDING_DIR = Path(
    os.environ.get("CCC_HERMES_CHUCK_PENDING_DIR")
    or (HERMES_WHATSAPP_DIR / "chuck_realtor_pending")
).expanduser()
# Profile workers (e.g. the "chuckrealtor" / Becky agent that actually writes
# code) run under their OWN state.db at ~/.hermes/profiles/<name>/state.db,
# not the main gateway DB. Those are the sessions that do the real work, so
# CCC ingests every profile DB alongside the gateway one.
HERMES_PROFILES_DIR = Path(
    os.environ.get("CCC_HERMES_PROFILES_DIR")
    or (HERMES_HOME / "profiles")
).expanduser()
HERMES_CONTEXT_LIMIT = 200_000
_HERMES_ID_CACHE = {"key": None, "ids": set()}
# session_id -> owning DB path (main gateway DB or a profile DB). Rebuilt by
# _hermes_session_ids() and keyed by the same (mtime,size) cache key.
_HERMES_DB_INDEX = {"key": None, "by_session": {}}
_HERMES_GATEWAY_CACHE = {"key": None, "by_session": {}}
_HERMES_BRIDGE_PREFIX = "hermes-whatsapp-bridge:"
_HERMES_PENDING_PREFIX = "hermes-whatsapp-pending:"


def _hermes_db_path():
    try:
        p = Path(HERMES_STATE_DB).expanduser()
        return p if p.is_file() else None
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def _hermes_profile_for_db(db):
    """Profile name for a DB path, or "" for the main gateway DB.

    ~/.hermes/profiles/chuckrealtor/state.db -> "chuckrealtor"."""
    try:
        db = Path(db)
        main = Path(HERMES_STATE_DB).expanduser()
        if db == main:
            return ""
        pdir = Path(HERMES_PROFILES_DIR)
        if pdir in db.parents:
            # .../profiles/<name>/state.db -> <name>
            rel = db.relative_to(pdir)
            return rel.parts[0] if rel.parts else ""
    except (OSError, RuntimeError, ValueError, TypeError):
        pass
    return ""


def _hermes_db_paths():
    """All Hermes state DBs: the main gateway DB first, then each profile DB.

    Main is first so that on the (unlikely) event of a session-id collision the
    gateway DB wins in the session->DB index."""
    paths = []
    main = _hermes_db_path()
    if main is not None:
        paths.append(main)
    try:
        pdir = Path(HERMES_PROFILES_DIR)
        if pdir.is_dir():
            for child in sorted(pdir.iterdir(), key=lambda p: p.name.lower()):
                try:
                    if not child.is_dir() or child.name.startswith("."):
                        continue
                    cand = child / "state.db"
                    if cand.is_file():
                        paths.append(cand)
                except OSError:
                    continue
    except (OSError, RuntimeError, ValueError, TypeError):
        pass
    return paths


def _hermes_db_cache_key():
    """Combined (mtime,size) across every Hermes DB so caches invalidate when
    any gateway or profile DB changes."""
    mtime_ns = 0
    size = 0
    for db in _hermes_db_paths():
        for p in (db, Path(str(db) + "-wal")):
            try:
                st = p.stat()
            except OSError:
                continue
            mtime_ns = max(mtime_ns, st.st_mtime_ns)
            size += st.st_size
    return (mtime_ns, size)


def _hermes_file_key(paths):
    mtime_ns = 0
    size = 0
    for path in paths:
        try:
            st = Path(path).expanduser().stat()
        except (OSError, RuntimeError, ValueError, TypeError):
            continue
        mtime_ns = max(mtime_ns, st.st_mtime_ns)
        size += st.st_size
    return (mtime_ns, size)


def _hermes_pending_paths():
    try:
        pdir = Path(HERMES_CHUCK_PENDING_DIR).expanduser()
        if not pdir.is_dir():
            return []
        return [
            p for p in sorted(pdir.glob("*.json"), key=lambda p: p.name.lower())
            if p.is_file()
        ]
    except (OSError, RuntimeError, ValueError, TypeError):
        return []


def _hermes_cache_key():
    """Combined cache key for all Hermes-backed cards, including file-backed
    WhatsApp gateway sources that may exist before a row reaches state.db."""
    db_mtime, db_size = _hermes_db_cache_key()
    paths = [HERMES_WHATSAPP_BRIDGE_LOG]
    paths.extend(_hermes_pending_paths())
    file_mtime, file_size = _hermes_file_key(paths)
    return (max(db_mtime, file_mtime), db_size + file_size)


def _hermes_bridge_session_id(chat_id):
    chat = str(chat_id or "").strip()
    return _HERMES_BRIDGE_PREFIX + chat if chat else ""


def _hermes_pending_session_id(chat_id):
    chat = str(chat_id or "").strip()
    return _HERMES_PENDING_PREFIX + chat if chat else ""


def _hermes_external_session_kind(session_id):
    sid = str(session_id or "").strip()
    if sid.startswith(_HERMES_BRIDGE_PREFIX) and sid[len(_HERMES_BRIDGE_PREFIX):]:
        return "bridge"
    if sid.startswith(_HERMES_PENDING_PREFIX) and sid[len(_HERMES_PENDING_PREFIX):]:
        return "pending"
    return ""


def _hermes_external_chat_id(session_id):
    sid = str(session_id or "").strip()
    kind = _hermes_external_session_kind(sid)
    if kind == "bridge":
        return sid[len(_HERMES_BRIDGE_PREFIX):]
    if kind == "pending":
        return sid[len(_HERMES_PENDING_PREFIX):]
    return ""


def _hermes_read_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _hermes_bridge_tail_lines(max_lines=2000):
    try:
        with open(Path(HERMES_WHATSAPP_BRIDGE_LOG).expanduser(), "r", encoding="utf-8", errors="replace") as fh:
            return list(collections.deque(fh, maxlen=max(1, int(max_lines or 2000))))
    except (OSError, RuntimeError, ValueError, TypeError):
        return []


def _hermes_parse_bridge_line(raw, line_num):
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        if text.startswith("[bridge]"):
            return {
                "line": line_num,
                "type": "system",
                "subtype": "hermes_whatsapp_bridge_log",
                "session": "",
                "text": text,
            }
        return None
    if not isinstance(obj, dict) or obj.get("event") != "upsert":
        return None
    chat_id = str(obj.get("chatId") or "").strip()
    body = str(obj.get("body") or "").strip()
    if not chat_id or not body:
        return None
    ts = _hermes_iso(obj.get("time") or obj.get("timestamp"))
    from_me = bool(obj.get("fromMe"))
    ev_type = "assistant" if from_me else "user_text"
    ev = {
        "line": line_num,
        "ts": ts,
        "type": ev_type,
        "subtype": "hermes_whatsapp_bridge",
        "session": _hermes_bridge_session_id(chat_id),
        "source_platform": "whatsapp",
        "chat_id": chat_id,
        "sender_id": str(obj.get("senderId") or "").strip(),
        "text": body,
    }
    if ev_type == "assistant":
        ev["message_id"] = str(obj.get("msgId") or f"hermes-bridge-{line_num}")
        ev["blocks"] = [{"kind": "text", "text": body}]
    return ev


def _hermes_bridge_events_by_chat():
    by_chat = {}
    lines = _hermes_bridge_tail_lines()
    start_line = 0
    try:
        with open(Path(HERMES_WHATSAPP_BRIDGE_LOG).expanduser(), "r", encoding="utf-8", errors="replace") as fh:
            total_lines = sum(1 for _ in fh)
        start_line = max(0, total_lines - len(lines))
    except (OSError, RuntimeError, ValueError, TypeError):
        pass
    for idx, raw in enumerate(lines, start=start_line + 1):
        ev = _hermes_parse_bridge_line(raw, idx)
        if not ev:
            continue
        chat_id = ev.get("chat_id") or ""
        if chat_id:
            by_chat.setdefault(chat_id, []).append(ev)
    return by_chat


def _hermes_pending_entries():
    out = []
    for path in _hermes_pending_paths():
        data = _hermes_read_json_file(path)
        chat_id = str(data.get("group_chat_id") or path.stem).strip()
        if not chat_id:
            continue
        try:
            st = path.stat()
            mtime = float(st.st_mtime)
        except OSError:
            mtime = 0.0
        out.append((path, chat_id, data, mtime))
    return out


def _hermes_connect(db=None):
    if db is None:
        db = _hermes_db_path()
    if not db:
        return None
    try:
        con = sqlite3.connect(str(db), timeout=0.5)
        con.execute("PRAGMA query_only=1")
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _hermes_db_for_session(session_id):
    """Return the DB path that owns this session id (gateway or a profile)."""
    sid = str(session_id or "").strip()
    if not sid:
        return None
    if _HERMES_DB_INDEX.get("key") != _hermes_db_cache_key():
        _hermes_session_ids()  # rebuilds the index as a side effect
    return _HERMES_DB_INDEX.get("by_session", {}).get(sid)


def _hermes_columns(con, table):
    if not con or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table or ""):
        return set()
    try:
        return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _hermes_session_ids():
    key = _hermes_db_cache_key()
    if _HERMES_ID_CACHE.get("key") == key:
        return set(_HERMES_ID_CACHE.get("ids") or set())
    ids = set()
    by_session = {}
    for db in _hermes_db_paths():
        con = _hermes_connect(db)
        if con is None:
            continue
        try:
            cols = _hermes_columns(con, "sessions")
            if "id" in cols:
                for row in con.execute("SELECT id FROM sessions"):
                    sid = row["id"]
                    if sid:
                        sid = str(sid)
                        ids.add(sid)
                        by_session.setdefault(sid, db)  # main DB wins on tie
        except sqlite3.Error:
            pass
        finally:
            con.close()
    _HERMES_ID_CACHE["key"] = key
    _HERMES_ID_CACHE["ids"] = set(ids)
    _HERMES_DB_INDEX["key"] = key
    _HERMES_DB_INDEX["by_session"] = by_session
    return ids


def _is_hermes_session(session_id):
    sid = str(session_id or "").strip()
    if not sid:
        return False
    if _hermes_external_session_kind(sid):
        return True
    return sid in _hermes_session_ids()


def _resolve_hermes_bin():
    """Locate a usable Hermes CLI binary."""
    env_bin = os.environ.get("CCC_HERMES_BIN")
    if env_bin:
        expanded = os.path.expanduser(env_bin)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return {"available": True, "bin": expanded, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "code": "hermes_unavailable",
            "reason": f"CCC_HERMES_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("hermes")
    if which_bin:
        return {"available": True, "bin": which_bin, "source": "path"}
    for candidate in _core._iter_common_cli_candidates("hermes"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return {"available": True, "bin": str(candidate), "source": "candidate"}
    local_bin = Path.home() / ".local" / "bin" / "hermes"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return {"available": True, "bin": str(local_bin), "source": "candidate"}
    return {
        "available": False,
        "bin": None,
        "code": "hermes_unavailable",
        "reason": "Hermes CLI not found. Install Hermes or set CCC_HERMES_BIN.",
    }


_ENGINE_UPDATE_STATE_FILE = _core.COMMAND_CENTER_STATE_DIR / "engine-updates.json"
_ENGINE_UPDATE_LOCK_FILE = _core.COMMAND_CENTER_STATE_DIR / "engine-updates.lock"
_ENGINE_UPDATE_INTERVAL_SEC = 60 * 60
_ENGINE_UPDATE_TIMEOUT_SEC = 5 * 60
_ENGINE_UPDATE_MUTEX = threading.Lock()
_ENGINE_UPDATE_START_LOCK = threading.Lock()
_ENGINE_UPDATE_RUNNING = False
_ENGINE_UPDATE_THREAD_ACTIVE = False


def _engine_update_specs():
    """Return only CLIs with a confirmed, non-interactive update command."""
    return [
        {
            "id": "claude",
            "label": "Claude Code",
            "resolver": _core._resolve_claude_bin,
            "args": ("update",),
            "install": "curl -fsSL https://claude.ai/install.sh | bash",
        },
        {
            "id": "codex",
            "label": "Codex",
            "resolver": _core._resolve_codex_bin,
            "args": ("update",),
            "install": "npm install -g @openai/codex@latest",
        },
        {
            "id": "cursor",
            "label": "Cursor Agent",
            "resolver": _core._resolve_cursor_bin,
            "args": ("update",),
            "install": "curl https://cursor.com/install -fsS | bash",
        },
        {
            "id": "antigravity",
            "label": "Antigravity",
            "resolver": _core._resolve_antigravity_bin,
            "args": ("update",),
            "install": "Install the AGY CLI, then restart CCC.",
        },
        {
            "id": "hermes",
            "label": "Hermes",
            "resolver": _resolve_hermes_bin,
            "args": ("update", "--yes"),
            "install": "Install Hermes Agent, then restart CCC.",
        },
    ]


def _read_engine_update_state():
    try:
        data = json.loads(_ENGINE_UPDATE_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_engine_update_state(data):
    try:
        _ENGINE_UPDATE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ENGINE_UPDATE_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, _ENGINE_UPDATE_STATE_FILE)
        return True
    except OSError:
        return False


def _engine_cli_version(bin_path):
    try:
        proc = subprocess.run(
            [bin_path, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    output = (proc.stdout or proc.stderr or "").strip()
    return output.splitlines()[0][:160] if output else ""


def _engine_update_status():
    state = _read_engine_update_state()
    engines = state.get("engines")
    if not isinstance(engines, dict):
        engines = {}
    for spec in _engine_update_specs():
        engines.setdefault(spec["id"], {
            "label": spec["label"],
            "status": "pending",
            "install": spec["install"],
        })
    return {
        "ok": True,
        "automatic": True,
        "interval_seconds": _ENGINE_UPDATE_INTERVAL_SEC,
        "running": bool(_ENGINE_UPDATE_RUNNING),
        "last_started_at": state.get("last_started_at"),
        "last_finished_at": state.get("last_finished_at"),
        "engines": engines,
    }


def _engine_update_message(proc):
    output = "\n".join(
        part.strip() for part in (proc.stdout or "", proc.stderr or "") if part.strip()
    )
    return output[-800:] if output else ""


def _run_engine_updates_once():
    """Update every supported installed CLI without blocking CCC requests."""
    global _ENGINE_UPDATE_RUNNING
    if not _ENGINE_UPDATE_MUTEX.acquire(blocking=False):
        status = _engine_update_status()
        status["busy"] = True
        status["running"] = True
        return status

    lock_file = None
    _ENGINE_UPDATE_RUNNING = True
    try:
        _ENGINE_UPDATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(_ENGINE_UPDATE_LOCK_FILE, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            status = _engine_update_status()
            status["busy"] = True
            status["running"] = True
            return status

        started_at = datetime.now(tz=timezone.utc).isoformat()
        results = {}
        for spec in _engine_update_specs():
            engine = spec["id"]
            base = {
                "label": spec["label"],
                "install": spec["install"],
                "checked_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            try:
                info = spec["resolver"]()
            except Exception as exc:
                results[engine] = {
                    **base,
                    "status": "failed",
                    "message": f"Could not locate CLI: {exc}",
                }
                continue
            if not info.get("available") or not info.get("bin"):
                results[engine] = {
                    **base,
                    "status": "missing",
                    "message": info.get("reason") or "CLI is not installed.",
                }
                continue
            bin_path = info["bin"]
            if info.get("source") == "bundle":
                results[engine] = {
                    **base,
                    "status": "managed",
                    "message": "Managed by its desktop application.",
                }
                continue
            version_before = _engine_cli_version(bin_path)
            cmd = [bin_path, *spec["args"]]
            try:
                proc = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=_ENGINE_UPDATE_TIMEOUT_SEC,
                )
            except subprocess.TimeoutExpired:
                results[engine] = {
                    **base,
                    "status": "failed",
                    "version_before": version_before,
                    "message": "Update timed out.",
                }
                continue
            except OSError as exc:
                results[engine] = {
                    **base,
                    "status": "failed",
                    "version_before": version_before,
                    "message": str(exc)[:800],
                }
                continue
            version_after = _engine_cli_version(bin_path)
            message = _engine_update_message(proc)
            if proc.returncode != 0:
                status = "failed"
                if not message:
                    message = f"Update exited with status {proc.returncode}."
            else:
                status = (
                    "updated"
                    if version_before and version_after and version_before != version_after
                    else "current"
                )
            results[engine] = {
                **base,
                "status": status,
                "version_before": version_before,
                "version_after": version_after,
                "message": message,
            }

        state = {
            "last_started_at": started_at,
            "last_finished_at": datetime.now(tz=timezone.utc).isoformat(),
            "engines": results,
        }
        _write_engine_update_state(state)
        return {
            "ok": True,
            "automatic": True,
            "interval_seconds": _ENGINE_UPDATE_INTERVAL_SEC,
            "running": False,
            **state,
        }
    finally:
        _ENGINE_UPDATE_RUNNING = False
        if lock_file is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            lock_file.close()
        _ENGINE_UPDATE_MUTEX.release()


def _engine_maintenance_once():
    catalog_status = _core._refresh_claude_model_catalog()
    update_status = _run_engine_updates_once()
    return {"updates": update_status, "catalog": catalog_status}


def _start_engine_update_pass():
    """Start one asynchronous update/catalog refresh pass."""
    global _ENGINE_UPDATE_THREAD_ACTIVE
    with _ENGINE_UPDATE_START_LOCK:
        if _ENGINE_UPDATE_THREAD_ACTIVE or _ENGINE_UPDATE_MUTEX.locked():
            return {"ok": True, "started": False, "running": True}
        _ENGINE_UPDATE_THREAD_ACTIVE = True

    def run():
        global _ENGINE_UPDATE_THREAD_ACTIVE
        try:
            _engine_maintenance_once()
        finally:
            with _ENGINE_UPDATE_START_LOCK:
                _ENGINE_UPDATE_THREAD_ACTIVE = False

    threading.Thread(
        target=run,
        daemon=True,
        name="ccc-engine-updates",
    ).start()
    return {"ok": True, "started": True, "running": True}


def _engine_maintenance_loop():
    while True:
        try:
            _engine_maintenance_once()
        except Exception:
            pass
        try:
            time.sleep(_ENGINE_UPDATE_INTERVAL_SEC)
        except Exception:
            return


def _hermes_epoch(value):
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        try:
            v = float(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if v <= 0:
            return 0.0
        if v > 1e17:
            v /= 1_000_000_000.0
        elif v > 1e14:
            v /= 1_000_000.0
        elif v > 1e11:
            v /= 1_000.0
        return v
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return _hermes_epoch(float(text))
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError, OverflowError):
        pass
    m = re.match(r"^(\d{8})_(\d{6})_", text)
    if m:
        try:
            dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return 0.0


def _hermes_iso(value):
    ts = _hermes_epoch(value)
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return ""


def _hermes_jsonish(value):
    cur = value
    for _ in range(2):
        if isinstance(cur, bytes):
            cur = cur.decode("utf-8", errors="replace")
        if not isinstance(cur, str):
            return cur
        text = cur.strip()
        if not text:
            return ""
        if text[0] not in "{[\"" and text not in ("null", "true", "false"):
            return cur
        try:
            cur = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return cur
    return cur


def _hermes_join_text(parts):
    cleaned = []
    for part in parts:
        text = re.sub(r"\s+\n", "\n", str(part or "")).strip()
        if text:
            cleaned.append(text)
    return "\n".join(cleaned).strip()


def _hermes_visible_text(value):
    value = _hermes_jsonish(value)

    def walk(v):
        v = _hermes_jsonish(v)
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            out = []
            for item in v:
                out.extend(walk(item))
            return out
        if isinstance(v, dict):
            out = []
            # Prefer human-authored content fields. Do not dump arbitrary dicts:
            # gateway/platform metadata can include customer-facing WhatsApp ids.
            for key in (
                "text", "content", "message", "body", "caption",
                "output_text", "input_text", "response", "result", "output",
            ):
                val = v.get(key)
                if isinstance(val, (str, list, dict)):
                    out.extend(walk(val))
            return out
        return []

    return _core._strip_ccc_session_state_instruction(_hermes_join_text(walk(value))).strip()


def _hermes_raw_content_text(value):
    """Last-resort render of a message's *own* content when the whitelist
    extraction in _hermes_visible_text yields nothing.

    JSON-mode turns (e.g. the chuck-router classifier) store the model's reply
    as a bare object like {"intent": "work_request", ...} — it carries none of
    the whitelisted text keys, so _hermes_visible_text returns "" and the whole
    turn was being dropped, leaving only the user prompts (the "sparse fragment"
    bug). This only fires as a fallback, so it never broadens what the whitelist
    already shows: the `content` column is the message's own payload (model
    output / inbound text), not nested platform metadata, so rendering it raw is
    safe and strictly better than dropping the turn.
    """
    parsed = _hermes_jsonish(value)
    if parsed is None:
        return ""
    if isinstance(parsed, str):
        return _core._strip_ccc_session_state_instruction(parsed).strip()
    try:
        dumped = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=False)
    except (TypeError, ValueError):
        dumped = str(parsed)
    return _core._strip_ccc_session_state_instruction(dumped).strip()


def _hermes_decision_summary(value):
    """Compact one-liner for a structured (JSON-object) assistant reply.

    Router/classifier turns answer with a bare object like
    {"intent": "work_request", "confidence": 0.95, "addressed_to": "becky"} —
    readable, but you have to parse the JSON in your head. Distil the headline
    decision, a confidence %, a recipient, and a couple of other short scalar
    fields into "-> work_request - 95% - to: becky" so the gist is legible
    above the raw JSON. Returns "" when there's nothing scalar worth showing.
    """
    obj = _hermes_jsonish(value)
    if not isinstance(obj, dict) or not obj:
        return ""
    head_keys = ("intent", "action", "decision", "route", "classification",
                 "category", "type", "label", "status")
    conf_keys = ("confidence", "score", "probability", "certainty")
    addr_keys = ("addressed_to", "recipient", "assignee", "target", "to")
    used = set()
    parts = []
    for k in head_keys:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            parts.append("→ " + v.strip())
            used.add(k)
            break
    for k in conf_keys:
        if k in obj:
            try:
                f = float(obj.get(k))
            except (TypeError, ValueError):
                continue
            parts.append((str(round(f * 100)) if 0 <= f <= 1 else str(round(f))) + "%")
            used.add(k)
            break
    for k in addr_keys:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            parts.append("to: " + v.strip())
            used.add(k)
            break
    extra = 0
    for k, v in obj.items():
        if extra >= 2:
            break
        if k in used or isinstance(v, bool) or not isinstance(v, (str, int, float)):
            continue
        sv = str(v).strip()
        if not sv or len(sv) > 32:
            continue
        parts.append(str(k) + ": " + sv)
        extra += 1
    if not parts:
        return ""
    return " · ".join(parts)[:160]


def _hermes_clean_error_message(err):
    """Best human-readable message from a request_dump 'error' object.

    Prefer the provider's nested error.message (clean text like "claude-...
    is not a valid model ID" or "You're out of extra usage.") over the
    top-level 'message', which wraps a python-dict repr and embeds a user_id.
    """
    if isinstance(err, str):
        return err.strip()
    if not isinstance(err, dict):
        return ""
    body = err.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            body = None
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"]).strip()
        if body.get("message"):
            return str(body["message"]).strip()
    return str(err.get("message") or "").strip()


def _hermes_error_request_id(err):
    """Anthropic request id (req_...) from a request_dump 'error' object, if any.

    The single most useful identifier for chasing a failure with provider
    support. Lives in error.body.request_id (or error.request_id); absent when
    the request never reached the provider (e.g. an invalid model id rejected
    locally)."""
    if not isinstance(err, dict):
        return ""
    rid = err.get("request_id")
    if rid:
        return str(rid).strip()
    body = err.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            body = None
    if isinstance(body, dict) and body.get("request_id"):
        return str(body["request_id"]).strip()
    return ""


def _hermes_failed_turns(session_id):
    """Failed-turn error records for a Hermes session, for inline rendering.

    Successful turns are persisted to the DB messages table, but a turn whose
    upstream API call failed is written ONLY as a
    request_dump_<sid>_<ts>.json file in the gateway's sessions/ dir (next to
    the owning state.db). Without these, the conversation view shows the user
    prompt with no indication the turn errored. Returns events tagged with a
    sort epoch so the caller can interleave them with the DB messages.
    Read-only; never raises (a bad dump must not break the transcript).
    """
    sid = str(session_id or "").strip()
    if not sid:
        return []
    out = []
    try:
        db = _hermes_db_for_session(sid)
        if db is None:
            return []
        dump_dir = Path(db).expanduser().parent / "sessions"
        if not dump_dir.is_dir():
            return []
        for fp in sorted(dump_dir.glob(f"request_dump_{sid}_*.json")):
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    dump = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(dump, dict):
                continue
            err = dump.get("error")
            msg = _hermes_clean_error_message(err)
            reason = str(dump.get("reason") or "").strip()
            if not msg and not reason:
                continue
            error_type = ""
            status = None
            if isinstance(err, dict):
                error_type = str(err.get("type") or "").strip()
                status = err.get("status_code") or err.get("code")
            model = ""
            body = (dump.get("request") or {}).get("body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except (ValueError, TypeError):
                    body = None
            if isinstance(body, dict):
                model = str(body.get("model") or "").strip()
            out.append({
                "_ts_epoch": _hermes_epoch(dump.get("timestamp")),
                "event": {
                    "ts": _hermes_iso(dump.get("timestamp")),
                    "type": "system",
                    "subtype": "hermes_failed_turn",
                    "session": sid,
                    "reason": reason,
                    "error_type": error_type,
                    "status_code": status,
                    "model": model,
                    "request_id": _hermes_error_request_id(err),
                    "text": msg,
                },
            })
    except (OSError, RuntimeError, ValueError, TypeError):
        return out
    return out


def _hermes_tool_args(raw):
    parsed = _hermes_jsonish(raw)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str) and parsed.strip():
        return {"value": parsed.strip()}
    return {}


def _hermes_tool_calls(raw):
    parsed = _hermes_jsonish(raw)
    if not parsed:
        return []
    if isinstance(parsed, dict) and isinstance(parsed.get("tool_calls"), list):
        parsed = parsed.get("tool_calls")
    elif isinstance(parsed, dict) and not any(k in parsed for k in ("name", "tool", "tool_name", "function")):
        vals = [v for v in parsed.values() if isinstance(v, dict)]
        if vals:
            parsed = vals
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    out = []
    for call in parsed:
        call = _hermes_jsonish(call)
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = (
            call.get("name") or call.get("tool_name") or call.get("tool")
            or call.get("function_name") or fn.get("name") or ""
        )
        args_raw = (
            call.get("arguments") if "arguments" in call else
            call.get("args") if "args" in call else
            call.get("input") if "input" in call else
            call.get("parameters") if "parameters" in call else
            fn.get("arguments")
        )
        args = _hermes_tool_args(args_raw)
        out.append({
            "id": call.get("id") or call.get("call_id") or call.get("tool_call_id") or "",
            "name": str(name or "tool"),
            "args": args,
        })
    return out


def _hermes_tool_display_name(name):
    raw = str(name or "").strip()
    low = raw.lower()
    if low in ("bash", "shell", "terminal", "run_command", "exec"):
        return "Bash"
    return raw or "tool"


def _hermes_tool_command(name, args):
    if not isinstance(args, dict):
        return ""
    for key in ("command", "cmd", "shell", "script"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val
    low = str(name or "").lower()
    if low in ("bash", "shell", "terminal", "run_command", "exec"):
        val = args.get("value")
        if isinstance(val, str):
            return val
    return ""


def _hermes_structured_tool_arguments(name, args):
    if not isinstance(args, dict) or not args:
        return ""
    low = str(name or "").lower()
    if not low.startswith("kanban_"):
        return ""
    try:
        return json.dumps(args, ensure_ascii=False, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return ""


def _hermes_tool_detail(name, args):
    display = _hermes_tool_display_name(name)
    command = _hermes_tool_command(display, args)
    if command:
        return _core._shell_command_activity_label(command, max_len=1200)
    structured = _hermes_structured_tool_arguments(display, args)
    if structured:
        return structured
    detail = _core._tool_use_detail(display, args, max_len=240)
    if detail:
        return detail
    if isinstance(args, dict) and args:
        try:
            return _core._prompt_fragment(json.dumps(args, ensure_ascii=False), 240)
        except (TypeError, ValueError):
            pass
    return ""


def _hermes_tool_block(call):
    name = _hermes_tool_display_name(call.get("name"))
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    detail = _hermes_tool_detail(name, args)
    block = {
        "kind": "tool_use",
        "name": name,
        "detail": detail,
        "id": call.get("id") or "",
    }
    command = _hermes_tool_command(name, args)
    if command:
        redacted = _core._redacted_shell_command_text(command, max_len=12000)
        if redacted and (
            "\n" in redacted
            or len(redacted) > 160
            or re.sub(r"\s+", " ", redacted).strip() != (detail or "")
        ):
            block["command"] = redacted
            here = _core._extract_shell_heredoc(command)
            block["command_kind"] = _core._shell_script_label(here.get("head", "")) if here else "Shell command"
    else:
        structured = _hermes_structured_tool_arguments(name, args)
        if structured:
            block["command"] = structured
            block["command_kind"] = "Kanban arguments"
    return block


def _hermes_gateway_index():
    try:
        p = Path(HERMES_GATEWAY_SESSIONS).expanduser()
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
    except (OSError, RuntimeError, ValueError, TypeError):
        _HERMES_GATEWAY_CACHE["key"] = None
        _HERMES_GATEWAY_CACHE["by_session"] = {}
        return {}
    if _HERMES_GATEWAY_CACHE.get("key") == key:
        return dict(_HERMES_GATEWAY_CACHE.get("by_session") or {})
    by_session = {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        data = None
    entries = data.values() if isinstance(data, dict) else data if isinstance(data, list) else []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("session_id") or "").strip()
        if not sid:
            continue
        by_session[sid] = {
            "platform": entry.get("platform") or entry.get("origin") or "",
            "origin": entry.get("origin") or "",
            "chat_type": entry.get("chat_type") or "",
            "display_name": entry.get("display_name") or "",
            "updated_at": entry.get("updated_at") or entry.get("created_at") or "",
        }
    _HERMES_GATEWAY_CACHE["key"] = key
    _HERMES_GATEWAY_CACHE["by_session"] = dict(by_session)
    return by_session


def _hermes_fetch_sessions(con, limit=None):
    cols = _hermes_columns(con, "sessions")
    if "id" not in cols:
        return []
    rows = []
    try:
        for row in con.execute("SELECT * FROM sessions"):
            rows.append(dict(row))
    except sqlite3.Error:
        return []
    rows.sort(key=_hermes_session_epoch, reverse=True)
    if limit and int(limit) > 0:
        rows = rows[:int(limit)]
    return rows


def _hermes_fetch_messages(con, session_id):
    cols = _hermes_columns(con, "messages")
    if "session_id" not in cols:
        return []
    if "id" in cols:
        order = "id"
    elif "timestamp" in cols:
        order = "timestamp"
    elif "created_at" in cols:
        order = "created_at"
    else:
        order = "rowid"
    sql = "SELECT * FROM messages WHERE session_id=?"
    if "active" in cols:
        sql += " AND active != 0"
    sql += f" ORDER BY {order}"
    try:
        return [dict(r) for r in con.execute(sql, (session_id,))]
    except sqlite3.Error:
        return []


def _hermes_session_epoch(row):
    if not isinstance(row, dict):
        return 0.0
    for key in ("last_active_at", "updated_at", "ended_at", "started_at", "created_at"):
        ts = _hermes_epoch(row.get(key))
        if ts:
            return ts
    return _hermes_epoch(row.get("id"))


def _hermes_session_row(session_id):
    sid = str(session_id or "").strip()
    if not sid:
        return None
    con = _hermes_connect(_hermes_db_for_session(sid))
    if con is None:
        return None
    try:
        cols = _hermes_columns(con, "sessions")
        if "id" not in cols:
            return None
        row = con.execute("SELECT * FROM sessions WHERE id=? LIMIT 1", (sid,)).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _hermes_lineage_chain(session_id, rows_by_id):
    sid = str(session_id or "").strip()
    if not sid:
        return []
    chain = []
    seen = set()
    cur = sid
    while cur and cur not in seen:
        row = rows_by_id.get(cur)
        if not row:
            if cur == sid:
                chain.append(cur)
            break
        chain.append(cur)
        seen.add(cur)
        cur = str(row.get("parent_session_id") or "").strip()
    chain.reverse()
    return chain


def _hermes_message_summary(con, sid):
    summary = {
        "first_user": "",
        "last_user": "",
        "last_assistant": "",
        "kanban_task_id": "",
        "kanban_task_title": "",
        "last_ts": 0.0,
        "size": 0,
        "has_edit": False,
        "has_commit": False,
        "has_push": False,
        "tail_pr_number": None,
        "tail_pr_url": None,
        "last_event_type": None,
    }
    pr_re = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d{1,7})")
    for msg in _hermes_fetch_messages(con, sid):
        role = str(msg.get("role") or "").lower()
        ts = _hermes_epoch(msg.get("timestamp") or msg.get("created_at"))
        if ts:
            summary["last_ts"] = max(summary["last_ts"], ts)
        content = msg.get("content")
        calls_raw = msg.get("tool_calls")
        summary["size"] += len(str(content or "")) + len(str(calls_raw or "")) + len(str(msg.get("reasoning") or ""))
        text = _hermes_visible_text(content)
        if role in ("tool", "function"):
            parsed_content = _hermes_jsonish(content)
            if isinstance(parsed_content, dict):
                task = parsed_content.get("task")
                if isinstance(task, dict):
                    task_title = str(task.get("title") or "").strip()
                    task_id = str(task.get("id") or "").strip()
                    if task_title and not summary["kanban_task_title"]:
                        summary["kanban_task_title"] = task_title
                    if task_id and not summary["kanban_task_id"]:
                        summary["kanban_task_id"] = task_id
        if role in ("user", "human"):
            if text:
                summary["first_user"] = summary["first_user"] or text
                summary["last_user"] = text
            summary["last_event_type"] = "user"
        elif role == "assistant":
            if text:
                summary["last_assistant"] = text
            summary["last_event_type"] = "assistant"
        elif role in ("tool", "function"):
            summary["last_event_type"] = "tool"
        for call in _hermes_tool_calls(calls_raw):
            name = _hermes_tool_display_name(call.get("name"))
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            cmd = _hermes_tool_command(name, args)
            if name in ("Edit", "Write", "NotebookEdit") or name.lower() in (
                "edit", "write", "write_file", "replace", "patch",
            ):
                summary["has_edit"] = True
            if cmd:
                signals = _core._shell_command_signals(cmd)
                summary["has_edit"] = summary["has_edit"] or signals["edit"]
                summary["has_commit"] = summary["has_commit"] or signals["commit"]
                summary["has_push"] = summary["has_push"] or signals["push"]
                mp = pr_re.search(cmd)
                if mp:
                    summary["tail_pr_number"] = int(mp.group(2))
                    summary["tail_pr_url"] = "https://github.com/" + mp.group(1) + "/pull/" + mp.group(2)
        if text:
            mp = pr_re.search(text)
            if mp:
                summary["tail_pr_number"] = int(mp.group(2))
                summary["tail_pr_url"] = "https://github.com/" + mp.group(1) + "/pull/" + mp.group(2)
    return summary


def _hermes_folder_for_row(row, pinned=None):
    cwd = row.get("cwd") or ""
    effective_cwd = _core._first_existing_dir(cwd, pinned) or cwd
    folder_path = pinned or cwd or effective_cwd or ""
    if folder_path:
        _git_root = _core._find_git_root(folder_path)
        folder_label = _core._resolve_dir_case(_git_root or folder_path)
    else:
        folder_label = "Hermes"
    worktree_label = None
    idx = folder_label.find("-wt-")
    if idx > 0:
        worktree_label = folder_label[idx + 4:]
        folder_label = folder_label[:idx]
    try:
        cwd_exists = bool(effective_cwd and Path(effective_cwd).is_dir())
    except OSError:
        cwd_exists = False
    return folder_path, folder_label, effective_cwd, cwd_exists, worktree_label


def _hermes_base_file_row(sid, modified, display_name, first_message="", last_prompt=""):
    return {
        "id": sid,
        "session_id": sid,
        "source": "hermes",
        "engine": "hermes",
        "timestamp": "",
        "branch": None,
        "git_branch": None,
        "first_message": (first_message or "")[:200],
        "display_name": display_name or "Hermes WhatsApp",
        "ai_title": None,
        "name_overridden": False,
        "last_prompt": (last_prompt or first_message or "")[:200],
        "size": len(first_message or "") + len(last_prompt or ""),
        "modified": modified,
        "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)) if modified else "",
        "mtime": modified,
        "jsonl_path": "",
        "folder_label": "Hermes",
        "folder_path": "",
        "folder_label_chip": "Hermes",
        "worktree_label": None,
        "session_cwd": "",
        "session_cwd_exists": False,
        "session_cwd_is_worktree": False,
        "worktree_dirty": False,
        "effective_branch": None,
        "effective_kind": None,
        "has_edit": False,
        "has_commit": False,
        "has_push": False,
        "last_edit_pos": 0,
        "last_commit_pos": 0,
        "last_push_pos": 0,
        "last_event_type": "user" if last_prompt else None,
        "pending_tool": None,
        "pending_file": None,
        "last_assistant_text": "",
        "tail_issue_number": None,
        "tail_pr_number": None,
        "tail_pr_url": None,
        "pr_state": None,
        "session_state": None,
        "archived": False,
        "verified": False,
        "pinned_repo": False,
        "last_interacted": None,
        "is_live": False,
        "spawn_pid": None,
        "needs_approval": False,
        "needs_approval_message": "",
        "model": "",
        "reasoning_effort": "",
        "latest_input_tokens": 0,
        "context_limit": HERMES_CONTEXT_LIMIT,
        "source_platform": "whatsapp",
        "hermes_source": "whatsapp",
        "hermes_origin": "whatsapp",
        "hermes_chat_type": "group",
        "hermes_tool_calls": 0,
        "parent_session_id": "",
        "hermes_parent_session_id": "",
        "hermes_lineage_session_ids": [sid],
        "hermes_lineage_count": 1,
        "hermes_lineage_collapsed": False,
        "hermes_lineage_root_id": sid,
        "hermes_continued_from": "",
        "hermes_child_session_ids": [],
        "hermes_is_parent": False,
        "hermes_profile": "",
    }


def _hermes_external_rows(include_old=True):
    rows = []
    seen_chats = set()
    for path, chat_id, data, mtime in _hermes_pending_entries():
        sid = _hermes_pending_session_id(chat_id)
        created = _hermes_epoch(data.get("created_at")) or mtime
        change_id = str(data.get("change_id") or "Pending ask").strip()
        group_name = str(data.get("group_chat_name") or chat_id).strip()
        request_text = str(data.get("request_text") or "").strip()
        display = (change_id + ": " + group_name).strip(": ")
        row = _hermes_base_file_row(sid, created, display, request_text, request_text)
        row["size"] += len(json.dumps(data, ensure_ascii=False))
        row["needs_approval"] = True
        row["needs_approval_message"] = str(data.get("reason") or "pending").strip()
        row["pending_tool"] = "Ask approval"
        row["pending_file"] = path.name
        row["last_event_type"] = "user"
        row["hermes_chat_type"] = "group"
        row["hermes_pending_path"] = str(path)
        rows.append(row)
        seen_chats.add(chat_id)

    by_chat = _hermes_bridge_events_by_chat()
    for chat_id, events in by_chat.items():
        if chat_id in seen_chats or not events:
            continue
        last = events[-1]
        text = last.get("text") or ""
        modified = _hermes_epoch(last.get("ts")) or 0.0
        if not modified:
            try:
                modified = float(Path(HERMES_WHATSAPP_BRIDGE_LOG).expanduser().stat().st_mtime)
            except OSError:
                modified = 0.0
        sid = _hermes_bridge_session_id(chat_id)
        display = "WhatsApp bridge: " + chat_id
        row = _hermes_base_file_row(sid, modified, display, text, text)
        row["size"] = sum(len(e.get("text") or "") for e in events)
        row["last_event_type"] = "assistant" if last.get("type") == "assistant" else "user"
        row["hermes_bridge_log_path"] = str(Path(HERMES_WHATSAPP_BRIDGE_LOG).expanduser())
        rows.append(row)
    return rows


def find_hermes_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    db_paths = _hermes_db_paths()
    external_rows = _hermes_external_rows(include_old=include_old)
    if not db_paths:
        if progress:
            progress(
                "hermes", state="done", count=len(external_rows),
                detail=f"{len(external_rows)} Hermes file-backed card(s) ready."
            )
        return external_rows
    if repo_only:
        repo_path = _core.resolve_repo_path(repo_path)
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
    gateway = _hermes_gateway_index()
    git_top_cache = {}
    out = []
    scanned = 0
    cons = []
    work_items = []
    try:
        # Gather sessions from the gateway DB AND every profile worker DB
        # (~/.hermes/profiles/<name>/state.db). Lineage, parent/child maps and
        # message summaries are per-DB — a parent_session_id only resolves
        # within the same DB — so build them once per connection and tag each
        # row with its owning connection + profile. Surface EVERY session as
        # its own row (parents included); nothing is folded away.
        for _db in db_paths:
            con = _hermes_connect(_db)
            if con is None:
                continue
            cons.append(con)
            sessions = _hermes_fetch_sessions(con, limit=None)
            if not sessions:
                continue
            rows_by_id = {str(r.get("id")): r for r in sessions if r.get("id")}
            parent_ids = {
                str(r.get("parent_session_id"))
                for r in sessions
                if r.get("parent_session_id") and str(r.get("parent_session_id")) in rows_by_id
            }
            children_by_parent = {}
            for r in sessions:
                pid = str(r.get("parent_session_id") or "").strip()
                if pid and pid in rows_by_id:
                    children_by_parent.setdefault(pid, []).append(str(r.get("id")))
            profile = _hermes_profile_for_db(_db)
            for r in sessions:
                work_items.append((r, con, rows_by_id, parent_ids, children_by_parent, profile))
        if limit and int(limit) > 0:
            work_items = work_items[:int(limit)]
        for row, con, rows_by_id, parent_ids, children_by_parent, profile in work_items:
            sid = str(row.get("id") or "").strip()
            if not sid:
                continue
            scanned += 1
            pinned = repo_pins.get(sid)
            pinned_repo = False
            cwd = row.get("cwd") or ""
            # Hermes is a non-repo-scoped source. A Hermes session (whatsapp,
            # cli, cron, ...) is a conversation, not a checkout: its cwd is
            # often the home dir or empty and must never hide it when another
            # repo is selected. So unlike Claude/Codex we do NOT `continue` on
            # a cwd/pin mismatch under repo_only — every Hermes row surfaces in
            # every repo's sidebar. We still resolve `pinned_repo` so an
            # explicit pin to the current repo keeps driving the repo chip.
            if repo_only and pinned == repo_path:
                pinned_repo = True
            summary = _hermes_message_summary(con, sid)
            modified = (
                summary.get("last_ts")
                or _hermes_session_epoch(row)
                or (_hermes_db_cache_key()[0] / 1_000_000_000.0 if _hermes_db_cache_key()[0] else 0)
            )
            freshness = max(modified, last_interactions.get(sid) or 0)
            if not include_old and cutoff > 0 and freshness < cutoff:
                continue
            if not include_old and max_rows > 0 and len(out) >= max_rows:
                continue
            gw = gateway.get(sid) or {}
            source_platform = (
                row.get("source") or gw.get("platform") or gw.get("origin") or ""
            ).strip() or "hermes"
            title = _core._strip_ccc_session_state_instruction(row.get("title") or "").strip()
            gateway_title = _core._strip_ccc_session_state_instruction(gw.get("display_name") or "").strip()
            first_message = summary.get("first_user") or ""
            kanban_title = _core._strip_ccc_session_state_instruction(summary.get("kanban_task_title") or "").strip()
            ai_title = (
                title if title and title != first_message
                else kanban_title if kanban_title and kanban_title != first_message
                else None
            )
            display_name = (
                name_overrides.get(sid)
                or _core._truncate_session_name(title)
                or _core._truncate_session_name(kanban_title)
                or _core._truncate_session_name(gateway_title)
                or (first_message[:80] if first_message else None)
                or "Hermes session"
            )
            folder_path, folder_label, effective_cwd, cwd_exists, wt_label = _hermes_folder_for_row(row, pinned)
            branch = _core._git_branch_for_cwd(effective_cwd)
            lineage = _hermes_lineage_chain(sid, rows_by_id)
            parent_id = str(row.get("parent_session_id") or "").strip()
            try:
                cwd_is_worktree = bool(effective_cwd and (Path(effective_cwd) / ".git").is_file())
            except OSError:
                cwd_is_worktree = False
            out.append({
                "id": sid,
                "session_id": sid,
                "source": "hermes",
                "engine": "hermes",
                "timestamp": "",
                "branch": branch,
                "git_branch": branch,
                "first_message": first_message[:200],
                "display_name": display_name,
                "ai_title": ai_title,
                "name_overridden": bool(name_overrides.get(sid)),
                "last_prompt": (summary.get("last_user") or "")[:200],
                "size": int(summary.get("size") or 0),
                "modified": modified,
                "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)) if modified else "",
                "mtime": modified,
                "jsonl_path": "",
                "folder_label": folder_label,
                "folder_path": folder_path,
                "folder_label_chip": "Hermes" if not folder_path else "",
                "worktree_label": wt_label,
                "session_cwd": effective_cwd,
                "session_cwd_exists": cwd_exists,
                "session_cwd_is_worktree": cwd_is_worktree,
                "worktree_dirty": (
                    _core._worktree_dirty_cached(effective_cwd, modified)
                    if resolve_worktree_dirty and effective_cwd else False
                ),
                "effective_branch": None,
                "effective_kind": None,
                "has_edit": bool(summary.get("has_edit")),
                "has_commit": bool(summary.get("has_commit")),
                "has_push": bool(summary.get("has_push")),
                "last_edit_pos": 0,
                "last_commit_pos": 0,
                "last_push_pos": 0,
                "last_event_type": summary.get("last_event_type"),
                "pending_tool": None,
                "pending_file": None,
                "last_assistant_text": summary.get("last_assistant") or "",
                "tail_issue_number": None,
                "tail_pr_number": summary.get("tail_pr_number"),
                "tail_pr_url": summary.get("tail_pr_url"),
                "pr_state": None,
                "session_state": _core._parse_session_state(summary.get("last_assistant")),
                "archived": sid in archived_set or bool(row.get("archived")),
                "trashed": sid in trashed_set or bool(row.get("trashed")),
                "verified": sid in verified_set,
                "pinned_repo": pinned_repo,
                "last_interacted": last_interactions.get(sid),
                "is_live": False,
                "spawn_pid": None,
                "needs_approval": False,
                "needs_approval_message": "",
                "model": row.get("model") or "",
                "reasoning_effort": "",
                "latest_input_tokens": _core._codex_int(row.get("input_tokens"))
                    + _core._codex_int(row.get("cache_read_tokens"))
                    + _core._codex_int(row.get("cache_write_tokens")),
                "context_limit": HERMES_CONTEXT_LIMIT,
                "source_platform": source_platform,
                "hermes_source": source_platform,
                "hermes_origin": gw.get("origin") or "",
                "hermes_chat_type": gw.get("chat_type") or "",
                # Tool-call count distinguishes agentic (LLM-with-tools) Hermes
                # sessions from plain chat conversations — see the "tools"/"chat"
                # chip in the sidebar. Lives on the session row already.
                "hermes_tool_calls": _core._codex_int(row.get("tool_call_count")),
                "hermes_kanban_task_id": summary.get("kanban_task_id") or "",
                "hermes_kanban_task_title": kanban_title,
                "parent_session_id": parent_id,
                "hermes_parent_session_id": parent_id,
                "hermes_lineage_session_ids": lineage,
                "hermes_lineage_count": len(lineage),
                "hermes_lineage_collapsed": len(lineage) > 1,
                "hermes_lineage_root_id": lineage[0] if lineage else sid,
                "hermes_continued_from": parent_id,
                "hermes_child_session_ids": children_by_parent.get(sid, []),
                "hermes_is_parent": sid in parent_ids,
                # "" for the main gateway DB; profile name (e.g. "chuckrealtor")
                # for a worker session living in a profile's own state.db.
                "hermes_profile": profile,
            })
        out.extend(external_rows)
        if resolve_pr_states:
            _core._prime_pr_states(c.get("tail_pr_url") for c in out)
            for c in out:
                if c.get("tail_pr_url"):
                    c["pr_state"] = _core._get_pr_state(c["tail_pr_url"])
        out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
        if progress:
            progress(
                "hermes",
                state="done",
                count=len(out),
                total=scanned,
                detail=f"{len(out)} Hermes session card(s) ready.",
            )
        return out
    finally:
        for _c in cons:
            try:
                _c.close()
            except Exception:
                pass


def _hermes_reasoning_visible():
    return os.environ.get("CCC_HERMES_SHOW_REASONING", "0").lower() in ("1", "true", "yes", "on")


def _parse_hermes_message(msg, line_num, session_row=None):
    role = str(msg.get("role") or "").lower()
    ts = _hermes_iso(msg.get("timestamp") or msg.get("created_at"))
    text = _hermes_visible_text(msg.get("content"))
    if role in ("user", "human"):
        if not text:
            text = _hermes_raw_content_text(msg.get("content"))
        if text:
            return {"line": line_num, "ts": ts, "type": "user_text", "text": text, "images": []}
        return None
    if role == "assistant":
        blocks = []
        if _hermes_reasoning_visible():
            reasoning = _hermes_visible_text(
                msg.get("reasoning") or msg.get("reasoning_content") or msg.get("reasoning_details")
            )
            if reasoning:
                blocks.append({
                    "kind": "thinking",
                    "text": reasoning[:4000] + ("..." if len(reasoning) > 4000 else ""),
                    "signature_only": False,
                })
        if text:
            blocks.append({"kind": "text", "text": text})
        tool_blocks = [_hermes_tool_block(call) for call in _hermes_tool_calls(msg.get("tool_calls"))]
        blocks.extend(tool_blocks)
        if not text and not tool_blocks:
            # JSON-mode / structured reply: content is a bare object with no
            # whitelisted text key. Render it raw rather than dropping the turn,
            # with a distilled one-liner above it when it reads like a decision.
            raw = _hermes_raw_content_text(msg.get("content"))
            if raw:
                block = {"kind": "text", "text": raw}
                summary = _hermes_decision_summary(msg.get("content"))
                if summary:
                    block["summary"] = summary
                blocks.append(block)
        if blocks:
            ev = {
                "line": line_num,
                "ts": ts,
                "type": "assistant",
                "message_id": f"hermes-{msg.get('id') or line_num}",
                "blocks": blocks,
            }
            model = (session_row or {}).get("model")
            if model:
                ev["model"] = model
            return ev
        return None
    if role in ("tool", "function") or msg.get("tool_name") or msg.get("tool_call_id"):
        if not text:
            text = _hermes_visible_text(msg.get("tool_calls")) or _hermes_raw_content_text(msg.get("content"))
        if len(text) > 800:
            text = text[:800] + "\n..."
        return {
            "line": line_num,
            "ts": ts,
            "type": "tool_result",
            "text": text,
            "tool_use_id": msg.get("tool_call_id") or msg.get("id") or "",
            "is_error": str(msg.get("finish_reason") or "").lower() == "error",
        }
    return None


def _parse_hermes_pending_conversation(session_id, after_line=0):
    chat_id = _hermes_external_chat_id(session_id)
    entry = None
    for path, cid, data, _mtime in _hermes_pending_entries():
        if cid == chat_id:
            entry = (path, data)
            break
    if not entry:
        return {"events": [], "last_line": 0}
    path, data = entry
    line = 0
    events = []

    def add(ev):
        nonlocal line
        line += 1
        ev["line"] = line
        ev.setdefault("session", session_id)
        events.append(ev)

    ts = _hermes_iso(data.get("created_at"))
    change_id = str(data.get("change_id") or "Pending ask").strip()
    reason = str(data.get("reason") or "").strip()
    add({
        "ts": ts,
        "type": "system",
        "subtype": "hermes_pending_ask",
        "source_platform": "whatsapp",
        "text": (change_id + (" - " + reason if reason else "")).strip(),
        "pending_path": str(path),
        "chat_id": chat_id,
        "group_chat_name": str(data.get("group_chat_name") or ""),
    })
    req = str(data.get("request_text") or "").strip()
    if req:
        add({
            "ts": ts,
            "type": "user_text",
            "subtype": "hermes_pending_request",
            "source_platform": "whatsapp",
            "text": req,
            "sender_id": str(data.get("sender_id") or ""),
            "sender_name": str(data.get("sender_name") or ""),
        })
    notes = data.get("notes")
    if isinstance(notes, list) and notes:
        add({
            "ts": ts,
            "type": "system",
            "subtype": "hermes_pending_notes",
            "source_platform": "whatsapp",
            "text": "\n".join(str(n).strip() for n in notes if str(n).strip()),
        })
    planning = str(data.get("private_last_planning_response") or "").strip()
    if planning:
        add({
            "ts": _hermes_iso(data.get("closed_at") or data.get("created_at")),
            "type": "assistant",
            "subtype": "hermes_pending_planning",
            "source_platform": "whatsapp",
            "text": planning,
            "message_id": f"hermes-pending-{chat_id}",
            "blocks": [{"kind": "text", "text": planning}],
        })
    try:
        after = int(after_line or 0)
    except (TypeError, ValueError):
        after = 0
    visible = [e for e in events if e.get("line", 0) > after] if after > 0 else events
    return {"events": visible, "last_line": line}


def _parse_hermes_bridge_conversation(session_id, after_line=0):
    chat_id = _hermes_external_chat_id(session_id)
    by_chat = _hermes_bridge_events_by_chat()
    raw_events = by_chat.get(chat_id) or []
    events = []
    line = 0
    if raw_events:
        line += 1
        events.append({
            "line": line,
            "type": "system",
            "subtype": "hermes_whatsapp_bridge_log",
            "session": session_id,
            "source_platform": "whatsapp",
            "chat_id": chat_id,
            "text": "Recent WhatsApp bridge messages from bridge.log",
        })
    for ev in raw_events:
        line += 1
        item = dict(ev)
        item["line"] = line
        item["session"] = session_id
        events.append(item)
    try:
        after = int(after_line or 0)
    except (TypeError, ValueError):
        after = 0
    visible = [e for e in events if e.get("line", 0) > after] if after > 0 else events
    return {"events": visible, "last_line": line}


def _parse_hermes_conversation(session_id, after_line=0):
    kind = _hermes_external_session_kind(session_id)
    if kind == "pending":
        return _parse_hermes_pending_conversation(session_id, after_line=after_line)
    if kind == "bridge":
        return _parse_hermes_bridge_conversation(session_id, after_line=after_line)
    con = _hermes_connect(_hermes_db_for_session(session_id))
    if con is None:
        return {"events": [], "last_line": 0}
    events = []
    line = 0
    try:
        sessions = _hermes_fetch_sessions(con, limit=None)
        rows_by_id = {str(r.get("id")): r for r in sessions if r.get("id")}
        if session_id not in rows_by_id:
            return {"events": [], "last_line": 0}
        chain = _hermes_lineage_chain(session_id, rows_by_id) or [session_id]
        # Phase 1: build each segment's event list up front — DB messages merged
        # with failed-turn error records (request_dump files), in chronological
        # order. Successful turns live in the DB; turns whose upstream API call
        # failed exist ONLY as dump files, so without this the transcript shows
        # the prompt with no hint the turn errored. Building first also lets us
        # tally turns for the summary banner (emitted at line 1, below).
        chain_blocks = []
        total_turns = 0
        total_failed = 0
        for sid in chain:
            row = rows_by_id.get(sid) or {}
            items = []
            last_ep = 0.0
            order = 0
            for msg in _hermes_fetch_messages(con, sid):
                parsed = _parse_hermes_message(msg, 0, row)
                if parsed:
                    ep = _hermes_epoch(msg.get("timestamp") or msg.get("created_at")) or last_ep
                    last_ep = ep
                    items.append((ep, 0, order, parsed))
                    order += 1
            for ferr in _hermes_failed_turns(sid):
                items.append((ferr["_ts_epoch"], 1, order, ferr["event"]))
                order += 1
            items.sort(key=lambda t: (t[0], t[1], t[2]))
            seg_events = [ev for _ep, _kind, _ord, ev in items]
            total_turns += sum(1 for ev in seg_events if ev.get("type") == "user_text")
            total_failed += sum(1 for ev in seg_events if ev.get("subtype") == "hermes_failed_turn")
            chain_blocks.append((sid, row, seg_events))
        # Phase 2: header events. The turn-summary banner goes first (line 1) so
        # the session's health (how many turns, how many failed) is visible at a
        # glance; then the injected system prompt and lineage. The after_line
        # filter below drops all of these from incremental polls — they show
        # once on open. Read-only.
        _sum_row = rows_by_id.get(session_id) or {}
        if total_turns or total_failed:
            line += 1
            events.append({
                "line": line,
                "ts": _hermes_iso(_sum_row.get("started_at") or _sum_row.get("created_at")),
                "type": "system",
                "subtype": "hermes_turn_summary",
                "session": session_id,
                "source_platform": _sum_row.get("source") or "",
                "model": _sum_row.get("model") or "",
                "turns": total_turns,
                "failed": total_failed,
                "succeeded": max(0, total_turns - total_failed),
            })
        # Hermes assembles a per-session system prompt (persona + skills +
        # memory + per-conversation context) and persists it on the session row;
        # CCC otherwise renders only user/assistant/tool messages, so this
        # priming layer would be invisible. Collapsed by default in the UI.
        _sys_row = _sum_row
        _sys_prompt = (_sys_row.get("system_prompt") or "").strip()
        if _sys_prompt:
            line += 1
            events.append({
                "line": line,
                "ts": _hermes_iso(_sys_row.get("started_at") or _sys_row.get("created_at")),
                "type": "system",
                "subtype": "hermes_system_prompt",
                "session": session_id,
                "text": _sys_prompt,
                "char_count": len(_sys_prompt),
            })
        if len(chain) > 1:
            current = _sum_row
            line += 1
            events.append({
                "line": line,
                "ts": _hermes_iso(current.get("started_at") or current.get("created_at")),
                "type": "system",
                "subtype": "hermes_lineage",
                "session": session_id,
                "parent_session_id": current.get("parent_session_id") or "",
                "lineage_session_ids": chain,
                "source_platform": current.get("source") or "",
                "model": current.get("model") or "",
            })
        # Phase 3: emit each segment's events (with a continuation marker before
        # any lineage-inherited segment after the first).
        for idx, (sid, row, seg_events) in enumerate(chain_blocks):
            if idx > 0:
                line += 1
                events.append({
                    "line": line,
                    "ts": _hermes_iso(row.get("started_at") or row.get("created_at")),
                    "type": "system",
                    "subtype": "hermes_segment",
                    "session": sid,
                    "parent_session_id": row.get("parent_session_id") or "",
                    "source_platform": row.get("source") or "",
                    "model": row.get("model") or "",
                })
            for ev in seg_events:
                line += 1
                ev["line"] = line
                events.append(ev)
    finally:
        con.close()
    try:
        after = int(after_line or 0)
    except (TypeError, ValueError):
        after = 0
    visible = [e for e in events if e.get("line", 0) > after] if after > 0 else events
    return {"events": visible, "last_line": line}


def _extract_hermes_usage(session_id):
    empty = {
        "latest_input_tokens": 0,
        "peak_input_tokens": 0,
        "total_output_tokens": 0,
        "total_input_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "model": "",
        "context_limit": HERMES_CONTEXT_LIMIT,
        "compact_count": 0,
        "live_context_tokens": 0,
        "live_context_limit": 0,
        "live_context_percent": 0,
        "live_context_timestamp": "",
        "live_context_source": "",
        "engine": "hermes",
        "override": None,
        "cost_usd": 0.0,
        "cost_breakdown_usd": {"input": 0.0, "cache_creation": 0.0,
                               "cache_read": 0.0, "output": 0.0},
    }
    row = _hermes_session_row(session_id) or {}
    input_tokens = _core._codex_int(row.get("input_tokens"))
    cache_read = _core._codex_int(row.get("cache_read_tokens"))
    cache_write = _core._codex_int(row.get("cache_write_tokens"))
    output_tokens = _core._codex_int(row.get("output_tokens"))
    window = input_tokens + cache_read + cache_write
    cost = row.get("actual_cost_usd")
    if cost is None:
        cost = row.get("estimated_cost_usd")
    try:
        cost_usd = float(cost or 0.0)
    except (TypeError, ValueError):
        cost_usd = 0.0
    return {
        **empty,
        "latest_input_tokens": window,
        "peak_input_tokens": window,
        "total_input_tokens": input_tokens,
        "total_cache_read_tokens": cache_read,
        "total_cache_creation_tokens": cache_write,
        "total_output_tokens": output_tokens,
        "model": row.get("model") or "",
        "cost_usd": cost_usd,
    }


def _extract_hermes_timeline(session_id):
    result = _parse_hermes_conversation(session_id, after_line=0)
    events = []
    turn = 0
    for ev in result.get("events") or []:
        if ev.get("type") != "assistant":
            continue
        turn += 1
        for block in ev.get("blocks") or []:
            if block.get("kind") != "tool_use":
                continue
            cmd = block.get("command") or block.get("detail") or ""
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
                    "ts": ev.get("ts") or "",
                    "subject": subject,
                    "success": None,
                })
    return {"events": events, "total_turns": turn}


def _extract_files_from_hermes_conversation(session_id):
    result = _parse_hermes_conversation(session_id, after_line=0)
    seen = {}
    truncated = False

    def consider(target, kind, line):
        nonlocal truncated
        truncated = _core._ffc_consider_file_target(seen, target, kind, line, truncated)

    for ev in result.get("events") or []:
        line = ev.get("line") or 0
        if ev.get("type") in ("user_text", "tool_result"):
            for target, kind in _core._ffc_iter_targets(ev.get("text") or ""):
                consider(target, kind, line)
        elif ev.get("type") == "assistant":
            for block in ev.get("blocks") or []:
                if block.get("kind") == "text":
                    for target, kind in _core._ffc_iter_targets(block.get("text") or ""):
                        consider(target, kind, line)
                elif block.get("kind") == "tool_use":
                    for raw in (block.get("detail"), block.get("command")):
                        for target, kind in _core._ffc_iter_targets(raw or ""):
                            consider(target, kind, line)
    groups = {}
    for row in seen.values():
        groups.setdefault(row["category"], []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: r["first_line"])
    return {"count": len(seen), "truncated": truncated, "groups": groups}


def resume_session_hermes(session_id, text):
    """Resume a Hermes conversation with a one-shot CLI prompt."""
    text = _core._strip_ccc_session_state_instruction(text)
    if not session_id or not text:
        return {"ok": False, "error": "missing session_id or text"}
    resolved = _resolve_hermes_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    for s in _core._spawned_sessions:
        if s.get("engine") == "hermes" and s.get("resumed_sid") == session_id:
            try:
                if _core._poll_spawn_entry(s) is None:
                    with _core._pending_resume_lock:
                        _core._pending_resume_queue.setdefault(session_id, []).append(text)
                    _core._save_pending_inputs()
                    return {
                        "ok": True,
                        "queued": True,
                        "pid": s.get("pid"),
                        "via": "hermes-resume-queued",
                        "queued_reason": "waiting for the current Hermes turn to finish",
                        "engine": "hermes",
                    }
            except Exception:
                pass
    row = _hermes_session_row(session_id) or {}
    spawned_ctx = _core._spawn_registry_entry_for_session(session_id, "hermes") or {}
    cwd = spawned_ctx.get("cwd") or row.get("cwd") or _core.find_session_cwd(session_id) or str(Path.home())
    if not Path(cwd).is_dir():
        cwd = str(Path.home())
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"resume-hermes-{str(session_id)[:8]}-{timestamp}.log"
    repo_for_logs = _core._git_toplevel_for_existing_dir(cwd) or cwd
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    cmd = [
        resolved["bin"],
        "chat",
        "--resume", str(session_id),
        "--query", text,
        "--quiet",
    ]
    model = row.get("model") or spawned_ctx.get("model") or ""
    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {"ok": False, "error": str(e), "via": "hermes-resume", "engine": "hermes"}
    entry = {
        "pid": proc.pid,
        "name": f"resume-hermes-{str(session_id)[:8]}",
        "log": str(log_path),
        "prompt": text[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "resumed_sid": session_id,
        "fifo": None,
        "stdin_fd": None,
        "engine": "hermes",
        "cwd": cwd,
        "repo_path": repo_for_logs,
        "model": model,
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=entry["name"],
        log_path=log_path,
        cwd=cwd,
        spawned_at=timestamp,
        command_summary=text[:200],
        fifo=None,
        engine="hermes",
        session_id=session_id,
        repo_path=repo_for_logs,
        model=model,
    )
    return {
        "ok": True,
        "pid": proc.pid,
        "log": str(log_path),
        "resumed": True,
        "via": "hermes-resume",
        "engine": "hermes",
        "model": model,
    }
