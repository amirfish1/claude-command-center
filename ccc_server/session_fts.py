# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Session-level FTS5 retrieval over Claude Code and Codex transcripts.

Contract:
  search_sessions(query: str, limit: int = 20) -> list of dicts with 'session_id', best first.

Builds and incrementally updates an SQLite FTS5 index persisted on disk
under ~/.claude/command-center/session_fts.sqlite, keyed by transcript
(mtime, size). Never re-parses unchanged files. Stdlib only.

Optionally fuses in a local-embeddings channel (Ollama's nomic-embed-text,
RRF-fused with the FTS ranking) when a local Ollama daemon is reachable on
localhost:11434. Embeddings are built incrementally by the same (mtime, size)
gate as the FTS index. Any Ollama failure (not installed, not running, model
missing) degrades silently to FTS-only -- the default for most users.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.request
from array import array
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ccc_server import hermes as _hermes
from ccc_server import lineage as _lineage
from ccc_server import sandbox_harvest as _sandbox_harvest
from ccc_server import ship_graph as _sg

# Query sanitization reuse from history_search
try:
    from ccc_server.history_search import (
        extract_history_terms,
        _is_explicit_history_fts_query,
        _rewrite_history_query,
    )
except ImportError:
    if "bench_history_search" in sys.modules:
        _bhs = sys.modules["bench_history_search"]
        extract_history_terms = _bhs.extract_history_terms
        _is_explicit_history_fts_query = _bhs._is_explicit_history_fts_query
        _rewrite_history_query = _bhs._rewrite_history_query
    else:
        import importlib.util
        _hs_path = Path(__file__).resolve().parent / "history_search.py"
        _spec = importlib.util.spec_from_file_location("ccc_server_history_search", _hs_path)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        extract_history_terms = _mod.extract_history_terms
        _is_explicit_history_fts_query = _mod._is_explicit_history_fts_query
        _rewrite_history_query = _mod._rewrite_history_query

# Regular expressions matching transcript cataloging
COMMIT_RE = re.compile(r"\[([\w./-]+)(?: \(root-commit\))? ([0-9a-f]{7,12})\] ([^\n\\]{3,160})")
TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,11}-\d{1,5})\b")
TICKET_STOP = re.compile(r"^(UTF|SHA|ISO|RFC|CVE|HTTP|TLS|GPT|MD|X|UUID|AES|RSA|P|H|E|W|U|A|B|C)-", re.I)
SCRATCH_RE = re.compile(r"(command-center-scratch|/private/var/|/tmp/|/var/folders/|scratch-|ccc-claude-midstream)", re.I)
CODEX_SID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", re.I)

MAX_USER = 30000
MAX_ASSIST = 30000

# MEMO-FIX-21: sdoc keeps one capped row per session (middle dropped), so a
# long session's mid-transcript mentions are invisible to it. Sessions whose
# text got capped -- or that carry task/plan text (TaskCreate, TodoWrite,
# Codex update_plan), which sdoc never stored -- are *also* indexed as
# sections in `ssec`: ~SECTION_TURNS user turns each (or a compaction
# boundary, or SECTION_CHARS), keyed to the parent sid. search_sessions()
# collapses section hits per session. Tool output stays out of sections.
SECTION_TURNS = 50
SECTION_CHARS = 60000
# Bump when the section shape changes: _sync_index() then re-parses every
# already-indexed transcript above _SECTION_MIGRATE_MIN_SIZE, in the
# background (never inline on a request thread).
SECTION_SCHEMA_VERSION = 1
_SECTION_MIGRATE_MIN_SIZE = 100_000
# MEMORY-18: bumped past SECTION_SCHEMA_VERSION so both migrations run in
# order off the same `PRAGMA user_version` counter.
KIMI_SID_SCHEMA_VERSION = 2
_TASK_TOOLS = ("TaskCreate", "TaskUpdate", "TodoWrite")
# bm25 weights for ssec(sid, sec, turn0, turn1, body, tasks). Task/plan text
# is the session's own statement of what it is working on -- stickier than
# any one message (Claude Code re-injects it every few turns) -- so it
# weighs like sdoc's prompts/meta rather than like body text.
SSEC_WEIGHTS_STR = "0, 0, 0, 0, 1.0, 6.0"

# BM25 column weights for sdoc(sid, title, prompts, report, body, meta, summary)
# sid is UNINDEXED (col 0 = 0 weight)
BM25_WEIGHTS = (0, 15.0, 2.0, 1.0, 1.0, 4.0, 0.0)
BM25_WEIGHTS_STR = ", ".join(str(w) for w in BM25_WEIGHTS)

_tls = threading.local()
_sync_lock = threading.Lock()
_last_sync_ts = 0.0
_SYNC_TTL = 5.0  # seconds between directory scans

# A cold-start (or any catch-up this large) parses too many transcripts to do
# inline on a request thread -- see _start_background_sync().
_BG_SYNC_THRESHOLD = int(os.environ.get("CCC_SESSION_FTS_SYNC_INLINE_MAX", "50"))
_bg_sync_state_lock = threading.Lock()
_bg_sync_running = False

# --- Local Ollama embeddings (optional P2 hybrid channel) --------------------
# Fully optional: any Ollama failure (not installed, not running, model not
# pulled) degrades silently back to FTS-only, which is the pre-existing
# behavior. Nothing here ever raises out of search_sessions().
EMB_MODEL = "nomic-embed-text"
_EMBED_BATCH = 32
# Background-only budget (document embedding, and the backfill drain below).
# A cold Ollama model-load has been measured at ~20s when the model lives on
# an external drive (OPS-1251); a 20s timeout here would race that load and
# fail the very first background batch. This is never on a user-facing
# request path -- _QUERY_EMBED_TIMEOUT below is the short one that is.
_EMBED_TIMEOUT = float(os.environ.get("CCC_SESSION_FTS_EMBED_TIMEOUT", "45"))
# The per-query embed in _vector_rank() runs synchronously on the request
# thread (it must, to rank *this* call's results) -- unlike document
# embedding, it can't be backgrounded. Capped short so a cold Ollama model
# (MEMO-FIX-13: measured ~10s to load vs ~0.05s warm) degrades this one call
# to FTS-only instead of blocking recall() for the full model-load time.
_QUERY_EMBED_TIMEOUT = float(os.environ.get("CCC_SESSION_FTS_QUERY_EMBED_TIMEOUT", "1.5"))
_OLLAMA_PROBE_TIMEOUT = 0.3
_OLLAMA_TTL = 30.0  # seconds between "is Ollama up" liveness probes
_MAX_EMBED_SESSIONS_PER_SYNC = int(os.environ.get("CCC_SESSION_FTS_EMBED_BATCH_CAP", "64"))

_ollama_state = {"ts": 0.0, "ok": False}
_vec_cache: dict = {"sids": [], "vecs": []}

# Catch-up backfill for sdoc rows that never got a semb row (eg. everything
# indexed while Ollama was unreachable, before semb_pending queueing existed).
# Runs once per process as a background loop kicked off from warm_start();
# see _run_embedding_backfill().
_BACKFILL_RETRY_INTERVAL = 30.0  # seconds; matches _OLLAMA_TTL cadence
_backfill_lock = threading.Lock()
_backfill_running = False


def _get_db_path() -> Path:
    env = os.environ.get("CCC_SESSION_FTS_DB")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "command-center" / "session_fts.sqlite"


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with WAL journaling so a long writer (the cold-start
    catch-up sync) never blocks a concurrent reader (MEMO-FIX-14: the default
    rollback-journal mode serializes readers behind writers for the whole
    write transaction, which is exactly the tens-of-seconds-long first recall
    seen after a real restart)."""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _get_projects_dir() -> Path:
    env = os.environ.get("CCC_PROJECTS_ROOT")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "projects"


def _get_codex_dir() -> Path:
    env = os.environ.get("CCC_CODEX_SESSIONS_ROOT")
    if env:
        return Path(env)
    return Path.home() / ".codex" / "sessions"


def _get_kimi_dir() -> Path:
    env = os.environ.get("CCC_KIMI_SESSIONS_ROOT")
    if env:
        return Path(env)
    home = os.environ.get("KIMI_CODE_HOME", "").strip()
    base = Path(os.path.expanduser(home)) if home else Path.home() / ".kimi-code"
    return base / "sessions"


def _get_gemini_dir() -> Path:
    env = os.environ.get("CCC_GEMINI_TMP_ROOT")
    if env:
        return Path(env)
    return Path.home() / ".gemini" / "tmp"


def _get_agy_roots() -> list[Path]:
    """Antigravity (agy) brain dirs: the desktop app and the CLI each keep one
    `<sid>/.system_generated/logs/transcript*.jsonl` per conversation."""
    env = os.environ.get("CCC_AGY_BRAIN_ROOTS")
    if env is not None:
        return [Path(x) for x in env.split(os.pathsep) if x]
    base = Path.home() / ".gemini"
    return [base / "antigravity" / "brain", base / "antigravity-cli" / "brain"]


def _get_cursor_dir() -> Path:
    env = os.environ.get("CCC_CURSOR_PROJECTS_ROOT")
    if env:
        return Path(env)
    return Path.home() / ".cursor" / "projects"


def _get_harvested_dir() -> Path:
    return _sandbox_harvest._get_harvested_dir()


def _repo_of(cwd: str) -> str:
    if not cwd:
        return ""
    p = cwd.rstrip("/")
    m = re.search(r"/([^/]+?)(?:-wt-[^/]+)?/\.(?:worktrees|claude/worktrees)/[^/]+", p)
    if m:
        return m.group(1)
    name = Path(p).name
    name = re.sub(r"-wt-.*$", "", name)
    return name


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict):
                if c.get("type") in ("text", "input_text", "output_text"):
                    out.append(c.get("text") or "")
    return "\n".join(out)


def _tool_blob(content) -> str:
    out = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                inner = c.get("content")
                out.append(inner if isinstance(inner, str) else _text_of(inner))
    return "\n".join(out)


def _is_real_prompt(t: str) -> bool:
    s = t.lstrip()
    if not s:
        return False
    if s.startswith("<") or s.startswith("Caveat:") or s.startswith("[Request interrupted"):
        return False
    if s.startswith("This session is being continued from a previous conversation"):
        return False
    if s.startswith("# AGENTS.md instructions") or s.startswith("# Global agent guidance"):
        return False
    return True


def _ts(s) -> float | None:
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _is_scratch(path: str, cwd: str) -> int:
    if os.environ.get("CCC_SESSION_FTS_ALLOW_SCRATCH") == "1":
        return 0
    return 1 if (SCRATCH_RE.search(path) or SCRATCH_RE.search(cwd or "")) else 0


def _task_text(name: str, inp) -> str:
    """Searchable text of one task/plan tool call (TaskCreate/TaskUpdate,
    TodoWrite, Codex update_plan) -- subjects, descriptions, plan steps."""
    if not isinstance(inp, dict):
        return ""
    out = []
    for k in ("subject", "description", "activeForm", "explanation"):
        v = inp.get(k)
        if isinstance(v, str):
            out.append(v)
    for item in (inp.get("todos") or []) + (inp.get("plan") or []):
        if isinstance(item, dict):
            v = item.get("content") or item.get("step")
            if isinstance(v, str):
                out.append(v)
    return "\n".join(out)


def _build_sections(stream: list[tuple[str, int, str]]) -> list[dict]:
    """Split an ordered (kind, turn, text) stream into sections.

    kind: "u" user prompt, "a" assistant text, "t" task/plan text, "b"
    compaction boundary. `turn` is the 1-based user-prompt index the item
    belongs to. A section closes at a compaction boundary, after
    SECTION_TURNS user prompts, or past SECTION_CHARS of text.
    """
    sections: list[dict] = []
    cur: dict | None = None
    seen_tasks: set[str] = set()

    def close():
        nonlocal cur
        if cur and (cur["parts"] or cur["tasks"]):
            sections.append({
                "sec": len(sections),
                "turn0": cur["turn0"],
                "turn1": cur["turn1"],
                "body": "\n---\n".join(cur["parts"]),
                "tasks": "\n---\n".join(cur["tasks"]),
            })
        cur = None

    for kind, turn, text in stream:
        if kind == "b":
            close()
            continue
        if kind == "t":
            if text in seen_tasks:
                continue
            seen_tasks.add(text)
        if cur and kind == "u" and (cur["users"] >= SECTION_TURNS or cur["chars"] >= SECTION_CHARS):
            close()
        if cur is None:
            cur = {"turn0": turn, "turn1": turn, "parts": [], "tasks": [], "chars": 0, "users": 0}
        piece = text[:4000] if kind != "a" else text[:3000]
        (cur["tasks"] if kind == "t" else cur["parts"]).append(piece)
        cur["chars"] += len(piece)
        cur["turn1"] = turn
        if kind == "u":
            cur["users"] += 1
    close()
    return sections


def _finish(sid, engine, path, cwd, title, users, assists, tools, files, ts0, ts1, stream=None):
    first = users[0] if users else ""
    joined_user = "\n---\n".join(u[:4000] for u in users)
    user_text = joined_user[:MAX_USER]
    joined = "\n---\n".join(a[:3000] for a in assists)
    assistant_text = (
        joined if len(joined) <= MAX_ASSIST
        else joined[: MAX_ASSIST // 3] + "\n…\n" + joined[-2 * MAX_ASSIST // 3:]
    )
    stream = stream or []
    capped = len(joined_user) > MAX_USER or len(joined) > MAX_ASSIST
    has_tasks = any(k == "t" for k, _, _ in stream)
    sections = _build_sections(stream) if (capped or has_tasks) else []
    final = "\n---\n".join(assists[-3:])[:12000]
    blob = "\n".join(tools)
    commits = {}
    for m in COMMIT_RE.finditer(blob):
        commits[m.group(2)] = {"branch": m.group(1), "subject": m.group(3).strip()}
    alltext = user_text + "\n" + assistant_text
    tickets = sorted({t for t in TICKET_RE.findall(alltext) if not TICKET_STOP.match(t)})
    st = os.stat(path)

    meta_parts = []
    repo = _repo_of(cwd)
    if repo:
        meta_parts.append(repo)
    if tickets:
        meta_parts.append(" ".join(tickets))
    if commits:
        meta_parts.append(" ".join(v["subject"] for v in commits.values()))
    if files:
        meta_parts.append(" ".join(Path(f).name for f in sorted(files)[:60]))
    meta = " ".join(meta_parts)

    t = (title or "").strip()
    if t.lower().startswith("prewarm-") or len(t) < 4:
        t = " ".join(first.split())[:200]

    is_scratch = _is_scratch(path, cwd)

    return {
        "sid": sid,
        "engine": engine,
        "path": path,
        "cwd": cwd or "",
        "size": st.st_size,
        "mtime": st.st_mtime,
        "n_user": len(users),
        "scratch": is_scratch,
        "title": t,
        "first_prompt": first,
        "user_text": user_text,
        "final_text": final,
        "assistant_text": assistant_text,
        "meta": meta,
        "summary": "",
        "sections": sections,
    }


def parse_claude(path: str) -> dict | None:
    sid = Path(path).stem
    cwd = ""
    title_custom = title_ai = ""
    users, assists, tools = [], [], []
    stream: list[tuple[str, int, str]] = []
    files = set()
    ts0 = ts1 = None
    with open(path, "rb") as f:
        for raw in f:
            try:
                d = json.loads(raw)
            except Exception:
                continue
            t = d.get("type")
            if t == "system" and d.get("subtype") == "compact_boundary":
                stream.append(("b", len(users), ""))
                continue
            if t == "custom-title":
                title_custom = d.get("customTitle") or title_custom
                continue
            if t == "ai-title":
                title_ai = d.get("aiTitle") or title_ai
                continue
            if t not in ("user", "assistant"):
                continue
            if d.get("isSidechain"):
                continue
            cwd = cwd or d.get("cwd") or ""
            ts = _ts(d.get("timestamp"))
            if ts:
                ts0 = ts if ts0 is None else min(ts0, ts)
                ts1 = ts if ts1 is None else max(ts1, ts)
            msg = d.get("message") or {}
            content = msg.get("content")
            if t == "user":
                if d.get("isMeta"):
                    continue
                txt = _text_of(content)
                if txt and _is_real_prompt(txt):
                    users.append(txt)
                    stream.append(("u", len(users), txt))
                tb = _tool_blob(content)
                if tb:
                    tools.append(tb[:4000])
            else:
                txt = _text_of(content)
                if txt:
                    assists.append(txt)
                    stream.append(("a", len(users), txt))
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            inp = c.get("input") or {}
                            if c.get("name") in _TASK_TOOLS:
                                tt = _task_text(c.get("name"), inp)
                                if tt:
                                    stream.append(("t", len(users), tt))
                            fp = inp.get("file_path") or inp.get("notebook_path")
                            if fp and c.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                                files.add(fp)
                            cmd = inp.get("command")
                            if isinstance(cmd, str) and "git commit" in cmd:
                                tools.append(cmd[:1500])
    return _finish(sid, "claude", path, cwd, title_custom or title_ai, users, assists, tools, files, ts0, ts1, stream)


def parse_codex(path: str) -> dict | None:
    sid = ""
    m = CODEX_SID_RE.search(path)
    if m:
        sid = m.group(1)
    cwd = ""
    users, assists, tools, finals = [], [], [], []
    stream: list[tuple[str, int, str]] = []
    files = set()
    ts0 = ts1 = None
    with open(path, "rb") as f:
        for raw in f:
            try:
                d = json.loads(raw)
            except Exception:
                continue
            t = d.get("type")
            p = d.get("payload") or {}
            ts = _ts(d.get("timestamp"))
            if ts:
                ts0 = ts if ts0 is None else min(ts0, ts)
                ts1 = ts if ts1 is None else max(ts1, ts)
            if t == "session_meta":
                sid = p.get("id") or p.get("session_id") or sid
                cwd = p.get("cwd") or cwd
                continue
            if t == "turn_context" and not cwd:
                cwd = p.get("cwd") or cwd
            if t == "compacted":
                stream.append(("b", len(users), ""))
                continue
            pt = p.get("type")
            if t == "response_item" and pt == "message":
                txt = _text_of(p.get("content"))
                role = p.get("role")
                if role == "user" and _is_real_prompt(txt):
                    users.append(txt)
                    stream.append(("u", len(users), txt))
                elif role == "assistant" and txt:
                    assists.append(txt)
                    stream.append(("a", len(users), txt))
            elif t == "response_item" and pt in ("function_call_output", "custom_tool_call_output"):
                out = p.get("output")
                tb = out if isinstance(out, str) else _text_of(out)
                if tb and ("git" in tb or "] " in tb):
                    tools.append(tb[:4000])
            elif t == "response_item" and pt in ("function_call", "custom_tool_call"):
                arg = p.get("arguments") or p.get("input") or ""
                if isinstance(arg, str) and "git commit" in arg:
                    tools.append(arg[:1500])
                if p.get("name") == "update_plan" and isinstance(arg, str):
                    try:
                        tt = _task_text("update_plan", json.loads(arg))
                    except ValueError:
                        tt = ""
                    if tt:
                        stream.append(("t", len(users), tt))
                for fm in re.finditer(r"\*\*\* (?:Update|Add) File: ([^\n\\]+)", arg if isinstance(arg, str) else ""):
                    files.add(fm.group(1).strip())
            elif t == "event_msg" and pt == "task_complete":
                if p.get("last_agent_message"):
                    finals.append(p["last_agent_message"])
    if not sid:
        return None
    r = _finish(sid, "codex", path, cwd, "", users, assists, tools, files, ts0, ts1, stream)
    if r and finals:
        r["final_text"] = "\n---\n".join(finals[-3:])[:12000]
    return r


def parse_kimi(path: str) -> dict | None:
    """Kimi Code's wire.jsonl: an event-sourced log at
    <sessionDir>/agents/main/wire.jsonl. The session id and cwd are not in
    the transcript itself -- the session dir's own name IS the sessionId
    (verified against a real ~/.kimi-code/sessions/*/session_<uuid> layout),
    and cwd lives in the sibling state.json's `workDir`."""
    p = Path(path)
    try:
        session_dir = p.parents[2]
    except IndexError:
        return None
    sid = session_dir.name
    if not sid:
        return None
    # MEMORY-18: Kimi's own on-disk dir name is `session_<uuid>`, but every
    # other harness here (Claude, Codex) uses a bare UUID -- normalize to
    # bare so search_sessions()/recall() ids are uniform across harnesses.
    # Kimi-specific code that needs the prefixed form back (to look the
    # session up in Kimi's own index) already has
    # kimi_store._canonical_kimi_session_id() for that.
    if sid.startswith("session_"):
        sid = sid[len("session_"):]
    cwd = ""
    try:
        with open(session_dir / "state.json", "rb") as sf:
            state = json.loads(sf.read())
        if isinstance(state, dict):
            cwd = state.get("cwd") or ""
    except Exception:
        pass
    users, assists, tools = [], [], []
    files = set()
    ts0 = ts1 = None
    with open(path, "rb") as f:
        for raw in f:
            try:
                d = json.loads(raw)
            except Exception:
                continue
            t = d.get("type")
            tms = d.get("time")
            if isinstance(tms, (int, float)) and tms > 0:
                ts = tms / 1000.0
                ts0 = ts if ts0 is None else min(ts0, ts)
                ts1 = ts if ts1 is None else max(ts1, ts)
            if t == "turn.prompt":
                blocks = d.get("input")
                if isinstance(blocks, list):
                    txt = "".join(
                        str(b.get("text") or "") for b in blocks
                        if isinstance(b, dict) and b.get("type") == "text"
                    ).strip()
                    if txt and _is_real_prompt(txt):
                        users.append(txt)
            elif t == "context.append_loop_event":
                ev = d.get("event") or {}
                et = ev.get("type")
                if et == "content.part":
                    part = ev.get("part") or {}
                    if part.get("type") == "text":
                        txt = part.get("text") or ""
                        if txt:
                            assists.append(txt)
                elif et == "tool.call":
                    name = ev.get("name") or ""
                    args = ev.get("args") or {}
                    cmd = args.get("command")
                    if isinstance(cmd, str) and "git commit" in cmd:
                        tools.append(cmd[:1500])
                    fp = args.get("file_path") or args.get("path") or args.get("notebook_path")
                    if fp and name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        files.add(fp)
                elif et == "tool.result":
                    out = (ev.get("result") or {}).get("output")
                    if isinstance(out, str) and out:
                        tools.append(out[:4000])
    if not users and not assists:
        return None
    return _finish(sid, "kimi", path, cwd, "", users, assists, tools, files, ts0, ts1)


def parse_gemini(path: str) -> dict | None:
    """Gemini CLI chat logs (~/.gemini/tmp/<slug>/chats/session-*.json[l]).
    Reuses ccc_server.gemini's already-battle-tested loader/field-extraction
    helpers rather than re-deriving the (single-doc vs line-delimited,
    $set-patched header) format here."""
    from ccc_server import gemini as _gem

    p = Path(path)
    data = _gem._load_gemini_chat(p)
    if not isinstance(data, dict):
        return None
    sid = data.get("sessionId") or p.stem
    if not sid:
        return None
    cwd = _gem._gemini_project_root_for_chat(p)
    users, assists, tools = [], [], []
    files = set()
    ts0 = ts1 = None
    for msg in data.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        ts = _ts(msg.get("timestamp"))
        if ts:
            ts0 = ts if ts0 is None else min(ts0, ts)
            ts1 = ts if ts1 is None else max(ts1, ts)
        mtype = msg.get("type")
        text = _gem._gemini_message_text(msg)
        if mtype == "user":
            if text and _is_real_prompt(text):
                users.append(text)
        elif mtype == "gemini":
            if text:
                assists.append(text)
            for call in (msg.get("toolCalls") or []):
                if not isinstance(call, dict):
                    continue
                cmd = _gem._gemini_tool_command(call)
                if isinstance(cmd, str) and "git commit" in cmd:
                    tools.append(cmd[:1500])
                out = _gem._gemini_tool_output(call)
                if out:
                    tools.append(out[:4000])
                args = _gem._gemini_tool_args(call)
                name = _gem._gemini_tool_name(call)
                fp = args.get("file_path") or args.get("absolute_path") or args.get("path")
                if fp and name.lower() in ("writefile", "edit", "replace"):
                    files.add(fp)
    if not users and not assists:
        return None
    return _finish(sid, "gemini", str(p), cwd, "", users, assists, tools, files, ts0, ts1)


def parse_cursor(path: str) -> dict | None:
    """Cursor's per-session transcript at
    <projects>/<slug>/agent-transcripts/<session-id>/<session-id>.jsonl --
    the session id is the transcript's own parent directory name, and cwd
    is only recoverable by decoding the project-slug directory name (there
    is no cwd field in the transcript). Reuses ccc_server.cursor's helpers
    for both, plus its <user_query>/[REDACTED] text cleanup."""
    from ccc_server import cursor as _cur

    p = Path(path)
    sid = p.parent.name
    if not sid:
        return None
    cwd = _cur._cursor_cwd_from_transcript_path(p)
    users, assists, tools = [], [], []
    files = set()
    ts0 = ts1 = None
    with open(path, "rb") as f:
        for raw in f:
            try:
                d = json.loads(raw)
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            ts = _ts(_cur._cursor_event_timestamp(d))
            if ts:
                ts0 = ts if ts0 is None else min(ts0, ts)
                ts1 = ts if ts1 is None else max(ts1, ts)
            role = _cur._cursor_event_role(d)
            blocks = _cur._cursor_content_blocks(d)
            if role == "user":
                for b in blocks:
                    if b.get("type") == "text":
                        txt = _cur._cursor_user_text(b.get("text") or "")
                        if txt and _is_real_prompt(txt):
                            users.append(txt)
            elif role == "assistant":
                for b in blocks:
                    if b.get("type") == "text":
                        txt = _cur._cursor_visible_text(b.get("text") or "")
                        if txt:
                            assists.append(txt)
                    elif b.get("type") == "tool_use":
                        cmd = _cur._cursor_tool_command(b)
                        if isinstance(cmd, str) and "git commit" in cmd:
                            tools.append(cmd[:1500])
                        args = _cur._cursor_tool_args(b)
                        name = _cur._cursor_tool_name(b)
                        fp = args.get("file_path") or args.get("target_file") or args.get("path")
                        if fp and name in ("StrReplace", "Write", "Edit", "MultiEdit"):
                            files.add(fp)
    if not users and not assists:
        return None
    return _finish(sid, "cursor", path, cwd, "", users, assists, tools, files, ts0, ts1)


_AGY_USER_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)


def parse_agy(path: str) -> dict | None:
    """Antigravity brain transcript (transcript_full.jsonl / transcript.jsonl).
    One JSON step per line: USER_INPUT (prompt wrapped in <USER_REQUEST>),
    PLANNER_RESPONSE / TEXT_RESPONSE (assistant text; PLANNER_RESPONSE also
    carries tool_calls), RUN_COMMAND (output in content)."""
    p = Path(path)
    sid = p.parent.parent.parent.name
    if not sid:
        return None
    users, assists, tools = [], [], []
    files = set()
    cwd = ""
    ts0 = ts1 = None
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            ts = _ts(ev.get("created_at"))
            if ts:
                ts0 = ts if ts0 is None else min(ts0, ts)
                ts1 = ts if ts1 is None else max(ts1, ts)
            typ = ev.get("type")
            content = ev.get("content") if isinstance(ev.get("content"), str) else ""
            if typ == "USER_INPUT":
                m = _AGY_USER_RE.search(content)
                txt = (m.group(1) if m else content).strip()
                if txt and _is_real_prompt(txt):
                    users.append(txt)
            elif typ in ("PLANNER_RESPONSE", "TEXT_RESPONSE"):
                if content.strip():
                    assists.append(content.strip())
                for call in ev.get("tool_calls") or []:
                    args = call.get("args") if isinstance(call, dict) else None
                    if not isinstance(args, dict):
                        continue
                    if not cwd and isinstance(args.get("Cwd"), str):
                        cwd = args["Cwd"]
                    cmd = args.get("CommandLine")
                    if isinstance(cmd, str) and "git commit" in cmd:
                        tools.append(cmd[:1500])
                    fp = args.get("TargetFile") or args.get("AbsolutePath")
                    if isinstance(fp, str) and call.get("name") in (
                            "write_to_file", "replace_file_content", "multi_replace_file_content"):
                        files.add(fp)
            elif typ == "RUN_COMMAND" and content:
                tools.append(content[:4000])
    if not users and not assists:
        return None
    return _finish(sid, "agy", str(p), cwd, "", users, assists, tools, files, ts0, ts1)


_PARSERS = {
    "agy": parse_agy,
    "claude": parse_claude,
    "codex": parse_codex,
    "kimi": parse_kimi,
    "gemini": parse_gemini,
    "cursor": parse_cursor,
}


def _parse_file(args: tuple[str, str]) -> dict | None:
    engine, path = args
    try:
        parser = _PARSERS.get(engine)
        return parser(path) if parser else None
    except Exception:
        return None


def _candidate_files(days: float | None = None) -> list[tuple[str, str, float, int]]:
    """Enumerate candidate session transcript files with their mtime and size."""
    if days is None:
        try:
            # MEMORY-19: was "45" -- a session-memory product that can't
            # recall anything older than ~6 weeks defeats its own purpose.
            # Measured root cause of one of the ten real hand-verified
            # queries in ~/dev/scratch/memory-19-benchmark missing entirely:
            # its answer transcript (a 68-day-old Codex rollout) was excluded
            # by this cutoff before a single query term was ever evaluated --
            # confirmed via _candidate_files(days=45) excluding it and
            # _candidate_files(days=365) (this default) including it -- no
            # ranking fix can recover a candidate that's filtered out here.
            # 365 covers a full year of work history; indexing itself stays
            # incremental (mtime, size) and any large one-time catch-up is
            # already backgrounded (see _BG_SYNC_THRESHOLD), so raising this
            # only costs one background pass, never a request-thread stall.
            days = float(os.environ.get("CCC_SESSION_FTS_DAYS", os.environ.get("BENCH_DAYS", "365")))
        except ValueError:
            days = 365.0
    cutoff = (time.time() - days * 86400) if days and days > 0 else 0.0

    out = []
    projects_dir = _get_projects_dir()
    if projects_dir.exists():
        for p in projects_dir.glob("*/*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > 0 and st.st_mtime >= cutoff:
                out.append(("claude", str(p), st.st_mtime, st.st_size))

    codex_dir = _get_codex_dir()
    if codex_dir.exists():
        for p in codex_dir.rglob("*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > 0 and st.st_mtime >= cutoff:
                out.append(("codex", str(p), st.st_mtime, st.st_size))

    kimi_dir = _get_kimi_dir()
    if kimi_dir.exists():
        for p in kimi_dir.glob("*/*/agents/main/wire.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > 0 and st.st_mtime >= cutoff:
                out.append(("kimi", str(p), st.st_mtime, st.st_size))

    gemini_dir = _get_gemini_dir()
    if gemini_dir.exists():
        for pattern in ("*/chats/session-*.json", "*/chats/session-*.jsonl"):
            for p in gemini_dir.glob(pattern):
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size > 0 and st.st_mtime >= cutoff:
                    out.append(("gemini", str(p), st.st_mtime, st.st_size))

    # One listdir per brain root + 2 stats per conversation (never a
    # recursive walk: chunks/ holds thousands of shards per long session).
    for brain in _get_agy_roots():
        if not brain.is_dir():
            continue
        for logs in brain.glob("*/.system_generated/logs"):
            for name in ("transcript_full.jsonl", "transcript.jsonl"):
                p = logs / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size > 0 and st.st_mtime >= cutoff:
                    out.append(("agy", str(p), st.st_mtime, st.st_size))
                    break

    cursor_dir = _get_cursor_dir()
    if cursor_dir.exists():
        for p in cursor_dir.glob("*/agent-transcripts/*/*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > 0 and st.st_mtime >= cutoff:
                out.append(("cursor", str(p), st.st_mtime, st.st_size))

    # Harvested worker-sandbox transcripts (multi-machine S8b): copies land
    # here preserving the source's .claude/projects or .codex/sessions
    # shape, so the same glob patterns as above apply one level deeper.
    harvested_dir = _get_harvested_dir()
    if harvested_dir.exists():
        for p in harvested_dir.glob("*/.claude/projects/*/*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > 0 and st.st_mtime >= cutoff:
                out.append(("claude", str(p), st.st_mtime, st.st_size))
        for p in harvested_dir.glob("*/.codex/sessions/**/*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > 0 and st.st_mtime >= cutoff:
                out.append(("codex", str(p), st.st_mtime, st.st_size))
    return out


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS sdoc USING fts5(
            sid UNINDEXED,
            title,
            prompts,
            report,
            body,
            meta,
            summary,
            tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS file_cache (
            path TEXT PRIMARY KEY,
            sid TEXT,
            mtime REAL,
            size INTEGER,
            indexed INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_file_cache_sid ON file_cache(sid);
        CREATE TABLE IF NOT EXISTS semb (
            sid TEXT,
            kind TEXT,
            vec BLOB
        );
        CREATE INDEX IF NOT EXISTS idx_semb_sid ON semb(sid);
        CREATE TABLE IF NOT EXISTS semb_pending (sid TEXT PRIMARY KEY);
        CREATE VIRTUAL TABLE IF NOT EXISTS ssec USING fts5(
            sid UNINDEXED,
            sec UNINDEXED,
            turn0 UNINDEXED,
            turn1 UNINDEXED,
            body,
            tasks,
            tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS ssec_map (sid TEXT, rid INTEGER);
        CREATE INDEX IF NOT EXISTS idx_ssec_map_sid ON ssec_map(sid);
    """)
    # MEMO-FIX-19: file_cache gained cwd/engine so the sidebar-search
    # endpoints (search-history, search-recall-sessions) can be served
    # straight from this index instead of a second per-harness store --
    # migration-safe ALTER for DBs built before these columns existed.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(file_cache)")}
    if "cwd" not in cols:
        conn.execute("ALTER TABLE file_cache ADD COLUMN cwd TEXT DEFAULT ''")
    if "engine" not in cols:
        conn.execute("ALTER TABLE file_cache ADD COLUMN engine TEXT DEFAULT ''")
    conn.commit()


def _delete_session_rows(conn: sqlite3.Connection, sid: str) -> None:
    conn.execute("DELETE FROM sdoc WHERE sid = ?", (sid,))
    conn.execute("DELETE FROM semb WHERE sid = ?", (sid,))
    conn.execute("DELETE FROM semb_pending WHERE sid = ?", (sid,))
    # ssec rows are deleted by rowid via ssec_map: `WHERE sid = ?` on an
    # UNINDEXED FTS column is a full scan, and ssec is the big table.
    conn.execute("DELETE FROM ssec WHERE rowid IN (SELECT rid FROM ssec_map WHERE sid = ?)", (sid,))
    conn.execute("DELETE FROM ssec_map WHERE sid = ?", (sid,))


def _insert_sections(conn: sqlite3.Connection, sid: str, sections: list[dict]) -> None:
    for s in sections:
        cur = conn.execute(
            "INSERT INTO ssec (sid, sec, turn0, turn1, body, tasks) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, s["sec"], s["turn0"], s["turn1"], s["body"], s["tasks"]),
        )
        conn.execute("INSERT INTO ssec_map (sid, rid) VALUES (?, ?)", (sid, cur.lastrowid))


def _section_migration_pending(conn: sqlite3.Connection) -> int:
    """Number of already-indexed transcripts that predate the current section
    schema and must be re-parsed (0 once migrated, or on a fresh DB)."""
    if conn.execute("PRAGMA user_version").fetchone()[0] >= SECTION_SCHEMA_VERSION:
        return 0
    n = conn.execute(
        "SELECT COUNT(*) FROM file_cache WHERE size > ? AND mtime >= 0", (_SECTION_MIGRATE_MIN_SIZE,),
    ).fetchone()[0]
    if n == 0:
        conn.execute(f"PRAGMA user_version = {SECTION_SCHEMA_VERSION}")
    return n


def _migrate_sections(conn: sqlite3.Connection) -> None:
    """Invalidate the (mtime, size) gate for transcripts big enough to need
    sections, so the sync that follows re-parses them. Background-only."""
    with conn:
        conn.execute("UPDATE file_cache SET mtime = -1 WHERE size > ?", (_SECTION_MIGRATE_MIN_SIZE,))
    conn.execute(f"PRAGMA user_version = {SECTION_SCHEMA_VERSION}")


def _kimi_sid_migration_pending(conn: sqlite3.Connection) -> int:
    """Number of already-indexed Kimi transcripts still keyed by the old
    `session_<uuid>` sid (0 once migrated to the bare-UUID form, or on a
    fresh DB)."""
    if conn.execute("PRAGMA user_version").fetchone()[0] >= KIMI_SID_SCHEMA_VERSION:
        return 0
    n = conn.execute(
        "SELECT COUNT(*) FROM file_cache WHERE engine = 'kimi' AND mtime >= 0",
    ).fetchone()[0]
    if n == 0:
        conn.execute(f"PRAGMA user_version = {KIMI_SID_SCHEMA_VERSION}")
    return n


def _migrate_kimi_sids(conn: sqlite3.Connection) -> None:
    """Invalidate the (mtime, size) gate for Kimi transcripts so the sync
    that follows re-parses them with the normalized bare-UUID sid."""
    with conn:
        conn.execute("UPDATE file_cache SET mtime = -1 WHERE engine = 'kimi'")
    conn.execute(f"PRAGMA user_version = {KIMI_SID_SCHEMA_VERSION}")


def _ollama_base() -> str:
    return os.environ.get("CCC_OLLAMA_URL", "http://localhost:11434")


def _ollama_available() -> bool:
    """Cheap liveness probe for the local Ollama daemon, cached for _OLLAMA_TTL.

    Any failure (not installed, not running, unreachable) means the embedding
    channel is skipped for this cycle; FTS-only search is unaffected.
    """
    if os.environ.get("CCC_SESSION_FTS_EMBED", "1") == "0":
        return False
    now = time.time()
    if now - _ollama_state["ts"] < _OLLAMA_TTL:
        return _ollama_state["ok"]
    ok = False
    try:
        req = urllib.request.Request(f"{_ollama_base()}/api/tags")
        with urllib.request.urlopen(req, timeout=_OLLAMA_PROBE_TIMEOUT) as resp:
            ok = resp.status == 200
    except Exception:
        ok = False
    _ollama_state["ts"] = now
    _ollama_state["ok"] = ok
    return ok


def _embed_texts(texts: list[str], batch: int = _EMBED_BATCH, timeout: float = _EMBED_TIMEOUT) -> list[list[float]] | None:
    """Embed texts via local Ollama. Returns None on any failure (caller degrades).

    `timeout` defaults to the batch/document budget (_EMBED_TIMEOUT); callers
    on a user-facing request path (see _vector_rank) pass a much shorter one
    so a cold model-load can't block that path.
    """
    if not texts:
        return []
    out: list[list[float]] = []
    base = _ollama_base()
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        body = json.dumps({
            "model": EMB_MODEL, "input": chunk, "truncate": True, "keep_alive": "10m",
        }).encode()
        req = urllib.request.Request(
            f"{base}/api/embed", data=body, headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except Exception:
            return None
        embeddings = data.get("embeddings")
        if not embeddings or len(embeddings) != len(chunk):
            return None
        out.extend(embeddings)
    return out


def _normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def _session_chunks(r: dict) -> list[tuple[str, str]]:
    """Embedding chunks for one session: a summary 'card' plus later user prompts,
    so a query can match either the overall session or one specific turn."""
    title = (r.get("title") or "").strip()
    card = f"{title}\n{(r.get('first_prompt') or '')[:1000]}\n{(r.get('final_text') or '')[:800]}"
    chunks = [("card", card)]
    prompts = (r.get("user_text") or "").split("\n---\n")[1:11]
    for p in prompts:
        if len(p.strip()) >= 25:
            chunks.append(("prompt", p[:700]))
    return chunks


def _rrf(lists: list[list[str]], k: int = 60, weights: list[float] | None = None) -> list[str]:
    """Reciprocal Rank Fusion over ranked sid lists, best first.

    `weights` (same length as `lists`) scales each channel's contribution;
    defaults to equal weight. See _VECTOR_RRF_WEIGHT below for why the
    caller in search_sessions() no longer passes equal weights for every
    channel.
    """
    if weights is None:
        weights = [1.0] * len(lists)
    sc: dict[str, float] = {}
    for lst, w in zip(lists, weights):
        for rank, sid in enumerate(lst, 1):
            sc[sid] = sc.get(sid, 0.0) + w / (k + rank)
    return [sid for sid, _ in sorted(sc.items(), key=lambda kv: -kv[1])]


# MEMORY-19: measured on 10 hand-verified real questions against this box's
# actual ~/.claude/projects transcripts (~/dev/scratch/memory-19-benchmark),
# the optional local-embeddings channel (_vector_rank, nomic-embed-text over
# a per-session "card" chunk) frequently fails to place the true best session
# anywhere in its own top-k: a session whose card doesn't happen to restate
# the query's topic in its title/first-prompt/final-text is invisible to it,
# even when that session is a clean top-1 keyword match. At the old *equal*
# RRF weight, that meant merely appearing somewhere in the vector channel's
# top-k -- regardless of true relevance -- was enough for a mediocre session
# to outrank a session that was the single best FTS hit but happened to get
# zero vector-channel credit (rank fell from #1 to #11 on one measured
# query). Down-weighting the vector channel relative to FTS preserves its
# intended job -- surfacing paraphrase-gap hits FTS finds nothing for, see
# test_rrf_fusion_surfaces_vector_only_match -- without letting it dilute
# strong lexical matches; test_rrf_weights_keep_fts_channel_authoritative_
# over_vector_noise reproduces the bug numerically and confirms 0.15 fixes
# it. Caveat: on the full 20-question real-data A/B
# (~/dev/scratch/memory-19-benchmark/rrf_weight_ab.py, repeated 4x against
# the live index) this made no measurable difference to end-to-end Hit@5/
# Hit@10 -- an earlier session in this same investigation logged a large
# improvement from this change, but it didn't reproduce afterward (most
# likely a run contaminated by an in-progress background index/embedding
# sync) and is not a real number. Kept anyway because the mechanism is
# real and the change is monotonically safer than equal-weighting a
# channel with demonstrated false-positive risk, not because it's proven
# to move this benchmark.
_VECTOR_RRF_WEIGHT = float(os.environ.get("CCC_SESSION_FTS_VECTOR_RRF_WEIGHT", "0.15"))


def _refresh_vec_cache(conn: sqlite3.Connection) -> None:
    sids: list[str] = []
    vecs: list[array] = []
    for sid, _kind, blob in conn.execute("SELECT sid, kind, vec FROM semb"):
        sids.append(sid)
        vecs.append(array("f", blob))
    _vec_cache["sids"] = sids
    _vec_cache["vecs"] = vecs


def _vector_rank(query: str, limit: int) -> list[str]:
    if not _vec_cache["sids"]:
        return []
    vecs = _embed_texts(["search_query: " + query], timeout=_QUERY_EMBED_TIMEOUT)
    if not vecs:
        return []
    qv = array("f", _normalize(vecs[0]))
    best: dict[str, float] = {}
    for sid, v in zip(_vec_cache["sids"], _vec_cache["vecs"]):
        sim = sum(a * b for a, b in zip(qv, v))
        cur = best.get(sid)
        if cur is None or sim > cur:
            best[sid] = sim
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    return [sid for sid, _ in ranked[:limit]]


def _hermes_channel_hits(query: str, limit: int) -> list[dict]:
    """Raw hits ({session_id, score, mtime, snippet}) from Hermes' own
    `messages_fts` table (closes TODO(hermes-search) in ccc_server/hermes.py)
    -- never CCC-parsed or CCC-cached, queried live and read-only on every
    call. Degrades to an empty channel on any failure (Hermes not installed,
    corrupt db, ...), same as the optional embeddings channel above."""
    try:
        return _hermes.search_hermes_messages(query, limit=limit)
    except Exception:
        return []


def _hermes_channel_sids(query: str, limit: int) -> list[str]:
    """Ranked Hermes-native session ids -- see _hermes_channel_hits."""
    return [h["session_id"] for h in _hermes_channel_hits(query, limit)]


def _drain_embeddings(conn: sqlite3.Connection, embed_jobs: list[tuple[str, list[tuple[str, str]]]]) -> None:
    """Embed newly-changed sessions plus a bounded slice of any backlog left
    over from a prior cycle where Ollama was unavailable or over-capacity."""
    if not _ollama_available():
        # Ollama down for this cycle: don't drop these sids on the floor --
        # queue them so a later cycle (or the backfill loop) picks them up.
        # This was the root cause of sessions indexed during an Ollama outage
        # (OPS-1251: ~/.ollama/models on an external drive) never getting
        # embedded -- they were parsed into sdoc/file_cache fine, but the
        # embed job for them was silently discarded right here.
        if embed_jobs:
            with conn:
                for sid, _chunks in embed_jobs:
                    conn.execute("INSERT OR IGNORE INTO semb_pending (sid) VALUES (?)", (sid,))
        return

    if not embed_jobs:
        pending = [r[0] for r in conn.execute("SELECT sid FROM semb_pending")]
        if pending:
            placeholders = ",".join("?" for _ in pending)
            rows = conn.execute(
                f"SELECT sid, title, prompts, report FROM sdoc WHERE sid IN ({placeholders})", pending,
            ).fetchall()
            for sid, title, prompts, report in rows:
                first_prompt = (prompts or "").split("\n---\n")[0]
                r = {"title": title, "first_prompt": first_prompt, "final_text": report, "user_text": prompts}
                embed_jobs.append((sid, _session_chunks(r)))

    if not embed_jobs:
        return

    jobs = embed_jobs[:_MAX_EMBED_SESSIONS_PER_SYNC]
    overflow = embed_jobs[_MAX_EMBED_SESSIONS_PER_SYNC:]

    with conn:
        for sid, _chunks in overflow:
            conn.execute("INSERT OR IGNORE INTO semb_pending (sid) VALUES (?)", (sid,))

        if not jobs:
            return

        texts, index_map = [], []
        for sid, chunks in jobs:
            for kind, text in chunks:
                index_map.append((sid, kind))
                texts.append("search_document: " + text)

        vecs = _embed_texts(texts)
        if vecs is None:
            for sid, _chunks in jobs:
                conn.execute("INSERT OR IGNORE INTO semb_pending (sid) VALUES (?)", (sid,))
            return

        for (sid, kind), v in zip(index_map, vecs):
            nv = _normalize(v)
            conn.execute("INSERT INTO semb (sid, kind, vec) VALUES (?, ?, ?)", (sid, kind, array("f", nv).tobytes()))
        for sid, _chunks in jobs:
            conn.execute("DELETE FROM semb_pending WHERE sid = ?", (sid,))


def _backfill_missing_embeddings(conn: sqlite3.Connection) -> int:
    """Queue one bounded slice of sdoc sids that have no semb row at all into
    semb_pending, so the normal drain picks them up. Covers sdoc rows that
    were indexed before semb_pending queueing existed (or from any other gap
    between sdoc and semb) -- never a full-corpus scan, and never called on a
    user-facing path. Returns the number of sids queued."""
    rows = conn.execute(
        """
        SELECT sid FROM sdoc
        WHERE sid NOT IN (SELECT sid FROM semb)
          AND sid NOT IN (SELECT sid FROM semb_pending)
        LIMIT ?
        """,
        (_MAX_EMBED_SESSIONS_PER_SYNC,),
    ).fetchall()
    if not rows:
        return 0
    with conn:
        for (sid,) in rows:
            conn.execute("INSERT OR IGNORE INTO semb_pending (sid) VALUES (?)", (sid,))
    return len(rows)


def _run_embedding_backfill() -> None:
    """Background-only catch-up loop for _backfill_missing_embeddings(): keep
    queueing and draining bounded slices until no sdoc sid is missing a semb
    row, backing off when Ollama is unavailable or a slice makes no progress
    (eg. a cold model-load timeout) instead of hammering it in a tight loop.
    Started once from warm_start(); idempotent while already running.
    """
    global _backfill_running
    with _backfill_lock:
        if _backfill_running:
            return
        _backfill_running = True

    def _worker() -> None:
        global _backfill_running
        try:
            conn = _connect(_get_db_path())
            try:
                while True:
                    queued = _backfill_missing_embeddings(conn)
                    pending = conn.execute("SELECT COUNT(*) FROM semb_pending").fetchone()[0]
                    if not queued and not pending:
                        break
                    if not _ollama_available():
                        time.sleep(_BACKFILL_RETRY_INTERVAL)
                        continue
                    _drain_embeddings(conn, [])
                    _refresh_vec_cache(conn)
                    still_pending = conn.execute("SELECT COUNT(*) FROM semb_pending").fetchone()[0]
                    if still_pending >= pending:
                        time.sleep(_BACKFILL_RETRY_INTERVAL)
            finally:
                conn.close()
        except Exception:
            pass
        finally:
            with _backfill_lock:
                _backfill_running = False

    threading.Thread(target=_worker, daemon=True, name="session-fts-backfill").start()


def _defer_embeddings(embed_jobs: list[tuple[str, list[tuple[str, str]]]]) -> None:
    """Run _drain_embeddings on its own connection on a background thread.

    Called for `force=False` syncs (a real request thread) so that embedding
    -- live Ollama network I/O -- never blocks the caller. Embeddings "join
    when ready": this thread commits them whenever it finishes, and the next
    search picks them up via the shared on-disk `semb` table / _vec_cache.
    """
    def _worker() -> None:
        try:
            conn2 = _connect(_get_db_path())
            try:
                _drain_embeddings(conn2, embed_jobs)
                _refresh_vec_cache(conn2)
            finally:
                conn2.close()
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True, name="session-fts-embed").start()


def _get_connection() -> sqlite3.Connection:
    if not hasattr(_tls, "conn") or _tls.conn is None:
        db_path = _get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _tls.conn = _connect(db_path)
    return _tls.conn


def _sync_index(conn: sqlite3.Connection, force: bool = False) -> None:
    global _last_sync_ts
    now = time.time()
    if not force and (now - _last_sync_ts < _SYNC_TTL):
        return

    # Non-blocking acquire for regular (non-forced) callers: if a sync is
    # already running -- most commonly the background warm/catch-up kicked
    # off below -- a request just uses whatever is indexed so far instead of
    # queueing up behind a multi-second (or cold-start, multi-minute) parse.
    # `force=True` (explicit force_refresh, and the background worker's own
    # call) still blocks for a fully deterministic result.
    got = _sync_lock.acquire(blocking=force)
    if not got:
        return
    try:
        if not force and (time.time() - _last_sync_ts < _SYNC_TTL):
            return

        _init_db(conn)

        # A large corpus needs a directory walk even when only one file
        # changed. Keep that scan off request threads on every expired
        # refresh, not just the first search after a restart. Count before
        # loading the cache rows: that too scales with the whole corpus.
        if not force:
            existing = conn.execute("SELECT COUNT(*) FROM file_cache").fetchone()[0]
            if existing > _BG_SYNC_THRESHOLD:
                _start_background_sync()
                return

        cur = conn.execute("SELECT path, sid, mtime, size, indexed FROM file_cache")
        have = {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.fetchall()}

        # MEMO-FIX-21: one-time section rebuild of already-indexed long
        # transcripts. Only ever done by a force=True (background) sync; a
        # request thread hands it off and answers from the existing index.
        if _section_migration_pending(conn):
            if not force:
                _start_background_sync()
                return
            _migrate_sections(conn)
            cur = conn.execute("SELECT path, sid, mtime, size, indexed FROM file_cache")
            have = {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.fetchall()}

        # MEMORY-18: one-time re-key of already-indexed Kimi sessions from
        # `session_<uuid>` to bare UUID. Same background-deferral shape as
        # the section migration above.
        if _kimi_sid_migration_pending(conn):
            if not force:
                _start_background_sync()
                return
            _migrate_kimi_sids(conn)
            cur = conn.execute("SELECT path, sid, mtime, size, indexed FROM file_cache")
            have = {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.fetchall()}

        files = _candidate_files()
        current_paths = {p for _, p, _, _ in files}

        todo = []
        for eng, path, mt, sz in files:
            cached = have.get(path)
            if cached is None or cached[1] != mt or cached[2] != sz:
                todo.append((eng, path, mt, sz))

        deleted_paths = [p for p in have if p not in current_paths]

        if not todo and not deleted_paths:
            _last_sync_ts = time.time()
            return

        # Cold start (or any large catch-up): don't block this request for
        # tens of seconds parsing thousands of transcripts. Hand the full
        # sync to a background thread and return with whatever is already
        # indexed; is_indexing() tells recall() to say so.
        if not force and len(todo) > _BG_SYNC_THRESHOLD:
            _last_sync_ts = time.time()
            _start_background_sync()
            return

        parsed_results = []
        if todo:
            items_to_parse = [(eng, path) for eng, path, _, _ in todo]
            if len(items_to_parse) > 16:
                with ThreadPoolExecutor(max_workers=8) as ex:
                    parsed_results = list(ex.map(_parse_file, items_to_parse, chunksize=16))
            else:
                parsed_results = [_parse_file(item) for item in items_to_parse]

        embed_jobs: list[tuple[str, list[tuple[str, str]]]] = []

        with conn:
            for (eng, path, mt, sz), r in zip(todo, parsed_results):
                old_cached = have.get(path)
                old_sid = old_cached[0] if old_cached else None

                if old_sid:
                    _delete_session_rows(conn, old_sid)

                if r and r.get("sid"):
                    sid = r["sid"]
                    if sid != old_sid:
                        _delete_session_rows(conn, sid)
                    if r.get("scratch", 0) == 0 and r.get("n_user", 0) > 0:
                        conn.execute(
                            "INSERT INTO sdoc VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                sid,
                                r["title"],
                                r["user_text"],
                                r["final_text"],
                                r["assistant_text"],
                                r["meta"],
                                r["summary"],
                            ),
                        )
                        _insert_sections(conn, sid, r.get("sections") or [])
                        conn.execute(
                            "INSERT OR REPLACE INTO file_cache (path, sid, mtime, size, indexed, cwd, engine) "
                            "VALUES (?, ?, ?, ?, 1, ?, ?)",
                            (path, sid, mt, sz, r.get("cwd", ""), eng),
                        )
                        embed_jobs.append((sid, _session_chunks(r)))
                    else:
                        conn.execute(
                            "INSERT OR REPLACE INTO file_cache (path, sid, mtime, size, indexed, cwd, engine) "
                            "VALUES (?, ?, ?, ?, 0, ?, ?)",
                            (path, sid, mt, sz, r.get("cwd", ""), eng),
                        )
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO file_cache (path, sid, mtime, size, indexed, cwd, engine) "
                        "VALUES (?, ?, ?, ?, 0, '', ?)",
                        (path, "", mt, sz, eng),
                    )

            for p in deleted_paths:
                old_sid = have[p][0]
                if old_sid:
                    _delete_session_rows(conn, old_sid)
                conn.execute("DELETE FROM file_cache WHERE path = ?", (p,))

        # FTS (sdoc/file_cache) is already committed above -- fast, no network.
        # Embedding is live Ollama I/O (MEMO-FIX-13: measured ~10s cold
        # model-load vs ~0.05s warm); a `force=False` caller is a real request
        # thread (recall()), so its embedding work is always backgrounded
        # instead of blocking. `force=True` (explicit force_refresh, and the
        # warm/backlog background worker's own call) still drains inline --
        # nothing besides that worker is waiting on it.
        if force:
            _drain_embeddings(conn, embed_jobs)
            _refresh_vec_cache(conn)
        elif embed_jobs:
            _defer_embeddings(embed_jobs)

        _last_sync_ts = time.time()
    finally:
        _sync_lock.release()


def is_indexing() -> bool:
    """True while a background cold-start/catch-up sync is in flight.

    recall() surfaces this so a caller hitting a cold index gets an honest
    'indexing: true' instead of silently-incomplete results.
    """
    return _bg_sync_running


def _start_background_sync() -> None:
    """Kick a full (blocking, force=True) sync on a background thread against
    its own connection. Idempotent while already running. This is what turns
    a 66s cold-start parse into a non-blocking warm-up: the request thread
    returns immediately and future requests see is_indexing() until it's done.
    """
    global _bg_sync_running
    with _bg_sync_state_lock:
        if _bg_sync_running:
            return
        _bg_sync_running = True

    def _worker() -> None:
        global _bg_sync_running
        try:
            conn2 = _connect(_get_db_path())
            try:
                _sync_index(conn2, force=True)
                # _sync_index() only refreshes _vec_cache when it actually
                # touched files -- an idle restart (nothing changed since
                # last sync) would otherwise leave _vec_cache empty forever,
                # even though `semb` already has embeddings on disk from a
                # prior run. Cheap local read; always safe to repeat.
                _refresh_vec_cache(conn2)
            finally:
                conn2.close()
            # Best-effort: force Ollama to load EMB_MODEL now, off the
            # request path, using the full document-embedding budget --
            # so a real query's own short _QUERY_EMBED_TIMEOUT doesn't have
            # to eat a cold model-load (MEMO-FIX-13).
            _prewarm_embed_model()
        except Exception:
            pass
        finally:
            with _bg_sync_state_lock:
                _bg_sync_running = False

    threading.Thread(target=_worker, daemon=True, name="session-fts-warm").start()


def _prewarm_embed_model() -> None:
    if not _ollama_available():
        return
    try:
        _embed_texts(["search_query: warm"], timeout=_EMBED_TIMEOUT)
    except Exception:
        pass


def warm_start() -> None:
    """Call once at process/server start to begin warming the index in the
    background before the first real request arrives, per CLAUDE.md's perf
    gates (no O(all sessions) work inline on a user-facing path)."""
    _start_background_sync()
    _run_embedding_backfill()


def _section_scores(conn: sqlite3.Connection, match: str, n: int) -> dict[str, float]:
    """Best (lowest) bm25 per session over its ssec sections for `match`.
    The ORDER BY/LIMIT bounds work by hit count, never by corpus size."""
    best: dict[str, float] = {}
    try:
        cur = conn.execute(
            f"SELECT sid, bm25(ssec, {SSEC_WEIGHTS_STR}) FROM ssec WHERE ssec MATCH ? "
            f"ORDER BY bm25(ssec, {SSEC_WEIGHTS_STR}) LIMIT ?",
            (match, n),
        )
    except sqlite3.OperationalError:
        return best
    for sid, score in cur.fetchall():
        if sid not in best or score < best[sid]:
            best[sid] = score
    return best


def _fuse_sections(scores: dict[str, float], sec: dict[str, float]) -> None:
    """A session's score is the better of its sdoc score and its best
    section's. Measured on the ccc-memory bench (main/judged/held-out) this
    is retrieval-neutral; scaling section scores up (x2, x3) or RRF-fusing
    the two lists all cost MRR (x3: held-out MRR 0.57 -> 0.35), so sections
    surface sessions sdoc can't see without reordering the ones it can."""
    for sid, score in sec.items():
        scores[sid] = min(scores.get(sid, 0.0), score)


def _match_expr(q: str) -> str:
    if _is_explicit_history_fts_query(q):
        return q
    terms = extract_history_terms(q, max_terms=25)
    return " OR ".join(f'"{t}"' for t in terms)


def section_matches(query: str, sids: list[str]) -> dict[str, dict]:
    """For each sid, the best-matching section for `query`: section index,
    turn range and an FTS snippet. One batched query over `sids`' sections
    (via ssec_map rowids, so it never scans other sessions' rows). Sessions
    with no section hit are absent. Reads only already-synced index state."""
    q = (query or "").strip()
    if not q or not sids:
        return {}
    match = _match_expr(q)
    if not match:
        return {}
    conn = _get_connection()
    placeholders = ",".join("?" for _ in sids)
    try:
        cur = conn.execute(
            f"""SELECT sid, sec, turn0, turn1, bm25(ssec, {SSEC_WEIGHTS_STR}),
                       snippet(ssec, -1, '[', ']', '…', 24)
                FROM ssec WHERE ssec MATCH ?
                  AND rowid IN (SELECT rid FROM ssec_map WHERE sid IN ({placeholders}))""",
            [match, *sids],
        )
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, dict] = {}
    best: dict[str, float] = {}
    for sid, sec, turn0, turn1, score, snip in rows:
        if sid in best and score >= best[sid]:
            continue
        best[sid] = score
        out[sid] = {
            "section": sec,
            "turn": turn0,
            "turn_end": turn1,
            "snippet": " ".join((snip or "").split())[:300],
        }
    return out


def _embed_model_dir() -> Path:
    override = os.environ.get("OLLAMA_MODELS")
    return Path(override) if override else (Path.home() / ".ollama" / "models")


def _embed_model_dir_status() -> dict:
    """MEMO-FIX-24: catches the OPS-1251 shape (the embedding model directory
    living on a share that can silently unmount, taking semantic search down
    with it) before it degrades search again. Stat-only, no subprocess."""
    raw = _embed_model_dir()
    try:
        resolved = raw.resolve(strict=True)
    except OSError:
        return {"path": str(raw), "resolved": None, "reachable": False, "on_volumes": None}
    return {
        "path": str(raw),
        "resolved": str(resolved),
        "reachable": True,
        "on_volumes": str(resolved).startswith("/Volumes/"),
    }


def _ollama_model_present() -> bool | None:
    """Whether EMB_MODEL shows up in `ollama list`. None if the daemon can't
    be asked (distinct from a confirmed-absent model)."""
    try:
        req = urllib.request.Request(f"{_ollama_base()}/api/tags")
        with urllib.request.urlopen(req, timeout=_OLLAMA_PROBE_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    names = [m.get("name", "") for m in (data.get("models") or []) if isinstance(m, dict)]
    prefix = EMB_MODEL.split(":")[0]
    return any(name.split(":")[0] == prefix for name in names)


def index_health() -> dict:
    """Cheap read-only snapshot of session-index and embeddings health for
    `ccc doctor` (MEMO-FIX-24). COUNT(*) queries plus the existing cached
    Ollama liveness probe only -- never triggers a sync, never spawns a
    subprocess, safe to poll on every doctor invocation."""
    conn = _get_connection()
    _init_db(conn)
    sdoc_rows = conn.execute("SELECT COUNT(*) FROM sdoc").fetchone()[0]
    semb_sids = conn.execute("SELECT COUNT(DISTINCT sid) FROM semb").fetchone()[0]
    semb_pending = conn.execute("SELECT COUNT(*) FROM semb_pending").fetchone()[0]
    ollama_ok = _ollama_available()
    return {
        "sdoc_rows": sdoc_rows,
        "semb_sids": semb_sids,
        "semb_pending": semb_pending,
        "last_sync_ts": _last_sync_ts or None,
        "ollama_reachable": ollama_ok,
        "embed_model_present": _ollama_model_present() if ollama_ok else None,
        "embed_model_dir": _embed_model_dir_status(),
    }


def _literal_phrase_query(q: str) -> str:
    """A whitespace-free literal made of several tokens (a file path, URL,
    filename, dotted/hyphenated identifier) becomes one quoted FTS5 phrase,
    so it matches that exact token sequence instead of OR-matching its
    pieces -- otherwise ".../paste-123.png" hits every session that ever
    mentioned any pasted image (CCC-1256). Anything else is returned as-is."""
    if not q or any(ch.isspace() for ch in q) or '"' in q:
        return q
    tokens = _HISTORY_LITERAL_TOKEN_RE.findall(q)
    if len(tokens) < 2:
        return q
    return '"' + " ".join(tokens) + '"'


_HISTORY_LITERAL_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def search_sessions(query: str, limit: int = 20, force_refresh: bool = False) -> list[dict]:
    """Search indexed sessions with BM25.

    Returns a list of dicts with 'session_id', ranked best first.
    """
    q = _literal_phrase_query((query or "").strip())
    if not q:
        return []

    conn = _get_connection()
    _sync_index(conn, force=force_refresh)

    if _is_explicit_history_fts_query(q):
        try:
            cur = conn.execute(
                f"SELECT sid, bm25(sdoc, {BM25_WEIGHTS_STR}) FROM sdoc WHERE sdoc MATCH ? ORDER BY bm25(sdoc, {BM25_WEIGHTS_STR}) LIMIT ?",
                (q, limit),
            )
            explicit = {r[0]: r[1] for r in cur.fetchall()}
        except sqlite3.OperationalError:
            return []
        for sid, score in _section_scores(conn, q, max(limit * 4, 100)).items():
            explicit[sid] = min(explicit.get(sid, 0.0), score)
        explicit_ranked = sorted(explicit, key=lambda s: explicit[s])
        hermes_sids = _hermes_channel_sids(q, limit)
        final = _rrf([explicit_ranked, hermes_sids]) if hermes_sids else explicit_ranked
        return [{"session_id": sid, "score": explicit.get(sid, 0.0)} for sid in final[:limit]]

    terms = extract_history_terms(q, max_terms=25)
    if not terms:
        return []

    and_q = " AND ".join(f'"{t}"' for t in terms)
    or_q = " OR ".join(f'"{t}"' for t in terms)

    scores: dict[str, float] = {}
    try:
        cur = conn.execute(
            f"SELECT sid, bm25(sdoc, {BM25_WEIGHTS_STR}) FROM sdoc WHERE sdoc MATCH ? ORDER BY bm25(sdoc, {BM25_WEIGHTS_STR}) LIMIT ?",
            (or_q, max(limit * 2, 50)),
        )
        for sid, score in cur.fetchall():
            scores[sid] = score
    except sqlite3.OperationalError:
        pass

    if and_q and and_q != or_q and len(terms) > 1:
        try:
            cur = conn.execute(
                f"SELECT sid, bm25(sdoc, {BM25_WEIGHTS_STR}) FROM sdoc WHERE sdoc MATCH ? ORDER BY bm25(sdoc, {BM25_WEIGHTS_STR}) LIMIT ?",
                (and_q, max(limit * 2, 50)),
            )
            for sid, score in cur.fetchall():
                if sid in scores:
                    scores[sid] = min(scores[sid], score)
                else:
                    scores[sid] = score
        except sqlite3.OperationalError:
            pass

    # MEMO-FIX-21: long sessions' dropped middles (and task/plan text) live
    # in ssec sections. Collapse to the best section per session and let it
    # compete with that session's sdoc score -- a section can only lift a
    # session, never push a whole-session match down.
    # Multi-term queries only take sections that match every term: a section
    # is a slice of one session, and OR-matching common words there is both
    # noisy and the slow half of the query.
    _fuse_sections(scores, _section_scores(conn, and_q, max(limit * 4, 100)))

    fts_sids = sorted(scores, key=lambda s: scores[s])

    # P2 hybrid: fuse the FTS ranking with a local-embeddings channel via RRF.
    # Skipped (silently) whenever Ollama isn't installed/running/warm, which
    # is the default for most users -- fts_sids alone is then the result,
    # identical to pre-embedding behavior.
    vector_sids = _vector_rank(q, max(limit * 2, 50)) if _ollama_available() else []
    # Hermes' own messages_fts as a third channel -- see _hermes_channel_sids.
    # Empty whenever Hermes isn't installed, same degrade-safe shape.
    hermes_sids = _hermes_channel_sids(q, max(limit * 2, 50))
    # MEMORY-19: FTS and Hermes (both exact lexical matches over real message
    # text) are trusted equally; the optional vector channel is down-weighted
    # -- see _VECTOR_RRF_WEIGHT.
    chan_lists = (fts_sids, vector_sids, hermes_sids)
    chan_weights = (1.0, _VECTOR_RRF_WEIGHT, 1.0)
    channels, weights = [], []
    for lst, w in zip(chan_lists, chan_weights):
        if lst:
            channels.append(lst)
            weights.append(w)
    final_sids = _rrf(channels, weights=weights) if channels else []

    return [{"session_id": sid, "score": scores.get(sid, 0.0)} for sid in final_sids[:limit]]


# --- Sidebar-search enrichment (MEMO-FIX-19) ----------------------------------
# search_sessions() above returns bare {session_id, score} hits. The two
# sidebar search endpoints (/api/search-history, /api/search-recall-sessions)
# and the Ask tab's retrieval both need session-level cwd/engine/mtime plus a
# highlighted snippet -- this is the one shared enrichment layer for all three,
# replacing what used to be a separate Claude-Index read (ccc_server/
# history_search.py) and a from-scratch per-harness byte scan
# (ccc_server/recent_search.py). Those two modules now call in here; see their
# thin wrapper functions for the day-window / "Nd"-string / snippet-cleanup
# details that are specific to each caller.


def _meta_for_sids(conn: sqlite3.Connection, sids: list[str]) -> dict[str, dict]:
    """Batched (path, cwd, engine, mtime) lookup for already-indexed sids --
    one bounded IN-clause query, no per-row file I/O.

    A sid the Hermes messages_fts channel surfaced (see
    _hermes_channel_sids) has no file_cache row -- CCC never parses Hermes
    transcripts into sdoc/file_cache -- so any sid still missing afterward
    falls back to hermes.hermes_meta_for_sids(), itself one bounded IN-clause
    query over each Hermes DB's `sessions` table."""
    if not sids:
        return {}
    placeholders = ",".join("?" for _ in sids)
    out: dict[str, dict] = {}
    try:
        cur = conn.execute(
            f"SELECT sid, path, cwd, engine, mtime FROM file_cache "
            f"WHERE sid IN ({placeholders}) AND indexed = 1",
            sids,
        )
        for sid, path, cwd, engine, mtime in cur.fetchall():
            out[sid] = {"path": path or "", "cwd": cwd or "", "engine": engine or "", "mtime": mtime or 0.0}
    except sqlite3.OperationalError:
        pass
    missing = [s for s in sids if s not in out]
    if missing:
        try:
            out.update(_hermes.hermes_meta_for_sids(missing))
        except Exception:
            pass
    return out


def _snippet_for_sids(conn: sqlite3.Connection, sids: list[str], or_q: str) -> dict[str, str]:
    """<mark>-highlighted snippet per sid via FTS5's own snippet() with
    automatic column selection (col=-1: whichever of title/prompts/report/
    body/meta has the most matches). Only covers sids that actually matched
    `or_q` -- a pure-vector (semantic-only) hit falls back to a plain excerpt
    via _plain_snippets below."""
    out: dict[str, str] = {}
    if not sids or not or_q:
        return out
    placeholders = ",".join("?" for _ in sids)
    try:
        cur = conn.execute(
            f"SELECT sid, snippet(sdoc, -1, '<mark>', '</mark>', '…', 20) FROM sdoc "
            f"WHERE sdoc MATCH ? AND sid IN ({placeholders})",
            [or_q, *sids],
        )
        for sid, sn in cur.fetchall():
            if sn and sid not in out:
                out[sid] = sn
    except sqlite3.OperationalError:
        pass
    return out


def _plain_snippets(conn: sqlite3.Connection, sids: list[str]) -> dict[str, str]:
    """Un-highlighted fallback excerpt (title + first prompts) for sids with
    no FTS match to snippet() -- semantic-only hits still get something to
    show instead of an empty preview."""
    out: dict[str, str] = {}
    if not sids:
        return out
    placeholders = ",".join("?" for _ in sids)
    try:
        cur = conn.execute(
            f"SELECT sid, title, prompts FROM sdoc WHERE sid IN ({placeholders})", sids,
        )
    except sqlite3.OperationalError:
        return out
    for sid, title, prompts in cur.fetchall():
        text = ((title or "") + " " + " ".join((prompts or "").split())).strip()
        out[sid] = text[:240]
    return out


def search_sessions_enriched(
    query: str,
    limit: int = 20,
    cwd_like: str | None = None,
    since_ts: float | None = None,
    source: str = "bm25",
) -> list[dict]:
    """Ranked session hits enriched for direct HTTP-response use.

    Shape matches what the sidebar's history/recall augmentation and the Ask
    tab's retrieval already expect (see static/app.js _mergeHistoryResults
    and ccc_server/ask.py merge_ask_hits): uuid, session_id, type, cwd,
    git_branch, timestamp, ts_unix, snippet, score, _source, transcript_path.

    `since_ts`/`cwd_like` are a post-filter over a generously-oversized
    candidate set (cheap: an indexed BM25/RRF query, not a corpus scan) rather
    than a SQL-level filter, since neither is exercised by a real caller
    today (recall-sessions and the sidebar's search-history call never send
    `cwd`) -- see the MEMO-FIX-19 ticket notes for the tradeoff.
    """
    q = (query or "").strip()
    if not q:
        return []
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 20

    hits = search_sessions(q, limit=max(limit * 5, 100))
    if not hits:
        return []

    conn = _get_connection()
    sids = [h["session_id"] for h in hits]
    meta = _meta_for_sids(conn, sids)

    phrase_q = _literal_phrase_query(q)
    if phrase_q != q:
        or_q = phrase_q
    else:
        terms = extract_history_terms(q, max_terms=25)
        or_q = " OR ".join(f'"{t}"' for t in terms) if terms else ""
    marked = _snippet_for_sids(conn, sids, or_q)
    plain: dict[str, str] | None = None
    hermes_snips: dict[str, str] | None = None

    cwd_filter = (cwd_like or "").strip()
    out = []
    for h in hits:
        sid = h["session_id"]
        m = meta.get(sid)
        if not m:
            continue
        mtime = m.get("mtime") or 0.0
        if since_ts is not None and mtime < since_ts:
            continue
        cwd = m.get("cwd") or ""
        if cwd_filter and cwd_filter not in cwd:
            continue
        snippet = marked.get(sid)
        if not snippet:
            if plain is None:
                plain = _plain_snippets(conn, sids)
            snippet = plain.get(sid, "")
        if not snippet and m.get("engine") == "hermes":
            # Hermes sids have no sdoc row for _snippet_for_sids/_plain_snippets
            # to read -- fall back to the matched-message snippet Hermes'
            # own messages_fts already produced.
            if hermes_snips is None:
                hermes_snips = {
                    hit["session_id"]: hit.get("snippet", "")
                    for hit in _hermes_channel_hits(q, max(limit * 5, 100))
                }
            snippet = hermes_snips.get(sid, "")
        out.append({
            "uuid": f"{source}:{sid}",
            "session_id": sid,
            "type": m.get("engine") or "",
            "cwd": cwd,
            "git_branch": "",
            "timestamp": (
                datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
                if mtime else ""
            ),
            "ts_unix": mtime,
            "snippet": snippet,
            "score": h.get("score", 0.0),
            "_source": source,
            "transcript_path": m.get("path") or "",
        })
        # MEMO-FIX-lineage: collapsing below can fold two of these `out` rows
        # into one, so keep building past `limit` (bounded by `hits`, itself
        # capped at limit*5/100 above) rather than an early break that could
        # leave fewer than `limit` rows after collapsing.
        if len(out) >= limit * 2:
            break
    out = _lineage.collapse_chain_hits(out, _sg._get_connection())[:limit]
    return out
