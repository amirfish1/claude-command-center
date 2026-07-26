"""Extracted from server.py (originally lines 52830-53224).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import json
import re
import subprocess
import time

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Pkood agent orchestration
# ---------------------------------------------------------------------------

PKOOD_STATE_DIR = Path.home() / ".pkood" / "state"
PKOOD_LOGS_DIR = Path.home() / ".pkood" / "logs"
PKOOD_SOCKETS_DIR = Path.home() / ".pkood" / "sockets"
PKOOD_BIN = str(Path.home() / ".local" / "bin" / "pkood")

# Cache for pkood -> claude-session UUID links. Keyed by agent_id.
# Entry shape: {"link": <dict-or-None>, "meta_mtime": float, "cached_at": float}
# Invalidation: pkood state-file mtime change OR 60s TTL, whichever first.
_PKOOD_LINK_CACHE = {}
_PKOOD_LINK_TTL = 60.0

# Strip common ANSI CSI/OSC sequences from a byte or text buffer. Pkood
# logs are raw pty streams so the Claude banner is wrapped in colour escapes.
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?<>=]*[a-zA-Z]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)")


def _strip_ansi(s):
    s = _ANSI_OSC_RE.sub("", s)
    s = _ANSI_CSI_RE.sub("", s)
    return s


def _pkood_log_spawn_time(agent_id):
    """Best-effort spawn timestamp for a pkood agent.

    Uses the log file's birth time when available (macOS / APFS expose it via
    st_birthtime), falling back to mtime. The meta.json `timestamp` field is
    unreliable because pkood sometimes rewrites it on reconnect, whereas the
    log file is created once at spawn.
    """
    log = PKOOD_LOGS_DIR / f"{agent_id}.log"
    try:
        st = log.stat()
    except OSError:
        return None
    ts = getattr(st, "st_birthtime", None) or st.st_mtime
    return float(ts) if ts else None


def _pkood_log_header(agent_id, nbytes=8192):
    """Read + ANSI-strip the first `nbytes` of a pkood agent's log."""
    log = PKOOD_LOGS_DIR / f"{agent_id}.log"
    try:
        with open(log, "rb") as fh:
            raw = fh.read(nbytes)
    except OSError:
        return ""
    return _strip_ansi(raw.decode("utf-8", errors="replace"))


# Claude prints a remote-control URL in its startup banner:
#   https://claude.ai/code/session_<alphanum>
# The same token is recorded once in the corresponding .jsonl as a
# `bridge_status` event. Matching on it is far more reliable than a
# cwd+timestamp heuristic when multiple pkood agents share a cwd.
_BRIDGE_SESSION_RE = re.compile(r"claude\.ai/code/(session_[A-Za-z0-9]+)")


def _pkood_bridge_session_id(agent_id):
    """Extract claude's remote-control bridge session ID from the log banner."""
    text = _pkood_log_header(agent_id)
    if not text:
        return None
    m = _BRIDGE_SESSION_RE.search(text)
    return m.group(1) if m else None


def _pkood_log_cwd(agent_id):
    """Extract the cwd from a pkood agent's log file header.

    Claude Code prints the cwd right under its banner (e.g. "~/MyOfficeMgr"
    or an absolute path), typically on the third visible line. To avoid
    matching stray paths further down the log (prompts, tool output), we
    clip the text at the first horizontal rule the banner draws (a run of
    box-drawing ─ characters) and only search above it.
    """
    text = _pkood_log_header(agent_id, nbytes=4096)
    if not text:
        return None
    # Clip at the first horizontal rule the banner renders
    rule = re.search(r"─{10,}", text)
    header = text[: rule.start()] if rule else text[:400]
    for m in re.finditer(r"(~/[^\s\x00-\x1f,)]+|/[A-Za-z0-9._/-]+)", header):
        candidate = m.group(1).strip().rstrip(",.)")
        if candidate.startswith("//") or "://" in candidate:
            continue
        if candidate.startswith("~"):
            candidate = str(Path(candidate).expanduser())
        if Path(candidate).is_dir():
            return candidate
    return None


def _peek_jsonl_meta(path, max_lines=40):
    """Return (first_cwd, first_timestamp_epoch, bridge_session_id) from a
    claude .jsonl file.

    `bridge_session_id` comes from the `bridge_status` system event claude
    writes in the first few lines, matching the same token printed in its
    startup banner (which pkood captures). It's the most reliable shared
    identifier between the two sources.
    """
    cwd = None
    ts_epoch = None
    bridge_sid = None
    try:
        with open(path, "r") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cwd is None and ev.get("cwd"):
                    cwd = ev["cwd"]
                if ts_epoch is None and ev.get("timestamp"):
                    try:
                        # ISO-8601 with Z suffix (claude format)
                        t = ev["timestamp"].replace("Z", "+00:00")
                        ts_epoch = datetime.fromisoformat(t).timestamp()
                    except (ValueError, TypeError):
                        pass
                if (
                    bridge_sid is None
                    and ev.get("subtype") == "bridge_status"
                    and isinstance(ev.get("url"), str)
                ):
                    m = _BRIDGE_SESSION_RE.search(ev["url"])
                    if m:
                        bridge_sid = m.group(1)
                if cwd and ts_epoch and bridge_sid:
                    break
    except (OSError, UnicodeDecodeError):
        pass
    return cwd, ts_epoch, bridge_sid


def _resolve_claude_session_for_pkood(agent_id):
    """Link a pkood agent to its underlying claude-session UUID.

    Two-tier heuristic:
      1. Primary — bridge session ID match. Claude prints its remote-control
         URL (`https://claude.ai/code/session_...`) in the startup banner and
         also records it as a `bridge_status` event in its .jsonl. This token
         is per-process, so it's a unique shared identifier.
      2. Fallback — cwd + spawn-time window. When the bridge ID isn't
         available (older claude builds, /remote-control disabled), we
         match on the pkood log banner's cwd and the log file's birth time
         vs. the jsonl's first-event timestamp (±60s window, or ±15s when
         cwd is unknown).

    Returns {claude_session_id, claude_cwd, claude_jsonl} on success, else None.
    """
    spawn_cwd = _pkood_log_cwd(agent_id)
    spawn_ts = _pkood_log_spawn_time(agent_id)
    bridge_sid = _pkood_bridge_session_id(agent_id)

    # Choose candidate project dirs: the one encoded from spawn_cwd if we
    # have it, otherwise all of them (slower — but still bounded).
    candidate_dirs = []
    if spawn_cwd:
        slug = _core._encode_project_slug(spawn_cwd)
        candidate = _core.PROJECTS_ROOT / slug
        if candidate.is_dir():
            candidate_dirs.append(candidate)
    if not candidate_dirs and _core.PROJECTS_ROOT.is_dir():
        candidate_dirs = [p for p in _core.PROJECTS_ROOT.iterdir() if p.is_dir()]

    # Tighter timestamp window when cwd is unknown (reduces cross-repo
    # collisions when agents are spawned back-to-back).
    window = 60.0 if spawn_cwd else 15.0

    best_ts = None  # (abs_delta, path, cwd)
    for proj in candidate_dirs:
        for jsonl in proj.glob("*.jsonl"):
            jsonl_cwd, jsonl_ts, jsonl_bridge = _peek_jsonl_meta(jsonl)

            # Primary: bridge-id exact match wins outright.
            if bridge_sid and jsonl_bridge and jsonl_bridge == bridge_sid:
                return {
                    "claude_session_id": jsonl.stem,
                    "claude_cwd": jsonl_cwd or spawn_cwd,
                    "claude_jsonl": str(jsonl),
                }

            # Fallback: timestamp+cwd window. Only consider when we have a
            # spawn_ts (we always do unless the log is missing).
            if not spawn_ts or not jsonl_ts:
                continue
            if spawn_cwd and jsonl_cwd and jsonl_cwd != spawn_cwd:
                continue
            delta = abs(jsonl_ts - spawn_ts)
            if delta > window:
                continue
            if best_ts is None or delta < best_ts[0]:
                best_ts = (delta, jsonl, jsonl_cwd)

    # If the bridge-id scan didn't return, fall back to the best timestamp
    # match. Only use it when we had NO bridge id at all (i.e. we couldn't
    # check the primary signal); if we had a bridge id but no jsonl had it,
    # a timestamp match would likely be wrong — a fresh claude process
    # should always emit bridge_status.
    if bridge_sid:
        return None
    if not best_ts:
        return None
    _, path, jsonl_cwd = best_ts
    return {
        "claude_session_id": path.stem,
        "claude_cwd": jsonl_cwd or spawn_cwd,
        "claude_jsonl": str(path),
    }


def _cached_claude_session_for_pkood(agent_id):
    """Cached wrapper around _resolve_claude_session_for_pkood.

    Invalidates on: pkood meta-file mtime change OR 60s TTL.
    """
    meta_file = PKOOD_STATE_DIR / f"{agent_id}_meta.json"
    try:
        meta_mtime = meta_file.stat().st_mtime
    except OSError:
        meta_mtime = 0.0
    now = time.time()
    entry = _PKOOD_LINK_CACHE.get(agent_id)
    if (
        entry
        and entry["meta_mtime"] == meta_mtime
        and (now - entry["cached_at"]) < _PKOOD_LINK_TTL
    ):
        return entry["link"]
    link = _resolve_claude_session_for_pkood(agent_id)
    _PKOOD_LINK_CACHE[agent_id] = {
        "link": link,
        "meta_mtime": meta_mtime,
        "cached_at": now,
    }
    return link


def find_pkood_agents():
    """Scan ~/.pkood/state/*_meta.json and return unified session dicts."""
    if not PKOOD_STATE_DIR.is_dir():
        return []
    # Pkood cards share the same archive list as claude sessions — without
    # consulting it here, archive toggles on a pkood-* id would persist
    # but the rendered card would still show archived=False.
    archived_set, trashed_set = _core._load_conversation_lifecycle_sets(sweep=True)
    agents = []
    for meta_file in PKOOD_STATE_DIR.glob("*_meta.json"):
        try:
            data = json.loads(meta_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        agent_id = data.get("agent_id", meta_file.stem.replace("_meta", ""))
        target_dir = data.get("target_dir", "")
        update_ts = data.get("update_ts", 0)
        # Verify tmux session is actually alive — stale meta files can lie
        status = data.get("status", "")
        sock = PKOOD_SOCKETS_DIR / f"{agent_id}.sock"
        if status == "RUNNING" and sock.exists():
            try:
                probe = subprocess.run(
                    ["tmux", "-S", str(sock), "list-sessions"],
                    capture_output=True, timeout=2,
                )
                if probe.returncode != 0:
                    status = "DEAD"
            except (subprocess.TimeoutExpired, FileNotFoundError):
                status = "DEAD"
        elif status == "RUNNING":
            status = "DEAD"

        # Link to the underlying claude-session UUID. Pkood's meta.json
        # doesn't record the session id, so we reconcile by spawn-cwd +
        # spawn-time heuristic. When we find a match, the kanban can merge
        # the two cards (see find_all_sessions) so the user sees one card
        # per running agent instead of a pkood card AND a jsonl card.
        link = _cached_claude_session_for_pkood(agent_id) or {}
        # Prefer the resolved cwd when pkood meta didn't record one —
        # helps with cross-repo bucketing for pkood-spawned cards.
        resolved_cwd = link.get("claude_cwd") or target_dir

        agents.append({
            "id": f"pkood-{agent_id}",
            "session_id": f"pkood-{agent_id}",
            "display_name": agent_id,
            "first_message": data.get("command", ""),
            "last_prompt": (data.get("last_output_snippet") or "")[:200],
            "branch": "",
            "modified": update_ts,
            "modified_human": time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(update_ts)
            ) if update_ts else "",
            "size": 0,
            "source": "pkood",
            "session_cwd": resolved_cwd,
            "session_cwd_exists": bool(resolved_cwd and Path(resolved_cwd).is_dir()),
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "archived": (f"pkood-{agent_id}" in archived_set),
            "trashed": (f"pkood-{agent_id}" in trashed_set),
            "verified": False,
            "name_overridden": False,
            # Pkood-specific fields
            "pkood_status": status,  # RUNNING, IDLE, BLOCKED, DEAD
            "pkood_is_stuck": data.get("is_stuck", False),
            "is_live": status not in ("DEAD", ""),
            # Link back to the underlying claude-session so the kanban can
            # dedup / enrich the pkood card with jsonl transcript fields.
            "claude_session_id": link.get("claude_session_id"),
            "claude_jsonl": link.get("claude_jsonl"),
        })
    agents.sort(key=lambda x: x["modified"], reverse=True)
    return agents


def pkood_spawn(prompt, agent_id=None, target_dir=None, repo_path=None):
    """Spawn a pkood agent. Returns {ok, agent_id} or {ok: False, error}."""
    if not agent_id:
        agent_id = _core._slugify(prompt, max_len=30) or "agent"
    target_dir = target_dir or repo_path
    if not target_dir:
        return {"ok": False, "error": "repo_path or target_dir is required"}
    try:
        target_dir = _core._resolve_cwd_context(target_dir)["cwd"]
    except _core.RepoContextError as e:
        return e.as_payload()
    cmd = [PKOOD_BIN, "spawn", "--name", agent_id, "--dir", target_dir, prompt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"ok": True, "agent_id": agent_id}
        return {"ok": False, "error": (result.stderr or result.stdout or "unknown error").strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pkood spawn timed out"}
    except FileNotFoundError:
        return {"ok": False, "error": "pkood not found on PATH"}


def pkood_inject(agent_id, message):
    """Inject a message into a pkood agent."""
    cmd = [PKOOD_BIN, "inject", agent_id, message]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "error": (result.stderr or result.stdout or "unknown error").strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pkood inject timed out"}
    except FileNotFoundError:
        return {"ok": False, "error": "pkood not found on PATH"}


def pkood_kill(agent_id):
    """Kill a pkood agent."""
    cmd = [PKOOD_BIN, "kill", agent_id]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "error": (result.stderr or result.stdout or "unknown error").strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pkood kill timed out"}
    except FileNotFoundError:
        return {"ok": False, "error": "pkood not found on PATH"}


def pkood_tail(agent_id):
    """Get recent output from a pkood agent."""
    cmd = [PKOOD_BIN, "tail", agent_id]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"ok": True, "output": result.stdout}
        return {"ok": False, "error": (result.stderr or result.stdout or "unknown error").strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pkood tail timed out"}
    except FileNotFoundError:
        return {"ok": False, "error": "pkood not found on PATH"}

