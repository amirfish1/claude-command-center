# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Ask tab — retrieval-then-answer assistant over CCC's session corpus.

Powers POST /api/assistant/ask (the right-rail Ask tab). Stage 1 gathers
candidate sessions from the existing search layers (recent transcript scan +
history index) plus warm in-memory metadata — no LLM, no subprocess, no cold
parses. Stage 2 makes ONE headless cheap-model CLI call (Antigravity `agy`
print mode first, Claude Code haiku fallback) whose answer cites sessions as
[[session:ID]] markers the UI renders as clickable chips.

Design rules (spec: CCC-private-docs/specs/2026-08-30-ask-tab-design.md):
- Stateless server: follow-up context is client-resent history, capped here.
- The model proposes, never invents: citations are validated against the hit
  set; status/branch facts travel in the prompt from CCC's own metadata.
- Perf: enrichment reads warm maps only; retrieval budgets live in the
  underlying search modules.
"""

from __future__ import annotations

import html
import os
import re
import subprocess
import time
from pathlib import Path

from ccc_server import core as _core

_ASK_HIT_CAP = 12
_ASK_HISTORY_TURNS = 4
_ASK_QUESTION_MAX = 2000
_ASK_SNIPPET_MAX = 220
_ASK_RECENT_DAYS = 14
_ASK_TIMEOUT_SEC = 90
_ASK_DEFAULT_AGY_MODEL = "gemini-3.7-flash-low"
_ASK_DEFAULT_CLAUDE_MODEL = "haiku"

_ASK_CITATION_RE = re.compile(r"\[\[session:([0-9A-Za-z][0-9A-Za-z_-]{4,63})\]\]")
_ASK_ACTION_RE = re.compile(r"\[\[action:spawn-continue:([0-9A-Za-z][0-9A-Za-z_-]{4,63})\]\]")
_ASK_MARK_RE = re.compile(r"</?mark>")
_ASK_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]+")
# Question scaffolding, not topic signal. "work"/"status"/"session" are here
# because they appear in nearly every Ask-tab question ("where did I work
# on…", "what's the status of…") and would AND-filter recall to nothing.
_ASK_STOPWORDS = frozenset(
    "a about an and are as at can could did do does for from how i in is it "
    "me my of on or session sessions status that the them these this those "
    "to was we were what when where which who with work worked working you".split()
)


def extract_ask_terms(question):
    """Topic keywords for the AND-semantics recent scan.

    Natural questions carry scaffolding ("where did I work on…") that would
    zero out recall under all-terms-must-match; keep only topic words, and
    fall back to the raw words when the whole question is scaffolding.
    """
    words = [w.lower() for w in _ASK_WORD_RE.findall(question or "")]
    seen, out = set(), []
    for w in words:
        if w in _ASK_STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:6] if out else words[:6]


def merge_ask_hits(recent, history, cap=_ASK_HIT_CAP):
    """Interleave recent-scan and history-index results, deduped by session
    id, then sort newest-first by ts_unix before the cap is applied. Both
    feeds already AND-match every query term, so recency is the right
    tiebreak — the owner's questions are almost always about the last few
    days to two weeks, not whatever an interleave position happens to
    surface. Missing/None ts_unix sorts as oldest (0)."""
    out, seen = [], set()

    def _push(row, source):
        sid = str(row.get("session_id") or "").strip()
        if not sid or sid in seen:
            return
        seen.add(sid)
        snippet = _ASK_MARK_RE.sub("", str(row.get("snippet") or ""))
        out.append({
            "id": sid,
            "cwd": str(row.get("cwd") or ""),
            "ts_unix": row.get("ts_unix") or None,
            "snippet": html.unescape(snippet)[:_ASK_SNIPPET_MAX],
            "source": source,
        })

    rec, hist = list(recent or []), list(history or [])
    for i in range(max(len(rec), len(hist))):
        if i < len(rec):
            _push(rec[i], "recent")
        if i < len(hist):
            _push(hist[i], "history")
    out.sort(key=lambda h: h.get("ts_unix") or 0, reverse=True)
    return out[:cap]


def enrich_ask_hits(hits, titles=None, live_ids=None):
    """Attach title/repo/status from warm state only — never trigger a
    transcript parse or subprocess. `titles`/`live_ids` are injectable for
    tests; production reads the auto-title map and the live-pid discovery
    that every list render already maintains."""
    if titles is None:
        try:
            titles = _core._auto_titled_session_ids() or {}
        except Exception:
            titles = {}
    if live_ids is None:
        try:
            live_ids = set(_core._discover_live_session_ids() or [])
        except Exception:
            live_ids = set()
    for h in hits:
        h["title"] = str(titles.get(h["id"]) or "").strip()
        h["repo"] = Path(h["cwd"]).name if h.get("cwd") else ""
        h["status"] = "live" if h["id"] in live_ids else "idle"
    return hits


def build_ask_prompt(question, history, hits):
    q = str(question or "").strip()[:_ASK_QUESTION_MAX]
    lines = [
        "You are the Ask assistant inside Claude Command Center (CCC), a "
        "dashboard for the user's AI coding sessions.",
        "Answer the user's question about their own sessions using ONLY the "
        "numbered session hits below.",
        "Cite every session you mention strictly as [[session:ID]] — the "
        "exact ID from the hit, nothing else inside the brackets.",
        "Status and time facts come only from the hit metadata; never invent "
        "sessions, statuses, or file paths.",
        "If the user asks to continue/resume work from a found session, "
        "append the marker [[action:spawn-continue:ID]] for that session at "
        "the end of your answer. Never emit it unless the user asked for a "
        "continuation.",
        "If the hits do not answer the question, say so plainly and suggest "
        "a more specific phrase to ask with.",
        "Prefer the most recent sessions — the user usually means work from "
        "the last few days to two weeks. Lead with the newest matches and "
        "give their dates; mention older matches only if nothing recent "
        "fits.",
        "Reply in short readable prose (2-6 sentences).",
        "",
    ]
    folded = [t for t in (history or []) if isinstance(t, dict)][-_ASK_HISTORY_TURNS:]
    for turn in folded:
        uq = str(turn.get("q") or "").strip()[:500]
        ua = str(turn.get("a") or "").strip()[:800]
        if uq:
            lines.append(f"Previous question: {uq}")
        if ua:
            lines.append(f"Previous answer: {ua}")
    if folded:
        lines.append("")
    lines.append("Session hits:")
    if not hits:
        lines.append("(none found)")
    for n, h in enumerate(hits, 1):
        ts = ""
        if h.get("ts_unix"):
            try:
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(h["ts_unix"])))
            except (TypeError, ValueError, OSError):
                ts = ""
        meta = " | ".join(x for x in (h.get("title"), h.get("repo"), h.get("status"), ts) if x)
        lines.append(f"{n}. [[session:{h['id']}]] {meta}")
        if h.get("snippet"):
            lines.append(f"   snippet: {h['snippet']}")
    lines += ["", f"Question: {q}"]
    return "\n".join(lines)


def parse_ask_citations(answer, known_ids):
    """Ordered unique session ids the answer cites, restricted to the hit
    set — an id the model made up must not become a clickable chip."""
    known = {str(k) for k in (known_ids or [])}
    ordered = []
    for m in _ASK_CITATION_RE.finditer(answer or ""):
        sid = m.group(1)
        if sid in known and sid not in ordered:
            ordered.append(sid)
    return ordered


def parse_ask_actions(answer, known_ids):
    """Ordered unique spawn-continue action targets, restricted to the hit
    set. The model proposes; the UI's confirm button is what executes."""
    known = {str(k) for k in (known_ids or [])}
    ordered = []
    for m in _ASK_ACTION_RE.finditer(answer or ""):
        sid = m.group(1)
        if sid in known and sid not in ordered:
            ordered.append(sid)
    return ordered


def select_ask_engine():
    """Pick the cheap-answer engine: agy (user's Antigravity/Gemini flash
    tokens) first, Claude haiku fallback. CCC_ASK_ENGINE forces one;
    CCC_ASK_MODEL overrides the model for whichever engine wins."""
    forced = (os.environ.get("CCC_ASK_ENGINE") or "").strip().lower()
    model = (os.environ.get("CCC_ASK_MODEL") or "").strip()

    def _agy():
        info = _core._resolve_antigravity_bin()
        if info.get("available"):
            return {"available": True, "engine": "antigravity", "bin": info["bin"],
                    "model": model or _ASK_DEFAULT_AGY_MODEL}
        return None

    def _claude():
        info = _core._resolve_claude_bin()
        if info.get("available"):
            return {"available": True, "engine": "claude", "bin": info["bin"],
                    "model": model or _ASK_DEFAULT_CLAUDE_MODEL}
        return None

    if forced == "antigravity":
        pick = _agy()
    elif forced == "claude":
        pick = _claude()
    else:
        pick = _agy() or _claude()
    if pick:
        return pick
    return {
        "available": False,
        "code": "ask_engine_unavailable",
        "error": "No Ask engine found: install Antigravity (agy) or Claude "
                 "Code, or point CCC_ANTIGRAVITY_BIN / CCC_CLAUDE_BIN at one.",
    }


def ask_engine_argv(engine, prompt):
    if engine["engine"] == "antigravity":
        return [engine["bin"], "--print", prompt, "--model", engine["model"],
                "--effort", "low", "--disable-slash-commands"]
    return [engine["bin"], "-p", "--model", engine["model"],
            "--strict-mcp-config", '--mcp-config={"mcpServers":{}}', prompt]


def run_ask_engine(engine, prompt, runner=None):
    """One headless CLI round-trip. `runner` is subprocess.run-compatible,
    injected by tests. cwd is the scratch dir so throwaway session artifacts
    stay out of repo scans (same as the auto-titler / queue brief)."""
    run = runner or subprocess.run
    scratch = Path(str(_core._SCRATCH_DIR))
    try:
        scratch.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    proc = run(ask_engine_argv(engine, prompt), capture_output=True, text=True,
               timeout=_ASK_TIMEOUT_SEC, cwd=str(scratch))
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[:300]
        raise RuntimeError(detail or f"{engine['engine']} exited {proc.returncode}")
    text = (proc.stdout or "").strip()
    if not text:
        raise RuntimeError(f"{engine['engine']} returned empty output")
    return text


def _ask_source_row(hit):
    return {k: hit.get(k) for k in ("id", "title", "repo", "status", "ts_unix", "cwd")}


def handle_assistant_ask(payload, runner=None):
    """POST /api/assistant/ask body -> (response dict, HTTP status)."""
    payload = payload if isinstance(payload, dict) else {}
    question = str(payload.get("question") or "").strip()
    if not question:
        return {"ok": False, "error": "question is required", "code": "ask_bad_request"}, 400
    history = payload.get("history")
    if not isinstance(history, list):
        history = []
    t0 = time.time()

    engine = select_ask_engine()
    if not engine.get("available"):
        return {"ok": False, "error": engine["error"], "code": engine["code"]}, 503

    query = " ".join(extract_ask_terms(question))
    recent, hist_rows = [], []
    if query:
        # Each layer degrades independently: a locked index or a scan error
        # must not turn the whole Ask into a 500 — the model just gets fewer
        # (or zero) hits and says so.
        try:
            recent = (_core.search_recent_sessions(
                query, days=_ASK_RECENT_DAYS, limit=_ASK_HIT_CAP) or {}).get("results") or []
        except Exception:
            recent = []
        try:
            hist_rows = (_core.search_conversation_history(
                query, limit=_ASK_HIT_CAP) or {}).get("results") or []
        except Exception:
            hist_rows = []

    hits = enrich_ask_hits(merge_ask_hits(recent, hist_rows))

    try:
        answer = run_ask_engine(engine, build_ask_prompt(question, history, hits),
                                runner=runner)
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "ask_timeout",
                "error": f"{engine['engine']} timed out after {_ASK_TIMEOUT_SEC}s"}, 504
    except (OSError, RuntimeError) as e:
        return {"ok": False, "code": "ask_engine_error", "error": str(e)[:300]}, 502

    cited = parse_ask_citations(answer, [h["id"] for h in hits])
    by_id = {h["id"]: h for h in hits}
    sources = [_ask_source_row(by_id[sid]) for sid in cited]
    sources += [_ask_source_row(h) for h in hits if h["id"] not in cited]
    return {
        "ok": True,
        "answer": answer,
        "sources": sources[:_ASK_HIT_CAP],
        "actions": parse_ask_actions(answer, [h["id"] for h in hits]),
        "engine": engine["engine"],
        "model": engine["model"],
        "elapsed_ms": int((time.time() - t0) * 1000),
    }, 200
