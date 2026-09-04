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
INDEX_BIN = os.environ.get(
    "CLAUDE_INDEX_BIN", "/Users/amirfish/dev/tools/indexing/.venv/bin/claude-index")
INDEX_DB = os.environ.get("CLAUDE_INDEX_DB", str(Path.home() / ".claude-index" / "index.db"))
CHECKIN_PATH = os.environ.get(
    "CCC_DAILY_CHECKIN_FILE", str(Path.home() / "MyOfficeMgr" / "daily-checkin.md"))
CLAUDE_BIN_FALLBACK = str(Path.home() / ".local" / "bin" / "claude")

MAZKIR_MODEL = os.environ.get("CCC_ASK_MODEL", "sonnet")
MAZKIR_MAX_TURNS = int(os.environ.get("CCC_ASK_MAX_TURNS", "8"))
MAZKIR_TIMEOUT_SEC = int(os.environ.get("CCC_ASK_TIMEOUT_SEC", "75"))
PREFETCH_TIMEOUT_SEC = 15
PREFETCH_LIMIT = 8
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
    except OSError:
        pass
    return DEFAULT_BASE


def fetch_json(base: str, path: str, timeout: float = HTTP_TIMEOUT_SEC) -> dict:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (localhost)
        data = json.loads(resp.read().decode("utf-8") or "{}")
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
# Daily check-in agenda (~/MyOfficeMgr/daily-checkin.md)
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
        status = cells[2].lower().strip("` ")
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
     "inputSchema": {"type": "object", "properties": {}}},    {"name": "daily_checkin",
     "description": "Amir's standing daily check-in agenda (~/MyOfficeMgr/daily-checkin.md): open items "
                    "grouped by section (immediate, Becky/BYM product, CCC/Mazkir tooling, growth) with "
                    "ids, status and notes, plus the recent discussion log. Call this for 'daily check-in', "
                    "'morning review', 'what's on my agenda', 'what should we discuss'.",
     "inputSchema": {"type": "object", "properties": {
         "include_closed": {"type": "boolean", "default": False,
                            "description": "Also return done/dropped items."}}}},
]


class CccState:
    """Tool dispatcher; `fetch` is injectable for tests."""

    def __init__(self, base: str | None = None, fetch=None):
        self.base = resolve_base(base)
        self._fetch = fetch or (lambda path: fetch_json(self.base, path))

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
        if name == "daily_checkin":
            return tool_daily_checkin(include_closed=bool(args.get("include_closed")))
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
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
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

SYSTEM_PROMPT = """You are Mazkir, the memory and fleet assistant inside Claude Command Center (CCC) for Amir.
You answer questions about Amir's past work (across Claude Code, Codex, Kimi, Antigravity sessions and pulled Gmail threads) and about the live agent fleet.

Tools:
- claude-index: search_sessions (find which sessions are about X), search (specific facts/strings), session_info (confirm a session, see how it ended), show_message, recent_sessions.
- ccc-state: fleet_diagnostics (stuck / waiting / burning), list_sessions, live_activity, throughput_window, queue_status, session_detail., daily_checkin (Amir's standing agenda).

Method:
1. CANDIDATES are pre-fetched below with excerpts from their best-matching messages, best match first, with currently-live sessions already excluded (a session still open right now cannot be where past work "already happened" — it's likely the very session asking). If they answer the question, answer immediately without any tool call (each tool round trip costs ~4 s); call session_info only when the excerpts do not say what was decided or how it ended.
2. Otherwise call search_sessions once (rephrase with 2-4 topic words), then at most one or two follow-ups. Never loop.
3. Trust the candidate order: it already ranks by relevance with only a small recency tie-break, and demotes planning-only/self-referential sessions. Don't override it just because a lower-ranked candidate is more recent.
4. For fleet questions (stuck, burning, waiting, what is running, cost) call fleet_diagnostics or the specific tool once.
5. Be honest: if nothing matches, say what you searched and that you found nothing.
6. For a daily check-in / morning review / "what should we discuss", call daily_checkin once, then walk every open item by section as "id — item — one-line status or note", lead with the "today" items, and end by asking which item to pull in first. The 110-word cap does not apply to that answer.

Answer format (plain text, no markdown headers):
- Lead with the answer in one or two sentences, then 1-4 short supporting lines.
- Name up to 3 distinct sessions that actually did the work (skip near-duplicates like a continuation of a session you already named), each with its date, ranked best match first — not just the most recent.
- Cite every session you rely on inline as [[session:SESSION_ID]] using the exact id from the tool output or the candidate list. Cite Gmail threads the same way with the thread id.
- Give dates as YYYY-MM-DD and name the harness (Claude, Codex, Kimi, Antigravity, Gmail) when it is not Claude.
- If a session should be resumed to continue that work, append [[action:spawn-continue:SESSION_ID]] on its own line.
- Keep the whole answer under 110 words."""


def _range_to_since(range_key) -> str | None:
    return {"24h": "1d", "7d": "7d", "30d": "30d"}.get(str(range_key or "any").lower())


def prefetch_sessions(question: str, since: str | None, runner=None, index_bin: str = INDEX_BIN,
                      limit: int = PREFETCH_LIMIT, exclude_session_ids=None) -> list[dict]:
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
    return f"fleet: {len(census.get('sessions') or [])} sessions ({parts}); waiting on Amir: {waiting}"


def _fmt_candidate(i: int, s: dict) -> str:
    first = (s.get("first_ts") or "")[:10]
    last = (s.get("last_ts") or "")[:10]
    when = first if first == last or not last else f"{first}..{last}"
    title = " ".join(str(s.get("title") or "").split())[:140]
    snip = " ".join(str(s.get("best_snippet") or s.get("snippet") or "").split())[:220]
    out = (f"{i}. [[session:{s['session_id']}]] {s.get('harness') or 'claude'} {when} "
           f"hits={s.get('hits', '?')} cwd={s.get('cwd') or '?'}\n"
           f"   title: {title}\n   match: {snip}")
    for e in (s.get("excerpts") or [])[:3]:
        if not isinstance(e, dict):
            continue
        text = " ".join(str(e.get("text") or "").split())[:400]
        out += f"\n   excerpt ({e.get('type') or '?'} {(e.get('timestamp') or '')[:10]}): {text}"
    return out


def build_prompt(question: str, history: list, candidates: list[dict], snapshot: str,
                 range_key: str | None) -> str:
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
    lines.append("")
    if candidates:
        lines.append(f"CANDIDATES (pre-fetched from claude-index search_sessions, best first):")
        for i, s in enumerate(candidates, 1):
            lines.append(_fmt_candidate(i, s))
    else:
        lines.append("CANDIDATES: none pre-fetched (index search found nothing for the literal question).")
    lines.append("")
    lines.append(f"QUESTION: {question}")
    return "\n".join(lines)


def mcp_config(base: str, index_bin: str = INDEX_BIN) -> str:
    return json.dumps({"mcpServers": {
        "claude-index": {"command": index_bin, "args": ["mcp", "--lite"]},
        MCP_SERVER_NAME: {"command": sys.executable, "args": [os.path.abspath(__file__), "mcp", "--base", base]},
    }})


def mazkir_argv(claude_bin: str, base: str, session_id: str, model: str = MAZKIR_MODEL,
                index_bin: str = INDEX_BIN) -> list[str]:
    return [
        claude_bin, "-p",
        "--model", model,
        "--session-id", session_id,
        "--setting-sources", "project",       # skip user hooks: 13 s -> 3 s round trip
        "--output-format", "json",
        "--permission-mode", "dontAsk",
        "--max-turns", str(MAZKIR_MAX_TURNS),
        "--system-prompt", SYSTEM_PROMPT,
        "--mcp-config", mcp_config(base, index_bin),
        "--strict-mcp-config",
        "--allowedTools", "mcp__claude-index", f"mcp__{MCP_SERVER_NAME}",
        "--disallowedTools", "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "Read", "Glob",
        "Grep", "LS", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite",
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
                "duration_ms": data.get("duration_ms"), "claude_session_id": data.get("session_id")}
    return {"answer": text, "num_turns": None, "cost_usd": None, "is_error": False}


def _harness_from_project_dir(pd: str | None) -> str:
    return {"_codex": "codex", "_kimi": "kimi", "_antigravity": "antigravity", "_gmail": "gmail"}.get(pd or "", "claude")


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
                if not d.get("title"):
                    t = conn.execute(
                        "SELECT content FROM messages WHERE session_id=? AND type='user' "
                        "AND LTRIM(content) NOT LIKE '<%' ORDER BY ts_unix LIMIT 1", (sid,)).fetchone()
                    d["title"] = " ".join(str(t[0] if t else "").split())[:200]
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


def source_row(s: dict, live_ids: set | None = None) -> dict:
    sid = s.get("session_id")
    title = " ".join(str(s.get("title") or "").split())[:120]
    harness = s.get("harness") or "claude"
    if harness != "claude" and title:
        title = f"[{harness}] {title}"
    elif harness != "claude":
        title = f"[{harness}] {sid}"
    return {
        "id": sid,
        "title": title or (sid or "")[:8],
        "repo": Path(str(s.get("cwd") or "")).name or None,
        "status": "live" if live_ids and sid in live_ids else s.get("status") or "idle",
        "ts_unix": _ts_unix(s.get("last_ts")) or s.get("ts_unix"),
        "cwd": s.get("cwd"),
        "snippet": " ".join(str(s.get("best_snippet") or s.get("snippet") or "").split())[:300],
        "harness": harness,
    }


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
    sources = [source_row(by_id[sid], live_ids) for sid in valid]
    sources += [source_row(c, live_ids) for c in candidates if c["session_id"] not in valid]
    actions = [{"kind": "spawn-continue", "session_id": sid}
               for sid in _ACTION_RE.findall(answer or "") if sid in by_id]
    return sources, valid, actions


def run_mazkir(question: str, history: list | None = None, range_key: str | None = None,
               runner=None, base: str | None = None, claude_bin: str | None = None,
               fetch=None, prefetch_runner=None, db_path: str = INDEX_DB) -> tuple[dict, int]:
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

    def do_prefetch() -> tuple[list[dict], str]:
        # The census fetch and the index search are independent; overlap them
        # (round 3 lost 12 s on Q3 waiting for a restarting CCC).
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            snap_f = ex.submit(fleet_snapshot, base, fetch)
            cands = prefetch_sessions(question, since, runner=prefetch_runner,
                                      exclude_session_ids=live_ids)
            try:
                snap = snap_f.result(timeout=SNAPSHOT_TIMEOUT_SEC + 1)
            except Exception:
                snap = "fleet: (CCC census unavailable)"
        return cands, snap

    def make_prompt(cands: list[dict], snap: str) -> str:
        return build_prompt(question, history, cands, snap, range_key)

    session_id = str(uuid.uuid4())
    cwd = _scratch_dir()
    _mark_spawn(session_id)
    argv = mazkir_argv(claude_bin, base, session_id)
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)  # allow nesting when called from inside a Claude session
    # Sequential on purpose: `claude -p` gives up on stdin after 3 s, so the
    # prompt cannot be fed after a slow prefetch (tried; it fails with
    # "Input must be provided" whenever the index is under load).
    run = runner or (lambda a, **kw: subprocess.run(a, capture_output=True, text=True, **kw))
    try:
        candidates, snapshot = do_prefetch()
        prefetch_ms = int((time.time() - t0) * 1000)
        proc = run(argv, input=make_prompt(candidates, snapshot),
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
    answer = res["answer"] or "(no answer)"
    sources, cited, actions = assemble_sources(answer, candidates, db_path, live_ids)
    return {
        "ok": True,
        "answer": answer,
        "sources": sources[:12],
        "hit_count": len(candidates),
        "cited": cited,
        "actions": actions,
        "engine": "claude",
        "model": MAZKIR_MODEL,
        "agent": "mazkir",
        "tools_used": True,
        "turns": res.get("num_turns"),
        "cost_usd": res.get("cost_usd"),
        "prefetch_ms": prefetch_ms,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }, 200


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
