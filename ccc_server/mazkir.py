"""Mazkir — the CCC Ask agent — and the `ccc-state` MCP server it talks to.

Two entry points, one stdlib-only file:

    python3 ccc_server/mazkir.py mcp [--base http://127.0.0.1:8090]
        stdio MCP server named `ccc-state`: read-only views of the fleet
        (census, live activity, throughput window, queue status, per-session
        detail, stuck/burning diagnostics) fetched from CCC's HTTP API.

    run_mazkir(question, history, range) -> (response dict, http status)
        The Ask tab pipeline (called from ask.handle_assistant_ask):
        1. pre-fetch candidate sessions from Claude-Index (`claude-index
           sessions --json`, ~1 s), excluding any currently-live session (it
           can't be "where the work already happened" — see `_live_ids`),
           and a one-line fleet snapshot;
        2. run headless Sonnet with two MCPs — `claude-index` (history) and
           `ccc-state` (live fleet) — and nothing else (no Bash/Write/Read);
        3. validate `[[session:ID]]` citations against the pre-fetched
           candidates plus a read-only lookup in the index, and return the
           same contract the Ask UI already renders.

The MCP half must stay importable when run as a plain script (no
`ccc_server.core`), so anything that needs CCC internals is imported lazily
inside run_mazkir().
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MCP_SERVER_NAME = "ccc-state"
MCP_SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

DEFAULT_BASE = "http://127.0.0.1:8090"
PORT_FILE = Path.home() / ".claude" / "command-center" / "port.txt"


def _resolve_index_bin() -> str | None:
    """The claude-index CLI: $CLAUDE_INDEX_BIN, else `claude-index` on PATH.

    None means session-history search is off; Ask still answers fleet
    questions and says history search is unavailable."""
    env = os.environ.get("CLAUDE_INDEX_BIN")
    if env:
        return env if os.access(env, os.X_OK) else None
    return shutil.which("claude-index")


INDEX_BIN = _resolve_index_bin()
INDEX_DB = os.environ.get("CLAUDE_INDEX_DB", str(Path.home() / ".claude-index" / "index.db"))
# Optional standing agenda. The daily_checkin tool is only offered to the
# model when this is configured or the default file exists.
CHECKIN_ENV = "CCC_DAILY_CHECKIN_FILE"
CHECKIN_DEFAULT = Path.home() / ".claude" / "command-center" / "daily-checkin.md"
CHECKIN_PATH = os.environ.get(CHECKIN_ENV) or str(CHECKIN_DEFAULT)


def checkin_enabled() -> bool:
    return bool(os.environ.get(CHECKIN_ENV)) or os.path.isfile(CHECKIN_PATH)
CLAUDE_BIN_FALLBACK = str(Path.home() / ".local" / "bin" / "claude")

MAZKIR_MODEL = os.environ.get("CCC_ASK_MODEL", "sonnet")
MAZKIR_MAX_TURNS = int(os.environ.get("CCC_ASK_MAX_TURNS", "8"))
MAZKIR_TIMEOUT_SEC = int(os.environ.get("CCC_ASK_TIMEOUT_SEC", "75"))
PREFETCH_TIMEOUT_SEC = 15
PREFETCH_LIMIT = 8
# Fan-out deadline is 1.5 s per peer; a little slack for thread scheduling.
PEER_PREFETCH_TIMEOUT_SEC = 2.5
HTTP_TIMEOUT_SEC = 12

STUCK_IDLE_SEC = 600          # live but transcript idle > 10 min
BURN_MULTIPLIER = 3.0         # tokens in the last 30 min > 3× fleet median
BURN_WINDOW_SEC = 1800
_CITE_RE = re.compile(r"\[\[session:([0-9a-zA-Z_.-]{6,})\]\]")
_ACTION_RE = re.compile(r"\[\[action:spawn-continue:([0-9a-zA-Z_.-]{6,})\]\]")


# ---------------------------------------------------------------------------
# CCC HTTP client
# ---------------------------------------------------------------------------

def resolve_base(base: str | None = None) -> str:
    if base:
        return base.rstrip("/")
    env = os.environ.get("CCC_BASE_URL")
    if env:
        return env.rstrip("/")
    try:
        port = PORT_FILE.read_text().strip()
        if port.isdigit():
            return f"http://127.0.0.1:{port}"
        if port.startswith(("http://127.0.0.1:", "http://localhost:")):  # write_port_file() form
            return port.rstrip("/")
    except OSError:
        pass
    return DEFAULT_BASE


def fetch_json(base: str, path: str, timeout: float = HTTP_TIMEOUT_SEC) -> dict:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (localhost)
        data = json.loads(resp.read().decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {"data": data}


def post_json(base: str, path: str, body: dict, timeout: float = HTTP_TIMEOUT_SEC) -> dict:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (localhost)
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            data = {"ok": False, "error": f"HTTP {e.code}"}
    return data if isinstance(data, dict) else {"data": data}


# ---------------------------------------------------------------------------
# Tool implementations (pure functions over fetched JSON — testable)
# ---------------------------------------------------------------------------

def _age(s) -> str:
    try:
        s = float(s)
    except (TypeError, ValueError):
        return "?"
    if s < 90:
        return f"{int(s)}s"
    if s < 5400:
        return f"{s / 60:.0f}m"
    if s < 172800:
        return f"{s / 3600:.1f}h"
    return f"{s / 86400:.1f}d"


def _census_row(s: dict) -> dict:
    return {
        "session_id": s.get("session_id"),
        "name": s.get("name") or s.get("session_name") or "",
        "state": s.get("state"),
        "engine": s.get("engine"),
        "model": s.get("model"),
        "repo": s.get("repo_path"),
        "last_event_age": _age(s.get("last_event_age_s")),
        "last_event_age_s": s.get("last_event_age_s"),
        "turn_age_s": s.get("turn_age_s"),
        "pending_tool": s.get("pending_tool"),
        "question_waiting": bool(s.get("question_waiting")),
        "needs_approval": bool(s.get("needs_approval")),
        "stuck": bool(s.get("stuck")),
        "parent": s.get("parent_session_id"),
        "spawned_via": s.get("spawned_via"),
        "children": len(s.get("children") or []) if isinstance(s.get("children"), list) else s.get("children"),
    }


def tool_list_sessions(census: dict, state: str | None = None, engine: str | None = None,
                       repo: str | None = None, limit: int = 40) -> dict:
    rows = [_census_row(s) for s in (census.get("sessions") or [])]
    if state:
        wanted = {x.strip().lower() for x in state.split(",") if x.strip()}
        rows = [r for r in rows if str(r.get("state") or "").lower() in wanted]
    if engine:
        rows = [r for r in rows if str(r.get("engine") or "").lower() == engine.lower()]
    if repo:
        rows = [r for r in rows if repo.lower() in str(r.get("repo") or "").lower()]
    rows.sort(key=lambda r: (r.get("last_event_age_s") if isinstance(r.get("last_event_age_s"), (int, float)) else 1e12))
    by_state: dict[str, int] = {}
    for s in census.get("sessions") or []:
        k = str(s.get("state") or "?")
        by_state[k] = by_state.get(k, 0) + 1
    return {"now": census.get("now"), "total": len(census.get("sessions") or []),
            "by_state": by_state, "sessions": rows[: max(1, int(limit))]}


def tool_live_activity(live: dict, session_id: str | None = None) -> dict:
    sessions = live.get("sessions") or {}
    if session_id:
        return {"session_id": session_id, "activity": sessions.get(session_id) or {}}
    out = {}
    for sid, a in sessions.items():
        if not isinstance(a, dict):
            continue
        if a.get("is_live") or a.get("pending_tool") or a.get("needs_approval") or a.get("question_waiting"):
            out[sid] = {k: a.get(k) for k in (
                "is_live", "sidecar_status", "is_compacting", "pending_tool",
                "stale_tool_call", "needs_approval", "question_waiting") if k in a}
    return {"live_count": len(out), "sessions": out}


# Activity-log verbs that explain why an inject didn't land (CCC-1214).
_INJECT_TROUBLE_RE = re.compile(r"REJECT|BLOCK|HELD|SKIP|FAIL|ERR|RECOVER|FORCE|WEDGE|DROP|TIMEOUT|MISS", re.I)


def tool_inject_diagnostics(sid: str, census_row: dict | None, live: dict | None,
                            receipt: dict | None, events: list | None) -> dict:
    """Why a session isn't accepting injected messages: the facts fleet_diagnostics
    doesn't carry (undelivered receipt, inject rejections/holds in activity.log)."""
    events = [e for e in (events or []) if isinstance(e, dict)]
    trouble = [e for e in events if _INJECT_TROUBLE_RE.search(str(e.get("verb") or ""))]
    live = live if isinstance(live, dict) else {}
    findings = []
    outstanding = (receipt or {}).get("outstanding")
    if outstanding:
        findings.append(f"undelivered inject {int(outstanding.get('age_s') or 0)}s old "
                        f"({outstanding.get('source') or '?'}): "
                        f"\"{str(outstanding.get('text_preview') or '')[:80]}\"; "
                        f"POST /api/session/{sid}/force-restart re-delivers it (only when the session is idle)")
    if census_row is None and not live:
        findings.append("CCC does not know this session id (wrong id, or not a CCC-visible session)")
    elif not live.get("is_live"):
        findings.append("no live process: an inject has to resume the session first")
    for key, why in (("pending_tool", "a tool call is still pending"),
                     ("needs_approval", "waiting on a permission approval"),
                     ("question_waiting", "waiting on an answer to its question"),
                     ("is_compacting", "compacting; input is held until it finishes")):
        if live.get(key):
            findings.append(why)
    seen = set()
    for e in reversed(trouble):
        verb = str(e.get("verb") or "")
        if verb in seen:
            continue
        seen.add(verb)
        m = re.search(r"(?:code|reason)=(\S+)", str(e.get("detail") or ""))
        findings.append(f"activity.log {verb}" + (f" ({m.group(1)})" if m else "") + f" at {e.get('ts')}")
    if not findings:
        findings.append("no delivery problem recorded: the last injects show no rejection, hold or "
                        "undelivered receipt")
    return {
        "session_id": sid,
        "findings": findings,
        "live": {k: live.get(k) for k in ("is_live", "state", "sidecar_status", "pending_tool",
                                          "needs_approval", "question_waiting", "is_compacting")
                 if k in live},
        "outstanding_inject": outstanding,
        "recent_inject_events": [{k: e.get(k) for k in ("ts", "category", "verb", "detail")}
                                 for e in events[-12:]],
    }


def tool_throughput(window: dict, limit: int = 20) -> dict:
    rows = []
    for s in (window.get("sessions") or [])[: max(1, int(limit))]:
        rows.append({k: s.get(k) for k in (
            "session_id", "session_name", "engine", "folder_path", "turns",
            "total_tokens", "output_tokens", "cost_usd", "models") if k in s})
    return {"scope": window.get("scope"), "totals": window.get("totals"),
            "session_count": window.get("session_count"), "sessions": rows}


def tool_queue_status(q: dict) -> dict:
    return {"projects": q.get("projects") or [], "ok": q.get("ok", True)}


def fleet_diagnostics(census: dict, live: dict, window30: dict, now: float | None = None) -> dict:
    """Stuck = live/busy but transcript idle > 10 min, waiting on a question
    or approval, or flagged stuck by CCC. Burning = tokens in the last 30 min
    > 3× the fleet median for sessions active in that window."""
    now = now or census.get("now") or time.time()
    live_map = live.get("sessions") or {}
    stuck, waiting = [], []
    for s in census.get("sessions") or []:
        sid = s.get("session_id")
        row = _census_row(s)
        a = live_map.get(sid) or {}
        idle = s.get("last_event_age_s")
        if "/command-center/scratch" in str(s.get("repo_path") or ""):
            continue  # CCC helper one-shots (titler, brief, Ask) are never "stuck"
        is_live = str(s.get("state") or "").lower() in ("live", "busy", "working", "running")
        reasons = []
        if s.get("stuck"):
            reasons.append(f"ccc flagged stuck ({_age(s.get('stuck_age_s'))})")
        if is_live and isinstance(idle, (int, float)) and idle > STUCK_IDLE_SEC:
            reasons.append(f"live but idle {_age(idle)}")
        if a.get("stale_tool_call") or s.get("pending_tool") and isinstance(s.get("turn_age_s"), (int, float)) and s["turn_age_s"] > STUCK_IDLE_SEC:
            reasons.append(f"tool call pending {_age(s.get('turn_age_s'))}: {s.get('pending_tool') or a.get('pending_tool')}")
        if s.get("question_waiting") or a.get("question_waiting"):
            waiting.append({**row, "waiting_on": "question"})
        elif s.get("needs_approval") or a.get("needs_approval"):
            waiting.append({**row, "waiting_on": "approval"})
        if reasons:
            stuck.append({**row, "reasons": reasons})

    burning = []
    per = [(s.get("session_id"), float(s.get("total_tokens") or 0), s)
           for s in (window30.get("sessions") or [])]
    active = [t for _, t, _ in per if t > 0]
    median = statistics.median(active) if active else 0.0
    threshold = median * BURN_MULTIPLIER if median else None
    for sid, tokens, s in per:
        if threshold and tokens > threshold and len(active) >= 3:
            burning.append({"session_id": sid, "session_name": s.get("session_name"),
                            "engine": s.get("engine"), "tokens_30m": int(tokens),
                            "x_median": round(tokens / median, 1), "cost_usd": s.get("cost_usd")})
    burning.sort(key=lambda r: -r["tokens_30m"])
    return {
        "checked_at": now,
        "sessions_total": len(census.get("sessions") or []),
        "stuck": stuck, "waiting": waiting, "burning": burning,
        "fleet_median_tokens_30m": int(median),
        "rules": {"stuck_idle_s": STUCK_IDLE_SEC, "burn_multiplier": BURN_MULTIPLIER},
    }


# ---------------------------------------------------------------------------
# Daily check-in agenda (optional markdown file, see CHECKIN_PATH)
# ---------------------------------------------------------------------------

CHECKIN_OPEN_STATES = ("open", "today", "active")


def parse_checkin(text: str, include_closed: bool = False) -> dict:
    """Parse the daily check-in agenda markdown into sections of items.

    Each `## N. Title` section holds a table `| # | Item | Status | Notes |`;
    rows whose status is done/dropped are skipped unless `include_closed`.
    The `## Discussion log` bullets are returned separately (last 8)."""
    sections, log = [], []
    cur = None
    in_log = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            title = line[3:].strip()
            in_log = title.lower().startswith("discussion log")
            cur = None if in_log else {"title": title, "items": []}
            if cur is not None:
                sections.append(cur)
            continue
        if in_log:
            if line.startswith("- "):
                log.append(line[2:].strip())
            continue
        if cur is None or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0] in ("#", "") or set(cells[0]) <= set("-: "):
            continue
        # First word only, so a hand-edited "today (ready)" still counts as today.
        status = (cells[2].lower().strip("` ").split() or [""])[0].strip("`")
        if status not in CHECKIN_OPEN_STATES and not include_closed:
            continue
        cur["items"].append({"id": cells[0], "item": cells[1], "status": status,
                             "notes": " | ".join(cells[3:])})
    sections = [sec for sec in sections if sec["items"]]
    open_count = sum(1 for sec in sections for it in sec["items"] if it["status"] in CHECKIN_OPEN_STATES)
    return {"sections": sections, "open_count": open_count, "discussion_log": log[-8:]}


def tool_daily_checkin(path: str = CHECKIN_PATH, include_closed: bool = False, reader=None) -> dict:
    read = reader or (lambda p: Path(p).read_text(encoding="utf-8"))
    try:
        text = read(path)
    except OSError as e:
        return {"path": path, "available": False, "error": f"{type(e).__name__}: {e}",
                "sections": [], "open_count": 0, "discussion_log": []}
    out = parse_checkin(text, include_closed=include_closed)
    out.update({"path": path, "available": True})
    return out


# Instinct brief (read-only; instinct.py is a sibling stdlib file)
BRIEF_DIR = Path(os.environ.get("CCC_INSTINCT_OUT_DIR",
                                str(Path.home() / ".claude" / "command-center" / "instinct")))


def tool_daily_brief(out_dir: Path | None = None, now: float | None = None) -> dict:
    """The newest Instinct brief, trimmed to what an answer needs."""
    d = Path(out_dir or BRIEF_DIR)
    files = sorted(d.glob("brief-*.json")) if d.is_dir() else []
    if not files:
        return {"available": False,
                "hint": "No brief yet. It is written by `python3 -m ccc_server.instinct brief` "
                        "(or its daily schedule)."}
    try:
        b = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}", "path": str(files[-1])}
    now = now or time.time()
    props = []
    for i, p in enumerate(b.get("proposals") or [], 1):
        if isinstance(p, dict):
            props.append({"n": i, "queue": p.get("queue"), "title": p.get("title"),
                          "text": str(p.get("text") or "")[:600], "priority": p.get("priority"),
                          "seen_before": p.get("seen_before"), "rationale": p.get("rationale")})
    return {
        "available": True, "path": str(files[-1]),
        "age_h": round((now - float(b.get("generated_ts") or now)) / 3600, 1),
        "headline": b.get("headline"), "totals": b.get("totals"),
        "stuck": [{k: s.get(k) for k in ("title", "detail", "severity", "source", "kind", "repo", "age", "ref", "session_id")
                   if s.get(k) is not None} for s in (b.get("stuck") or [])[:12] if isinstance(s, dict)],
        "next": (b.get("next") or [])[:8],
        "proposals": props[:10],
        "blind_spots": b.get("blind_spots") or [],
    }


# ---------------------------------------------------------------------------
# MCP stdio server (JSON-RPC 2.0, newline-delimited)
# ---------------------------------------------------------------------------

TOOLS = [
    {"name": "list_sessions",
     "description": "Sessions CCC currently tracks (all engines): state, engine, model, repo, "
                    "how long since the last transcript event, pending tool, waiting flags, "
                    "parent/children. Counts by state included. Filter by state (comma list), "
                    "engine, or repo substring.",
     "inputSchema": {"type": "object", "properties": {
         "state": {"type": "string", "description": "Comma list of states to keep, e.g. 'live,busy'."},
         "engine": {"type": "string", "description": "claude | codex | kimi | gemini | antigravity | grok"},
         "repo": {"type": "string", "description": "Substring of the repo path."},
         "limit": {"type": "integer", "default": 40}}}},
    {"name": "live_activity",
     "description": "Per-session live flags: is_live, sidecar_status, is_compacting, pending_tool, "
                    "stale_tool_call, needs_approval, question_waiting. Without session_id returns "
                    "only sessions that are live or waiting on something.",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}}}},
    {"name": "throughput_window",
     "description": "Token/cost throughput per session over a recent window: turns, total/output "
                    "tokens, cost, models. Use hours=0.5 for 'right now', 24 for today.",
     "inputSchema": {"type": "object", "properties": {
         "hours": {"type": "number", "default": 24},
         "engine": {"type": "string"},
         "limit": {"type": "integer", "default": 20}}}},
    {"name": "queue_status",
     "description": "OPS queue per project: depth, oldest open age, fixer session and whether it is live/stuck.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "session_detail",
     "description": "Everything CCC knows about one session right now: census row + live activity + "
                    "its throughput in the last 24h. Pair with claude-index session_info for the transcript.",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}}, "required": ["session_id"]}},
    {"name": "fleet_diagnostics",
     "description": "Which sessions look stuck (live but idle >10 min, pending tool, flagged by CCC), "
                    "which are waiting on a question/approval, and which are burning tokens "
                    "(>3× fleet median over the last 30 min). Call this first for 'is anything "
                    "stuck / burning / waiting on me?'.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "inject_diagnostics",
     "description": "Why a session is not accepting injected/sent messages: an undelivered inject "
                    "receipt, inject rejections/holds/blocks from CCC's activity.log (e.g. "
                    "INJECT_REJECT repo_not_allowed, BLOCKED repeat, Q_HELD headless_turn), and "
                    "live blockers. Call this when the user says a session won't take a message; "
                    "fleet_diagnostics does not show these.",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}}, "required": ["session_id"]}},
    {"name": "daily_checkin",
     "description": "The user's standing daily check-in agenda (a markdown file): open items "
                    "grouped by section with ids, status and notes, plus the recent discussion "
                    "log. Call this for 'daily check-in', 'morning review', 'what's on my agenda', "
                    "'what should we discuss'.",
     "inputSchema": {"type": "object", "properties": {
         "include_closed": {"type": "boolean", "default": False,
                            "description": "Also return done/dropped items."}}}},
    {"name": "daily_brief",
     "description": "The newest proactive Instinct brief: what changed across repos, what is stuck "
                    "or waiting on the user, ranked next actions, and numbered proposed tickets "
                    "(dry run; nothing filed). Call for 'daily brief', 'what happened overnight', "
                    "'what's stuck', or before filing a brief proposal.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "propose_spawn_session",
     "description": "PROPOSE starting a new agent session (also how git work like a push gets done). "
                    "Does not start anything: the user must click Confirm in CCC. Returns an action_id to cite as [[action:confirm:ID]].",
     "inputSchema": {"type": "object", "properties": {
         "cwd": {"type": "string", "description": "Absolute repo/work directory."},
         "prompt": {"type": "string", "description": "The first message for the new session."},
         "engine": {"type": "string", "default": "claude"},
         "model": {"type": "string"},
         "name": {"type": "string"},
         "reason": {"type": "string", "description": "One line: why."}},
         "required": ["cwd", "prompt"]}},
    {"name": "propose_inject",
     "description": "PROPOSE sending a message into an existing session (steer it). Nothing is sent "
                    "until the user confirms. Returns an action_id to cite as [[action:confirm:ID]].",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}, "text": {"type": "string"},
         "reason": {"type": "string"}}, "required": ["session_id", "text"]}},
    {"name": "propose_wt_add",
     "description": "PROPOSE filing a WatchTower ticket. Nothing is filed until the user confirms. "
                    "Returns an action_id to cite as [[action:confirm:ID]].",
     "inputSchema": {"type": "object", "properties": {
         "queue": {"type": "string"}, "title": {"type": "string"}, "text": {"type": "string"},
         "priority": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"},
         "reason": {"type": "string"}}, "required": ["queue", "title", "text"]}},
    {"name": "propose_wt_comment",
     "description": "PROPOSE commenting on a WatchTower ticket (e.g. OPS-12). Nothing is posted "
                    "until the user confirms. Returns an action_id to cite as [[action:confirm:ID]].",
     "inputSchema": {"type": "object", "properties": {
         "ref": {"type": "string"}, "text": {"type": "string"},
         "reason": {"type": "string"}}, "required": ["ref", "text"]}},
]

PROPOSE_TOOLS = {"propose_spawn_session": "spawn_session", "propose_inject": "inject",
                 "propose_wt_add": "wt_add", "propose_wt_comment": "wt_comment"}


class CccState:
    """Tool dispatcher; `fetch` is injectable for tests."""

    def __init__(self, base: str | None = None, fetch=None, post=None):
        self.base = resolve_base(base)
        self._fetch = fetch or (lambda path: fetch_json(self.base, path))
        self._post = post or (lambda path, body: post_json(self.base, path, body))

    def get(self, path: str) -> dict:
        return self._fetch(path)

    def _window(self, hours: float, engine: str | None = None, limit: int = 20) -> dict:
        end = int(time.time())
        start = end - int(float(hours) * 3600)
        q = f"/api/throughput/window?start={start}&end={end}&limit={int(limit)}"
        if engine:
            q += f"&engine={engine}"
        return self.get(q)

    def call(self, name: str, args: dict) -> dict:
        args = args or {}
        if name == "list_sessions":
            return tool_list_sessions(self.get("/api/sessions/census"), args.get("state"),
                                      args.get("engine"), args.get("repo"), args.get("limit", 40))
        if name == "live_activity":
            return tool_live_activity(self.get("/api/sessions/live-activity"), args.get("session_id"))
        if name == "throughput_window":
            return tool_throughput(self._window(args.get("hours", 24), args.get("engine"),
                                                args.get("limit", 20)), args.get("limit", 20))
        if name == "queue_status":
            return tool_queue_status(self.get("/api/queue/status"))
        if name == "session_detail":
            sid = str(args.get("session_id") or "")
            census = self.get("/api/sessions/census")
            row = next((s for s in census.get("sessions") or [] if s.get("session_id") == sid), None)
            live = (self.get("/api/sessions/live-activity").get("sessions") or {}).get(sid)
            tp = next((s for s in self._window(24, limit=500).get("sessions") or []
                       if s.get("session_id") == sid), None)
            if row is None and live is None and tp is None:
                return {"session_id": sid, "known": False}
            return {"session_id": sid, "known": True, "census": row, "live": live, "throughput_24h": tp}
        if name == "inject_diagnostics":
            sid = str(args.get("session_id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
                return {"session_id": sid, "error": "session_id must be a full session id"}
            census = self.get("/api/sessions/census")
            row = next((x for x in census.get("sessions") or [] if x.get("session_id") == sid), None)
            live = (self.get("/api/sessions/live-activity").get("sessions") or {}).get(sid)
            receipt = self.get(f"/api/session/{sid}/inject-receipt")
            events = self.get(f"/api/activity-log?session_id={sid}&limit=40").get("events")
            return tool_inject_diagnostics(sid, row, live, receipt, events)
        if name == "daily_checkin":
            if not checkin_enabled():
                raise KeyError(name)
            return tool_daily_checkin(include_closed=bool(args.get("include_closed")))
        if name == "daily_brief":
            return tool_daily_brief()
        if name in PROPOSE_TOOLS:
            # Proposal only: CCC stores it and keeps the confirm token to itself.
            params = {k: v for k, v in args.items() if k != "reason"}
            return self._post("/api/assistant/actions/propose",
                              {"kind": PROPOSE_TOOLS[name], "params": params,
                               "reason": str(args.get("reason") or "")})
        if name == "fleet_diagnostics":
            census = self.get("/api/sessions/census")
            notes = []
            try:
                live = self.get("/api/sessions/live-activity")
            except (urllib.error.URLError, OSError, ValueError) as e:
                live, notes = {}, notes + [f"live-activity unavailable: {type(e).__name__}"]
            try:
                win = self._window(BURN_WINDOW_SEC / 3600, limit=500)
            except (urllib.error.URLError, OSError, ValueError) as e:
                win, notes = {}, notes + [f"throughput unavailable: {type(e).__name__}"]
            out = fleet_diagnostics(census, live, win)
            if notes:
                out["notes"] = notes
            return out
        raise KeyError(name)


def available_tools() -> list[dict]:
    """TOOLS minus the ones whose backing data this machine doesn't have."""
    return [t for t in TOOLS if t["name"] != "daily_checkin" or checkin_enabled()]


def handle_request(state: CccState, req: dict) -> dict | None:
    """One JSON-RPC request -> response dict (None for notifications)."""
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION}}}
    if method in ("notifications/initialized", "notifications/cancelled") or (rid is None and method and method.startswith("notifications/")):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": available_tools()}}
    if method == "tools/call":
        name = params.get("name")
        try:
            data = state.call(name, params.get("arguments") or {})
            text = json.dumps(data, ensure_ascii=False, default=str)
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}}
        except KeyError:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"ccc-state error: {type(e).__name__}: {e}"}],
                "isError": True}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve_stdio(base: str | None = None) -> None:
    state = CccState(base)
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        reqs = req if isinstance(req, list) else [req]
        for r in reqs:
            resp = handle_request(state, r) if isinstance(r, dict) else None
            if resp is not None:
                out.write(json.dumps(resp, ensure_ascii=False) + "\n")
                out.flush()


# ---------------------------------------------------------------------------
# Mazkir: the Ask agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Mazkir, the memory and fleet assistant inside Claude Command Center (CCC).
You answer questions about the user's past work (across Claude Code, Codex, Kimi, Antigravity sessions and pulled Gmail threads) and about the live agent fleet.

Tools:
- claude-index: search_sessions (find which sessions are about X), search (specific facts/strings), session_info (confirm a session, see how it ended), show_message, recent_sessions.
- ccc-state: fleet_diagnostics (stuck / waiting / burning), list_sessions, live_activity, throughput_window, queue_status, session_detail, inject_diagnostics (why a session won't accept a message).
- ccc-state (brief): daily_brief (the proactive morning brief: changes, stuck items, numbered proposed tickets).
- ccc-state (actions, propose only): propose_spawn_session, propose_inject, propose_wt_add, propose_wt_comment. These never act; the user confirms in CCC.

Method:
1. CANDIDATES are pre-fetched below with excerpts from their best-matching messages, best match first, with currently-live sessions already excluded (a session still open right now cannot be where past work "already happened" — it's likely the very session asking). If they answer the question, answer immediately without any tool call (each tool round trip costs ~4 s); call session_info only when the excerpts do not say what was decided or how it ended.
2. Otherwise call search_sessions once (rephrase with 2-4 topic words), then at most one or two follow-ups. Never loop.
3. Candidates marked machine=NAME were found on another of the user's CCC machines (e.g. the VM), not this one. Cite them the same way and say which machine the work happened on.
4. Trust the candidate order: it already ranks by relevance with only a small recency tie-break, and demotes planning-only/self-referential sessions. Don't override it just because a lower-ranked candidate is more recent.
5. For fleet questions (stuck, burning, waiting, what is running, cost) call fleet_diagnostics or the specific tool once.
6. "This session", "the session on (the) screen", "this one": the ON SCREEN session named in the prompt. Use its id; never guess another. If the user says it won't take a message, call inject_diagnostics on it and report its findings.
7. Be honest: if nothing matches, say what you searched and that you found nothing.
8. For a daily brief / "what happened overnight", call daily_brief once and lead with its headline, then stuck items, then the numbered proposals.
9. Acting: you cannot start, send, file, or post anything yourself. When the user asks you to (start a session, steer or message a session, file or comment on a ticket, "file proposal 2"), call the matching propose_* tool once with complete parameters, put [[action:confirm:ACTION_ID]] on its own line, and say it is waiting for their Confirm. Never say it was done. Don't propose actions the user did not ask for.
10. Git work ("push bym", commit, merge, open a PR): don't say you can't. Call propose_spawn_session once with cwd = that repo's absolute path (take it from a candidate's or list_sessions' cwd; if unsure, ask which repo) and a prompt asking the session to do exactly that, following the repo's own git rules. Never ask it to force-push or skip hooks.

Answer format (plain text, no markdown headers):
- Lead with the answer in one or two sentences, then 1-4 short supporting lines.
- When the answer spans several threads of work, make it a numbered list: each item starts with a **bold 2-5 word topic**, then one short line, then its citations and date at the end of that same line.
- Skip evaluation/test runs (prompts marked "Evaluation run") unless the user asks about them. Treat repeated runs of the same loop or worker prompt as one thread and cite its most recent run once.
- Skip WatchTower worker sessions (queue drains like "Drain the X WatchTower queue", ticket fixers, planners, verifiers) unless the user asks about workers or queues; the user nearly always means work they did directly.
- Name up to 3 distinct sessions that actually did the work (skip near-duplicates like a continuation of a session you already named), each with its date, ranked best match first — not just the most recent.
- Cite every session you rely on inline as [[session:SESSION_ID]] using the exact id from the tool output or the candidate list. Cite Gmail threads the same way with the thread id.
- Give dates as YYYY-MM-DD and name the harness (Claude, Codex, Kimi, Antigravity, Gmail) when it is not Claude.
- If a session should be resumed to continue that work, append [[action:spawn-continue:SESSION_ID]] on its own line.
- Keep the whole answer under 110 words."""

_CHECKIN_PROMPT = """

Daily check-in: ccc-state also has daily_checkin (the user's standing agenda). For a daily check-in / morning review / "what should we discuss", call it once, then walk every open item by section as "id — item — one-line status or note", lead with the "today" items, and end by asking which item to pull in first. The 110-word cap does not apply to that answer."""

_NO_INDEX_PROMPT = """

claude-index is not installed on this machine, so the search_sessions/search/session_info tools do not exist. CANDIDATES come from CCC's built-in keyword search (titles and short snippets only) and you cannot search further. Answer from them when they fit; otherwise say what was searched and that nothing matched, and mention once that installing claude-index enables deeper history search. Never invent past sessions."""


def system_prompt(index_bin: str | None = INDEX_BIN) -> str:
    return SYSTEM_PROMPT + (_CHECKIN_PROMPT if checkin_enabled() else "") + ("" if index_bin else _NO_INDEX_PROMPT)


def _range_to_since(range_key) -> str | None:
    return {"24h": "1d", "7d": "7d", "30d": "30d"}.get(str(range_key or "any").lower())


def prefetch_sessions(question: str, since: str | None, runner=None, index_bin: str | None = INDEX_BIN,
                      limit: int = PREFETCH_LIMIT, exclude_session_ids=None) -> list[dict]:
    if not index_bin:
        return []
    argv = [index_bin, "sessions", question, "--json", "-n", str(limit), "--excerpts", "3"]
    if since:
        argv += ["--since", since]
    for sid in exclude_session_ids or ():
        argv += ["--exclude-session", sid]
    run = runner or (lambda a, **kw: subprocess.run(a, capture_output=True, text=True, **kw))
    try:
        proc = run(argv, timeout=PREFETCH_TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if getattr(proc, "returncode", 1) != 0:
        return []
    try:
        data = json.loads(proc.stdout or "[]")
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict) and d.get("session_id")] if isinstance(data, list) else []


PEER_PREFETCH_LIMIT = 5


def peer_prefetch(question: str, limit: int = PEER_PREFETCH_LIMIT, fan_out=None) -> tuple[list[dict], list[dict]]:
    """Candidates from paired CCC machines (multi-machine S4): each peer's
    own local recall, asked in parallel under the fan-out deadline. Returns
    (candidates, peer statuses). No peers, or any failure, means ([], ...):
    another machine must never break the local answer."""
    try:
        if fan_out is None:
            import federation
            if not federation.load_peers():
                return [], []
            from ccc_server import memory_fanout as _mf
            fan_out = _mf.fan_out
        entries = fan_out("memory_recall", {"q": question[:500], "limit": limit})
    except Exception:
        return [], []
    cands: list[dict] = []
    statuses: list[dict] = []
    for e in entries:
        statuses.append({"name": e.get("name"), "status": e.get("status"),
                         "stale": bool(e.get("stale")), "rows": 0})
        for row in ((e.get("result") or {}).get("results") or [])[:limit]:
            sid = row.get("session_id") if isinstance(row, dict) else None
            if not sid:
                continue
            match = row.get("match") if isinstance(row.get("match"), dict) else {}
            date = str(row.get("date") or "")
            cands.append({
                "session_id": sid,
                "title": row.get("title") or "",
                "snippet": match.get("snippet") or row.get("snippet") or "",
                "first_ts": date,
                "last_ts": date,
                "cwd": row.get("repo") or "",
                "node": e.get("name") or "",
                "node_id": e.get("node_id") or "",
            })
            statuses[-1]["rows"] += 1
    return cands, statuses


def builtin_prefetch(question: str, range_key: str | None, exclude_session_ids=None,
                     limit: int = PREFETCH_LIMIT, search_recent=None, search_history=None,
                     enrich=None) -> list[dict]:
    """Candidates from CCC's own session search, for machines without
    claude-index. Same warm, cached searches the legacy Ask path uses."""
    try:
        from ccc_server import ask as _ask
        if search_recent is None or search_history is None:
            from ccc_server import core as _core
            search_recent = search_recent or _core.search_recent_sessions
            search_history = search_history or _core.search_conversation_history
    except Exception:
        return []
    query = " ".join(_ask.extract_ask_terms(question))
    if not query:
        return []
    days, since = _ask._ask_range_window(range_key)
    try:
        recent = (search_recent(query, days=days, limit=limit * 2) or {}).get("results") or []
    except Exception:
        recent = []
    try:
        hist = (search_history(query, limit=limit * 2, since=since) or {}).get("results") or []
    except Exception:
        hist = []
    try:
        hits = (enrich or _ask.enrich_ask_hits)(_ask.merge_ask_hits(recent, hist, cap=limit * 2))
    except Exception:
        return []
    skip = set(exclude_session_ids or ())
    out = []
    for h in hits:
        if h.get("id") in skip:
            continue
        ts = h.get("ts_unix")
        day = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""
        out.append({"session_id": h["id"], "title": h.get("title") or "", "cwd": h.get("cwd") or "",
                    "best_snippet": h.get("snippet") or "", "first_ts": day, "last_ts": day,
                    "ts_unix": ts, "harness": "claude"})
    return out[:limit]


SNAPSHOT_TIMEOUT_SEC = 2.0  # the snapshot is a nicety; Mazkir can call ccc-state itself


def fleet_snapshot(base: str | None = None, fetch=None) -> str:
    try:
        if fetch is None:
            b = resolve_base(base)
            fetch = lambda path: fetch_json(b, path, timeout=SNAPSHOT_TIMEOUT_SEC)  # noqa: E731
        census = CccState(base, fetch=fetch).get("/api/sessions/census")
    except Exception:
        return "fleet: (CCC census unavailable)"
    by_state: dict[str, int] = {}
    waiting = 0
    for s in census.get("sessions") or []:
        k = str(s.get("state") or "?")
        by_state[k] = by_state.get(k, 0) + 1
        if s.get("question_waiting") or s.get("needs_approval"):
            waiting += 1
    parts = ", ".join(f"{k}={v}" for k, v in sorted(by_state.items()))
    return f"fleet: {len(census.get('sessions') or [])} sessions ({parts}); waiting on you: {waiting}"


def _fmt_candidate(i: int, s: dict) -> str:
    first = (s.get("first_ts") or "")[:10]
    last = (s.get("last_ts") or "")[:10]
    when = first if first == last or not last else f"{first}..{last}"
    title = " ".join(str(s.get("title") or "").split())[:140]
    snip = " ".join(str(s.get("best_snippet") or s.get("snippet") or "").split())[:220]
    runs = f" runs={s['runs']}" if (s.get("runs") or 1) > 1 else ""
    node = f" machine={s['node']}" if s.get("node") else ""
    out = (f"{i}. [[session:{s['session_id']}]] {s.get('harness') or 'claude'} {when} "
           f"hits={s.get('hits', '?')}{runs}{node} cwd={s.get('cwd') or '?'}\n"
           f"   title: {title}\n   match: {snip}")
    for e in (s.get("excerpts") or [])[:3]:
        if not isinstance(e, dict):
            continue
        text = " ".join(str(e.get("text") or "").split())[:400]
        out += f"\n   excerpt ({e.get('type') or '?'} {(e.get('timestamp') or '')[:10]}): {text}"
    return out


def focused_line(focused) -> str:
    """The session the user has open in CCC right now (CCC-1214), or ''."""
    if not isinstance(focused, dict):
        return ""
    sid = str(focused.get("session_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", sid):
        return ""
    title = " ".join(str(focused.get("title") or "").split())[:120]
    return (f"ON SCREEN: the user is viewing session {sid}" + (f' ("{title}")' if title else "")
            + " in CCC. \"This session\" / \"the session on the screen\" means this one.")


def build_prompt(question: str, history: list, candidates: list[dict], snapshot: str,
                 range_key: str | None, index_available: bool = True, focused=None) -> str:
    lines = []
    if history:
        lines.append("Earlier in this conversation:")
        for turn in history[-4:]:
            if isinstance(turn, dict):
                q = " ".join(str(turn.get("q") or "").split())[:300]
                a = " ".join(str(turn.get("a") or "").split())[:300]
                if q:
                    lines.append(f"Q: {q}")
                if a:
                    lines.append(f"A: {a}")
        lines.append("")
    lines.append(f"Today: {time.strftime('%Y-%m-%d')}. Time range filter: {range_key or 'any'}.")
    lines.append(snapshot)
    on_screen = focused_line(focused)
    if on_screen:
        lines.append(on_screen)
    lines.append("")
    if candidates:
        src = "claude-index search_sessions" if index_available else "CCC's built-in session search"
        lines.append(f"CANDIDATES (pre-fetched from {src}, best first):")
        for i, s in enumerate(candidates, 1):
            lines.append(_fmt_candidate(i, s))
    elif not index_available:
        lines.append("CANDIDATES: none (CCC's built-in keyword search found nothing; claude-index is not installed).")
    else:
        lines.append("CANDIDATES: none pre-fetched (index search found nothing for the literal question).")
    lines.append("")
    lines.append(f"QUESTION: {question}")
    return "\n".join(lines)


def mcp_config(base: str, index_bin: str | None = INDEX_BIN) -> str:
    servers = {}
    if index_bin:
        servers["claude-index"] = {"command": index_bin, "args": ["mcp", "--lite"]}
    servers[MCP_SERVER_NAME] = {"command": sys.executable,
                                "args": [os.path.abspath(__file__), "mcp", "--base", base]}
    return json.dumps({"mcpServers": servers})


# Every built-in Claude Code tool is off (`--tools ""`); the deny list is
# belt and braces. It matters more for the warm process: a Cron/ScheduleWakeup
# prompt could otherwise fire inside the next user's Ask. `ingest` is the one
# write tool the index MCP exposes.
_DISALLOWED = ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "Read", "Glob", "Grep", "LS",
               "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "CronCreate", "CronDelete",
               "CronList", "ScheduleWakeup", "Monitor", "SendMessage", "Workflow", "Skill",
               "RemoteTrigger", "PushNotification", "EnterWorktree", "ExitWorktree",
               "mcp__claude-index__ingest")


def mazkir_argv(claude_bin: str, base: str, session_id: str | None, model: str = MAZKIR_MODEL,
                index_bin: str | None = INDEX_BIN) -> list[str]:
    """One-shot argv. `session_id=None` lets the CLI pick one (the warm
    process gets a new id per /clear and reports each on its init event)."""
    return [
        claude_bin, "-p",
        "--model", model,
        *(["--session-id", session_id] if session_id else []),
        "--setting-sources", "project",       # skip user hooks: 13 s -> 3 s round trip
        "--output-format", "json",
        "--permission-mode", "dontAsk",
        "--max-turns", str(MAZKIR_MAX_TURNS),
        "--system-prompt", system_prompt(index_bin),
        "--mcp-config", mcp_config(base, index_bin),
        "--strict-mcp-config",
        "--tools", "",
        "--allowedTools", *(["mcp__claude-index"] if index_bin else []), f"mcp__{MCP_SERVER_NAME}",
        "--disallowedTools", *_DISALLOWED,
    ]


def parse_result(stdout: str) -> dict:
    text = (stdout or "").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return {"answer": text, "num_turns": None, "cost_usd": None, "is_error": False}
    if isinstance(data, dict):
        return {"answer": str(data.get("result") or "").strip(), "num_turns": data.get("num_turns"),
                "cost_usd": data.get("total_cost_usd"), "is_error": bool(data.get("is_error")),
                "duration_ms": data.get("duration_ms"), "claude_session_id": data.get("session_id"),
                "usage": data.get("usage"), "model_usage": data.get("modelUsage")}
    return {"answer": text, "num_turns": None, "cost_usd": None, "is_error": False}


def turn_usage(res: dict) -> dict | None:
    """Per-answer token usage, cache-adjusted the same way as the throughput views.

    The result event's `usage` is per turn even in the warm multi-turn
    process (`total_cost_usd` there is cumulative, so it can't be reused).
    """
    usage = res.get("usage")
    if not isinstance(usage, dict):
        return None
    flat = dict(usage)
    # The 5m/1h cache-write split is nested; the normalizer reads top-level keys.
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"):
            if key in split:
                flat[key] = split[key]
    model_usage = res.get("model_usage")
    model = next(iter(model_usage), "") if isinstance(model_usage, dict) and model_usage else MAZKIR_MODEL
    from ccc_server.usage_stats import _throughput_normalize_usage
    try:
        norm = _throughput_normalize_usage(flat, engine="claude", model=model)
    except Exception:  # a usage footer must never fail the answer
        return None
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_adjusted_tokens": int(round(norm["effective_total_tokens"])),
        "model": model,
    }


def _harness_from_project_dir(pd: str | None) -> str:
    return {"_codex": "codex", "_kimi": "kimi", "_antigravity": "antigravity", "_gmail": "gmail"}.get(pd or "", "claude")


def _first_user_message(conn, sid: str) -> str:
    t = conn.execute(
        "SELECT content FROM messages WHERE session_id=? AND type='user' "
        "AND LTRIM(content) NOT LIKE '<%' ORDER BY ts_unix LIMIT 1", (sid,)).fetchone()
    # Generous cap: an injected preamble can run past the index's 200-char
    # title before the real ask starts; clean_title strips it.
    return " ".join(str(t[0] if t else "")[:2000].split())


def full_titles(ids: list[str], db_path: str = INDEX_DB) -> dict[str, str]:
    """First user message per session, for index titles cut off inside a preamble."""
    if not ids:
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            return {sid: t for sid in ids if (t := _first_user_message(conn, sid))}
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


def lookup_sessions(ids: list[str], db_path: str = INDEX_DB) -> dict[str, dict]:
    """Read-only lookup for cited ids that were not in the pre-fetched set."""
    if not ids:
        return {}
    out: dict[str, dict] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            for sid in ids:
                r = conn.execute(
                    "SELECT session_id, cwd, project_dir, first_ts, last_ts, message_count, title "
                    "FROM sessions WHERE session_id=?", (sid,)).fetchone()
                if r is None:
                    continue
                d = dict(r)
                if not d.get("title") or _needs_full_title(d["title"]):
                    d["title"] = _first_user_message(conn, sid) or d.get("title") or ""
                d["harness"] = _harness_from_project_dir(d.get("project_dir"))
                out[sid] = d
        finally:
            conn.close()
    except sqlite3.Error:
        return out
    return out


def _ts_unix(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# CCC injects these preambles into prompts; as a source label they bury the ask.
_PREAMBLE_RES = (
    re.compile(r"^Heads-up: this may already be shipped:.*?Verify before rebuilding\.\s*", re.I),
    re.compile(r"^Heads-up: this may already be shipped:\s*", re.I),
    re.compile(r"^continu(?:e|ing) (?:session )?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}[\s:,.-]*", re.I),
)
_CONTINUE_RE = re.compile(r"^Continue (?:the work from )?session [0-9a-f-]{6,}\S*\s*\((['\"])(.+?)\1\).*", re.I)
_EVAL_RE = re.compile(r"\bEvaluation run\s+r?\d+", re.I)
TITLE_MAX = 80


def clean_title(title: str | None, limit: int = TITLE_MAX) -> str:
    """A short human label for a session: drop injected preambles, name a
    continuation after what it continues, cut at a word boundary."""
    t = " ".join(str(title or "").split())
    for rx in _PREAMBLE_RES:
        t = rx.sub("", t)
    m = _CONTINUE_RE.match(t)
    if m:
        t = "Continue: " + m.group(2)
    if len(t) > limit:
        cut = t[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-(")
        t = (cut or t[: limit - 1]) + "…"
    return t


def _needs_full_title(title: str | None) -> bool:
    return any(rx.match(" ".join(str(title or "").split())) for rx in _PREAMBLE_RES)


def is_eval_run(s: dict) -> bool:
    return bool(_EVAL_RE.search(f"{s.get('title') or ''} {s.get('best_snippet') or ''}"))


# WatchTower worker sessions (the sidebar's Workers tab). Asks are almost always
# about work the user did directly, so these are hidden unless the question is
# about workers/queues. Title shapes mirror app.js _WT_LAUNCH_PROMPT_RE and
# server._infer_session_spawned_via; the ledgers catch everything else.
_WT_WORKER_TITLE_RE = re.compile(
    r"^(?:🧵\s*)?(?:drain the [a-z0-9_]+(?:-[a-z0-9_]+)* watchtower queue\b"
    r"|fix ticket \S+ on the \S+ watchtower queue\b"
    r"|you are the planner for watchtower ticket "
    r"|you are an independent (?:plan reviewer|verifier) for watchtower ticket "
    r"|you are the independent post-fix assessor for the bug ticket "
    r"|lane-[we]|wt-|.*\[(?:watchtower|wt)\])", re.I)
_WORKER_QUESTION_RE = re.compile(r"\b(?:workers?|watchtower|queues?)\b", re.I)


def is_worker_session(c: dict, worker_ids=None, title: str | None = None) -> bool:
    if worker_ids and c.get("session_id") in worker_ids:
        return True
    return any(_WT_WORKER_TITLE_RE.match(" ".join(str(t or "").split()))
               for t in (c.get("title"), title) if t)


def worker_session_ids(ids) -> set:
    """The subset of `ids` that WatchTower or a spawn marker records as a
    worker: the worker-sessions ledger, the session-origins ledger, and
    workers-lane spawn markers. Per-candidate marker reads only, so the cost
    is the candidate count, not the session count. Empty outside CCC."""
    want = {i for i in ids or () if i}
    if not want:
        return set()
    try:
        from ccc_server import core as _core
        found = want & (set(_core._wt_read_worker_session_ids()) | set(_core._wt_read_session_origins()))
        for sid in want - found:
            m = _core._decode_spawn_marker_file(Path(_core.SPAWN_MARKERS_DIR) / f"{sid}.json")
            if m and m.get("lane") == "workers":
                found.add(sid)
        return found
    except Exception:
        return set()


def prepare_candidates(cands: list[dict], question: str, titles: dict | None = None,
                       full_title=None, worker_ids=None) -> tuple[list[dict], dict]:
    """Label, filter and merge pre-fetched candidates before Mazkir sees them.

    CCC's own session titles win over the index's (often a raw first prompt).
    Eval runs and WatchTower worker sessions are dropped unless the question
    is about them, and repeated runs of one loop prompt collapse into the
    best-ranked run (`runs=N`) so one worker loop can't fill every slot."""
    titles = dict(titles or {})
    if full_title:
        cut = [c["session_id"] for c in cands
               if not titles.get(c["session_id"]) and _needs_full_title(c.get("title"))]
        titles.update(full_title(cut) if cut else {})
    keep_evals = bool(re.search(r"\beval", question or "", re.I))
    keep_workers = bool(_WORKER_QUESTION_RE.search(question or ""))
    out: list[dict] = []
    by_title: dict[str, dict] = {}
    stats = {"evals_hidden": 0, "runs_merged": 0}
    for c in cands:
        c = dict(c)
        raw = c.get("title") or ""
        if not keep_evals and (is_eval_run(c) or _EVAL_RE.search(raw)):
            stats["evals_hidden"] += 1
            continue
        if not keep_workers and is_worker_session(c, worker_ids, titles.get(c["session_id"])):
            stats["workers_hidden"] = stats.get("workers_hidden", 0) + 1
            continue
        c["title"] = clean_title(titles.get(c["session_id"]) or raw)
        key = c["title"].lower()
        if key and key in by_title:
            by_title[key]["runs"] = by_title[key].get("runs", 1) + 1
            stats["runs_merged"] += 1
            continue
        if key:
            by_title[key] = c
        out.append(c)
    return out, stats


def _short_args(inp) -> str:
    if not isinstance(inp, dict):
        return ""
    for k in ("query", "q", "question", "session_id", "queue", "state"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            v = " ".join(v.split())
            return f'"{v[:57]}…"' if len(v) > 58 else f'"{v}"'
    return ""


def build_trace(prefetch_src: str, n_raw: int, n_kept: int, prefetch_ms: int, stats: dict,
                tool_calls: list | None, peers: list | None = None) -> list[dict]:
    """What Mazkir looked at, in order, for the small trace above the answer."""
    detail = f"{n_raw} candidates"
    if n_kept != n_raw:
        detail += f", {n_kept} kept"
    extra = []
    if stats.get("evals_hidden"):
        extra.append(f"{stats['evals_hidden']} eval run{'s' if stats['evals_hidden'] > 1 else ''} hidden")
    if stats.get("workers_hidden"):
        extra.append(f"{stats['workers_hidden']} worker session{'s' if stats['workers_hidden'] > 1 else ''} hidden")
    if stats.get("runs_merged"):
        extra.append(f"{stats['runs_merged']} repeat run{'s' if stats['runs_merged'] > 1 else ''} merged")
    if extra:
        detail += " (" + ", ".join(extra) + ")"
    trace = [{"tool": prefetch_src, "detail": f"{detail} · {prefetch_ms / 1000:.1f}s"}]
    for p in peers or []:
        if p.get("status") == "ok":
            d = f"{p.get('rows', 0)} sessions"
        else:
            d = str(p.get("status") or "?").replace("_", " ")
            if p.get("stale"):
                d += f", {p.get('rows', 0)} cached"
        trace.append({"tool": f"{p.get('name') or 'peer'} · memory recall", "detail": d})
    trace.append({"tool": "ccc-state · fleet snapshot", "detail": "census"})
    for call in tool_calls or []:
        name = str(call.get("name") or "?")
        if name.startswith("mcp__"):
            server, _, tool = name[5:].partition("__")
            name = f"{server} · {tool}" if tool else server
        trace.append({"tool": name, "detail": _short_args(call.get("input"))})
    if not tool_calls:
        trace.append({"tool": "no tool calls", "detail": "answered from the pre-search"})
    return trace


def source_row(s: dict, live_ids: set | None = None) -> dict:
    sid = s.get("session_id")
    title = clean_title(s.get("title"))
    harness = s.get("harness") or "claude"
    if harness != "claude" and title:
        title = f"[{harness}] {title}"
    elif harness != "claude":
        title = f"[{harness}] {sid}"
    if s.get("node"):
        title = f"[{s['node']}] {title or sid}"
    row = {
        "id": sid,
        "title": title or (sid or "")[:8],
        "repo": Path(str(s.get("cwd") or "")).name or None,
        "status": "live" if live_ids and sid in live_ids else s.get("status") or "idle",
        "ts_unix": _ts_unix(s.get("last_ts")) or s.get("ts_unix"),
        "cwd": s.get("cwd"),
        "snippet": " ".join(str(s.get("best_snippet") or s.get("snippet") or "").split())[:300],
        "harness": harness,
    }
    if s.get("node"):
        # Lives on a paired machine: the UI can't open it locally.
        row.update(node=s["node"], node_id=s.get("node_id"), local=False)
    return row


def assemble_sources(answer: str, candidates: list[dict], db_path: str = INDEX_DB,
                     live_ids: set | None = None) -> tuple[list[dict], list[str], list[dict]]:
    by_id = {c["session_id"]: c for c in candidates}
    cited: list[str] = []
    for sid in _CITE_RE.findall(answer or ""):
        if sid not in cited:
            cited.append(sid)
    missing = [sid for sid in cited if sid not in by_id]
    by_id.update(lookup_sessions(missing, db_path))
    valid = [sid for sid in cited if sid in by_id]
    sources = [dict(source_row(by_id[sid], live_ids), cited=True) for sid in valid]
    sources += [dict(source_row(c, live_ids), cited=False) for c in candidates if c["session_id"] not in valid]
    actions = [{"kind": "spawn-continue", "session_id": sid}
               for sid in _ACTION_RE.findall(answer or "") if sid in by_id]
    return sources, valid, actions


def run_mazkir(question: str, history: list | None = None, range_key: str | None = None,
               runner=None, base: str | None = None, claude_bin: str | None = None,
               fetch=None, prefetch_runner=None, db_path: str = INDEX_DB,
               focused=None, peer_fan_out=None) -> tuple[dict, int]:
    """Full Ask pipeline. Returns (response dict, HTTP status)."""
    t0 = time.time()
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "question is required", "code": "ask_bad_request"}, 400
    history = history if isinstance(history, list) else []
    base = resolve_base(base)
    since = _range_to_since(range_key)

    if claude_bin is None:
        claude_bin = os.environ.get("CCC_CLAUDE_BIN") or _find_claude_bin()
    if not claude_bin:
        return {"ok": False, "code": "ask_engine_unavailable", "error": "claude binary not found"}, 503

    # A currently-live session can't be "where the work already happened" —
    # it's still in progress, and is often the very session asking the
    # question (self-reference: the live check that motivated this fix cited
    # today's active sprint sessions instead of the actual 2026-08-30 build).
    # Computed once, up front, and reused for both the prefetch exclusion and
    # the "live" status badge on sources below.
    live_ids = _live_ids()

    prefetch_info: dict = {"raw": 0, "stats": {}, "peers": []}

    def do_prefetch() -> tuple[list[dict], str]:
        # The census fetch and the index search are independent; overlap them
        # (round 3 lost 12 s on Q3 waiting for a restarting CCC).
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=2) as ex:
            snap_f = ex.submit(fleet_snapshot, base, fetch)
            peer_f = ex.submit(peer_prefetch, question, fan_out=peer_fan_out)
            # Over-fetch so hidden worker sessions don't starve the slots;
            # trimmed back to PREFETCH_LIMIT local rows after filtering.
            if INDEX_BIN or prefetch_runner is not None:
                cands = prefetch_sessions(question, since, runner=prefetch_runner,
                                          index_bin=INDEX_BIN or "claude-index",
                                          limit=PREFETCH_LIMIT * 2, exclude_session_ids=live_ids)
            else:
                cands = builtin_prefetch(question, range_key, exclude_session_ids=live_ids,
                                         limit=PREFETCH_LIMIT * 2)
            try:
                peer_cands, prefetch_info["peers"] = peer_f.result(timeout=PEER_PREFETCH_TIMEOUT_SEC)
            except Exception:
                peer_cands = []
            local_ids = {c.get("session_id") for c in cands}
            cands = cands + [c for c in peer_cands if c["session_id"] not in local_ids]
            prefetch_info["raw"] = len(cands)
            cands, prefetch_info["stats"] = prepare_candidates(
                cands, question, _ccc_titles(c.get("session_id") for c in cands),
                full_title=lambda ids: full_titles(ids, db_path),
                worker_ids=worker_session_ids(local_ids))
            local = [c for c in cands if not c.get("node")][:PREFETCH_LIMIT]
            cands = local + [c for c in cands if c.get("node")]
            try:
                snap = snap_f.result(timeout=SNAPSHOT_TIMEOUT_SEC + 1)
            except Exception:
                snap = "fleet: (CCC census unavailable)"
        return cands, snap

    def make_prompt(cands: list[dict], snap: str) -> str:
        return build_prompt(question, history, cands, snap, range_key, index_available=bool(INDEX_BIN),
                            focused=focused)

    cwd = _scratch_dir()
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)  # allow nesting when called from inside a Claude session

    if runner is not None:
        # Legacy one-shot path (tests inject `runner`). Sequential on purpose:
        # text-mode `claude -p` gives up on stdin after 3 s, so the prompt
        # cannot be fed after a slow prefetch.
        session_id = str(uuid.uuid4())
        _mark_spawn(session_id)
        argv = mazkir_argv(claude_bin, base, session_id, index_bin=INDEX_BIN)
        try:
            candidates, snapshot = do_prefetch()
            prefetch_ms = int((time.time() - t0) * 1000)
            proc = runner(argv, input=make_prompt(candidates, snapshot),
                          timeout=MAZKIR_TIMEOUT_SEC, cwd=cwd, env=env)
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": "ask_timeout",
                    "error": f"mazkir timed out after {MAZKIR_TIMEOUT_SEC}s"}, 504
        except OSError as e:
            return {"ok": False, "code": "ask_engine_error", "error": str(e)[:300]}, 502
        if getattr(proc, "returncode", 1) != 0 and not (proc.stdout or "").strip():
            return {"ok": False, "code": "ask_engine_error",
                    "error": (proc.stderr or "claude exited non-zero")[:300]}, 502
        res = parse_result(proc.stdout)
        res.update({"mode": "oneshot", "ttft_ms": None})
    else:
        # Stream-json mode: the process waits on stdin indefinitely, so it can
        # boot (claude + both MCP servers) while the prefetch runs.
        from ccc_server import mazkir_warm as _warm
        argv = _warm.stream_argv(mazkir_argv(claude_bin, base, None, index_bin=INDEX_BIN))
        use_warm = _warm.warm_enabled()
        cold = None
        if use_warm:
            _warm.pool().warm(argv, cwd=cwd, env=env, on_session=_mark_spawn)
        else:
            cold = _warm.WarmProcess(argv, cwd=cwd, env=env, on_session=_mark_spawn)
        candidates, snapshot = do_prefetch()
        prefetch_ms = int((time.time() - t0) * 1000)
        prompt = make_prompt(candidates, snapshot)
        budget = lambda: max(5.0, MAZKIR_TIMEOUT_SEC - (time.time() - t0))  # noqa: E731
        res, mode, fallback = None, "warm" if use_warm else "cold", None
        try:
            if use_warm:
                try:
                    res = _warm.pool().ask(argv, prompt, budget(), cwd=cwd, env=env,
                                           on_session=_mark_spawn, t_start=t0)
                    mode = "warm" if res.get("warm") else "warm_boot"
                except _warm.WarmTimeout:
                    raise
                except _warm.WarmBusy:
                    fallback = "busy"
                except (_warm.WarmError, OSError) as e:
                    fallback = "crash: " + str(e)[:120]
                if res is None:
                    mode = "cold"
                    cold = _warm.WarmProcess(argv, cwd=cwd, env=env, on_session=_mark_spawn)
            if res is None:
                res = cold.ask(prompt, budget(), t_start=t0)
        except _warm.WarmTimeout:
            return {"ok": False, "code": "ask_timeout",
                    "error": f"mazkir timed out after {MAZKIR_TIMEOUT_SEC}s"}, 504
        except (_warm.WarmError, OSError) as e:
            return {"ok": False, "code": "ask_engine_error", "error": str(e)[:300]}, 502
        finally:
            if cold is not None:
                cold.close()
        res.update({"mode": mode, "fallback": fallback})

    if res.get("is_error") and _AUTH_ERROR_RE.search(res.get("answer") or ""):
        return {"ok": False, "code": "ask_engine_unauthenticated", "error": NOT_SIGNED_IN}, 401
    answer = res["answer"] or "(no answer)"
    sources, cited, actions = assemble_sources(answer, candidates, db_path, live_ids)
    # Sessions cited from a mid-answer search skipped prepare_candidates.
    names = _ccc_titles(src["id"] for src in sources if src.get("harness") == "claude")
    for src in sources:
        if names.get(src["id"]):
            src["title"] = clean_title(names[src["id"]])
    confirm_actions = collect_confirm_actions(answer, t0)
    prefetch_src = ("claude-index · sessions search" if (INDEX_BIN or prefetch_runner is not None)
                    else "CCC built-in session search")
    trace = build_trace(prefetch_src, prefetch_info["raw"], len(candidates), prefetch_ms,
                        prefetch_info["stats"], res.get("tool_calls"), prefetch_info["peers"])
    return {
        "ok": True,
        "answer": answer,
        "sources": sources[:12],
        "hit_count": len(candidates),
        "history_search": bool(INDEX_BIN),
        "cited": cited,
        "actions": actions,
        "confirm_actions": confirm_actions,
        "trace": trace,
        "engine": "claude",
        "model": MAZKIR_MODEL,
        "agent": "mazkir",
        "tools_used": True,
        "turns": res.get("num_turns"),
        "cost_usd": res.get("cost_usd"),
        "usage": turn_usage(res),
        "process_mode": res.get("mode"),
        "warm_fallback": res.get("fallback"),
        "ttft_ms": res.get("ttft_ms"),
        "prefetch_ms": prefetch_ms,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }, 200


_AUTH_ERROR_RE = re.compile(r"not logged in|/login|invalid api key|failed to authenticate|authentication_error|oauth", re.I)
NOT_SIGNED_IN = ("Ask uses Claude Code, which is installed but not signed in. Run `claude` in a "
                 "terminal, sign in with /login, then ask again.")


_CONFIRM_RE = re.compile(r"\[\[action:confirm:(act_[0-9a-f]{6,32})\]\]")


def collect_confirm_actions(answer: str, t0: float, store=None) -> list[dict]:
    """Proposals made during this Ask, with their confirm tokens, for the UI.

    Only proposals created since `t0` qualify, so a model can't resurface an
    older proposal by citing its id; uncited ones still show, because a
    proposal the model forgot to cite should not silently vanish."""
    try:
        if store is None:
            from ccc_server import assistant_actions as _aa
            store = _aa.store()
    except Exception:
        return []
    cited = _CONFIRM_RE.findall(answer or "")
    fresh = {it["id"]: it for it in store.since(t0)}
    order = [a for a in cited if a in fresh] + [a for a in fresh if a not in cited]
    return [store.public(fresh[a], with_token=True) for a in order]


def warm_up(base: str | None = None) -> dict:
    """Boot the warm process ahead of the first Ask (the Ask tab opening).

    Also reports which optional Ask features this machine has, so the tab
    only offers prompts that can work."""
    from ccc_server import mazkir_warm as _warm
    features = {"daily_checkin": checkin_enabled(), "history_search": bool(INDEX_BIN)}
    if not _warm.warm_enabled():
        return {"ok": True, "warm": False, "reason": "disabled", **features}
    claude_bin = os.environ.get("CCC_CLAUDE_BIN") or _find_claude_bin()
    if not claude_bin:
        return {"ok": False, "code": "ask_engine_unavailable", "error": "claude binary not found", **features}
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    argv = _warm.stream_argv(mazkir_argv(claude_bin, resolve_base(base), None, index_bin=INDEX_BIN))
    started = _warm.pool().warm(argv, cwd=_scratch_dir(), env=env, on_session=_mark_spawn)
    return {"ok": True, "warm": started, **_warm.pool().status(), **features}


# --- CCC-internal seams (lazy so the MCP half runs as a bare script) --------

def _find_claude_bin() -> str | None:
    try:
        from ccc_server import core as _core
        info = _core._resolve_claude_bin()
        return info.get("bin") if info.get("available") else None  # CCC's answer is final
    except Exception:
        pass
    for cand in (CLAUDE_BIN_FALLBACK, "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if os.path.exists(cand):
            return cand
    return None


def _scratch_dir() -> str:
    try:
        from ccc_server import core as _core
        p = Path(str(_core._SCRATCH_DIR))
        p.mkdir(parents=True, exist_ok=True)
        return str(p)
    except Exception:
        p = Path.home() / ".claude" / "command-center" / "scratch"
        p.mkdir(parents=True, exist_ok=True)
        return str(p)


def _mark_spawn(session_id: str) -> None:
    try:
        from ccc_server import core as _core
        _core._write_spawn_marker(session_id, lane="other", kind="assistant", spawned_via="ccc-ask")
    except Exception:
        pass


def ccc_titles(ids, overrides: dict, meta_cache: dict, auto: dict) -> dict:
    """The name CCC's sidebar shows for each id: user rename, then the
    transcript's custom title, then Claude's ai-title, then CCC's auto-title
    (same order as the session list's display_name)."""
    want = set(ids or ())
    meta: dict[str, dict] = {}
    for path, m in (meta_cache or {}).items():
        base = os.path.basename(str(path))
        if base.endswith(".jsonl") and base[:-6] in want and isinstance(m, dict):
            meta[base[:-6]] = m
    out = {}
    for sid in want:
        m = meta.get(sid) or {}
        t = next((str(v).strip() for v in (overrides.get(sid), m.get("custom_title"), m.get("ai_title"),
                                           auto.get(sid)) if v and str(v).strip()), "")
        if t:
            out[sid] = t
    return out


def _ccc_titles(ids) -> dict:
    """Sidebar names from CCC's warm in-memory state (no transcript reads);
    empty when CCC isn't importable (the MCP half runs as a plain script)."""
    ids = [i for i in ids or () if i]
    if not ids:
        return {}
    try:
        from ccc_server import core as _core
        from ccc_server import log_parse as _lp
        with _lp._conv_meta_cache_lock:
            meta_cache = dict(_lp._conv_meta_cache)
        return ccc_titles(ids, _core._load_session_name_overrides() or {}, meta_cache,
                          _core._auto_titled_session_ids() or {})
    except Exception:
        return {}


def _live_ids() -> set:
    try:
        from ccc_server import core as _core
        return set(_core._discover_live_session_ids() or ())
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    base = None
    if "--base" in rest:
        i = rest.index("--base")
        base = rest[i + 1] if i + 1 < len(rest) else None
        del rest[i:i + 2]
    if cmd == "mcp":
        serve_stdio(base)
        return 0
    if cmd == "tool":  # debugging: python mazkir.py tool fleet_diagnostics
        st = CccState(base)
        print(json.dumps(st.call(rest[0], json.loads(rest[1]) if len(rest) > 1 else {}), indent=1, default=str))
        return 0
    if cmd == "ask":
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        rng = None
        if "--range" in rest:
            i = rest.index("--range")
            rng = rest[i + 1]
            del rest[i:i + 2]
        body, status = run_mazkir(" ".join(rest), range_key=rng, base=base)
        print(json.dumps(body, indent=1, ensure_ascii=False, default=str))
        return 0 if status == 200 else 1
    print(f"unknown command {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
