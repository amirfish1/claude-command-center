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
# UI range picker -> (days for the recent-transcript scan, days back for the
# history index's `since` filter). "any" leaves `since` unset (full history)
# but still bounds the recent scan at recent_search's own _MAX_DAYS — that
# module clamps internally, so 30 here is a real ceiling, not a guess.
_ASK_RANGE_DAYS = {"24h": 1, "7d": 7, "30d": 30, "any": 30}
_ASK_FS_LIMIT = 8
_ASK_FS_TIMEOUT_SEC = 4

_ASK_CITATION_RE = re.compile(r"\[\[session:([0-9A-Za-z][0-9A-Za-z_-]{4,63})\]\]")
_ASK_ACTION_RE = re.compile(r"\[\[action:spawn-continue:([0-9A-Za-z][0-9A-Za-z_-]{4,63})\]\]")
_ASK_MARK_RE = re.compile(r"</?mark>")
_ASK_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]+")
# Question scaffolding, not topic signal. "work"/"status"/"session" are here
# because they appear in nearly every Ask-tab question ("where did I work
# on…", "what's the status of…") and would AND-filter recall to nothing.
# "store"/"save"/"find"/"put"/"locate" are here for the same reason on file-
# location questions ("where did I store/save/put the X file").
_ASK_STOPWORDS = frozenset(
    "a about an and are as at can could did do does file files find for "
    "from how i in is it know locate me my of on or put save saved session "
    "sessions status store stored that the them these there this those to was "
    "way ways we were what when where which who with work worked working "
    "you".split()
)
# A question only pays for a Spotlight lookup when it plausibly names a file
# — a bare noun/extension hint, not every "where did I work on X".
_ASK_FS_HINT_RE = re.compile(
    r"\b(file|files|folder|download|downloaded|screenshot|recording|video|"
    r"photo|image|document|clip|mov|mp4|mkv|avi|png|jpe?g|gif|pdf|docx?|"
    r"xlsx?|pptx?|csv|zip|key|psd|ai|mp3|wav|txt|md)\b", re.IGNORECASE)
# "What triggers X / when does X run / is it a cron job" questions — the
# real answer lives in a launchd plist or a GitHub workflow's `on:` block,
# never in a chat transcript, so these need their own content search
# (see search_local_automation_hits) instead of the session-hit retrieval.
_ASK_TRIGGER_HINT_RE = re.compile(
    r"\b(trigger|triggers|triggered|schedule|scheduled|scheduling|cron|"
    r"crontab|launchd|automatic|automated|automation|hook|hooked|webhook|"
    r"workflow|pre-?flight|preflight|runs? when|what runs|when does|"
    r"when is|how often)\b", re.IGNORECASE)
_ASK_AUTOMATION_LIMIT = 6
_ASK_AUTOMATION_SNIPPET_MAX = 900
_LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
# The question's own framing words ("what TRIGGERS X", "is it a CRON job")
# never appear verbatim in a plist/workflow, so using them as match keys
# only adds noise — every job file that happens to contain "automatic"
# somewhere matches. Match on the SUBJECT words instead (e.g. "bym",
# "preflight"), which is `terms` minus this vocabulary.
_ASK_AUTOMATION_STOPWORDS = frozenset(
    "trigger triggers triggered schedule scheduled scheduling cron crontab "
    "launchd automatic automated automation hook hooked webhook workflow "
    "runs run often does when github push check checks job jobs task tasks "
    "test tested testing service services process background".split()
)
_PLIST_TRIGGER_KEY_RE = re.compile(
    r"<key>(Label|StartInterval|StartCalendarInterval|WatchPaths|RunAtLoad|"
    r"KeepAlive|ProgramArguments)</key>.*?(?=<key>|</dict>)", re.DOTALL)
_YAML_TRIGGER_RE = re.compile(r"^on:.*?(?=^\S|\Z)", re.DOTALL | re.MULTILINE)


def _norm_match_text(s):
    return (s or "").lower().replace("-", "").replace("_", "")


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


def looks_like_file_question(question):
    """True when the question plausibly names a file — gates the Spotlight
    lookup so ordinary "what did I work on" questions don't pay for it."""
    return bool(_ASK_FS_HINT_RE.search(question or ""))


def looks_like_trigger_question(question):
    """True when the question asks what fires/schedules something (a
    launchd job, a CI workflow) — gates search_local_automation_hits so
    ordinary questions don't pay for the extra file reads."""
    return bool(_ASK_TRIGGER_HINT_RE.search(question or ""))


def _automation_snippet(text, is_plist):
    """Pull just the trigger-defining lines (StartInterval, `on:` block…)
    instead of the file head — a plain head-of-file cap gets eaten by XML/
    YAML boilerplate before ever reaching the line that answers the
    question."""
    if is_plist:
        parts = [m.group(0).strip() for m in _PLIST_TRIGGER_KEY_RE.finditer(text)]
        if parts:
            return "\n".join(parts)[:_ASK_AUTOMATION_SNIPPET_MAX]
    else:
        m = _YAML_TRIGGER_RE.search(text)
        if m:
            return m.group(0).strip()[:_ASK_AUTOMATION_SNIPPET_MAX]
    return text.strip()[:_ASK_AUTOMATION_SNIPPET_MAX]


def _automation_match(words, name_l, text):
    """AND-match on the subject words (all must appear, in filename or
    content) — the same word-count that would zero out recall on the
    session-hit scan is exactly right here, because the subject words
    (a job/repo name) really should co-occur precisely in the one file
    that defines it. Hyphens are stripped on both sides so "pre-flight"
    matches a file literally named "...preflight...")."""
    haystack = _norm_match_text(name_l + " " + text)
    return all(_norm_match_text(w) in haystack for w in words)


def search_local_automation_hits(terms, repo_roots=None, limit=_ASK_AUTOMATION_LIMIT):
    """Content search over launchd job plists and GitHub Actions workflows —
    the actual trigger definition (StartInterval, StartCalendarInterval,
    WatchPaths, or a workflow's `on:` block) lives in these files, never in
    a chat transcript, so no amount of session retrieval will ever answer
    "what triggers X". Matches by term appearing in the filename or file
    content; empty on any read error so transcript hits still carry the
    rest of the answer. `repo_roots` scopes the workflow search to repos
    the user has actually been working in (derived from session hit cwds)
    rather than crawling the whole filesystem."""
    all_words = [w for w in (terms or []) if w][:6]
    if not all_words:
        return []
    # Prefer the subject words (drop the question's own "trigger/cron/
    # webhook" framing) so matching stays precise; if that empties the set
    # (a bare "what triggers this?" with no named subject), fall back to
    # the full word list rather than matching nothing.
    words = [w for w in all_words if w not in _ASK_AUTOMATION_STOPWORDS] or all_words
    hits = []

    if _LAUNCH_AGENTS_DIR.is_dir():
        try:
            plists = sorted(_LAUNCH_AGENTS_DIR.glob("*.plist"))
        except OSError:
            plists = []
        for p in plists:
            if len(hits) >= limit:
                break
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            if not _automation_match(words, p.name.lower(), text):
                continue
            hits.append({"path": str(p), "kind": "launchd job",
                         "snippet": _automation_snippet(text, is_plist=True)})

    seen_roots = set()
    for root in (repo_roots or []):
        if len(hits) >= limit:
            break
        root = str(root or "").strip()
        if not root or root in seen_roots:
            continue
        seen_roots.add(root)
        wf_dir = Path(root) / ".github" / "workflows"
        if not wf_dir.is_dir():
            continue
        try:
            wf_files = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
        except OSError:
            wf_files = []
        for p in wf_files:
            if len(hits) >= limit:
                break
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            if not _automation_match(words, p.name.lower(), text):
                continue
            hits.append({"path": str(p), "kind": "GitHub workflow",
                         "snippet": _automation_snippet(text, is_plist=False)})

    return hits[:limit]


def search_filesystem_hits(terms, limit=_ASK_FS_LIMIT, runner=None):
    """Spotlight (mdfind) filename lookup for "where did I store/save/put X"
    questions — those live on disk, not in any session transcript. AND-match
    on filename substrings (a raw full-text mdfind query is too noisy — it
    matches file *contents*, surfacing code and logs instead of the file
    itself). Scoped to the user's home dir; empty on any failure (missing
    mdfind, disabled Spotlight, timeout) so transcript hits still carry the
    rest of the answer. `terms` come from extract_ask_terms, whose charset
    excludes quotes, so building the query string from them is injection-safe."""
    words = [w for w in (terms or []) if w][:4]
    if not words:
        return []
    clause = " && ".join(f"kMDItemFSName == '*{w}*'c" for w in words)
    run = runner or subprocess.run
    try:
        proc = run(["mdfind", "-onlyin", str(Path.home()), clause],
                   capture_output=True, text=True, timeout=_ASK_FS_TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    paths = [p for p in (proc.stdout or "").splitlines() if p.strip()]
    return paths[:limit]


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


def build_ask_prompt(question, history, hits, fs_hits=None, automation_hits=None):
    q = str(question or "").strip()[:_ASK_QUESTION_MAX]
    lines = [
        "You are the Ask assistant inside Claude Command Center (CCC), a "
        "dashboard for the user's AI coding sessions.",
        "When the question is about the user's own sessions or work (e.g. "
        "\"when did I...\", \"where did we discuss...\", \"what session had "
        "...\"), answer using ONLY the numbered session hits below.",
        "Cite every session you mention strictly as [[session:ID]] — the "
        "exact ID from the hit, nothing else inside the brackets.",
        "Status and time facts about a session come only from the hit "
        "metadata; never invent sessions, statuses, or file paths.",
        "If the user asks to continue/resume work from a found session, "
        "append the marker [[action:spawn-continue:ID]] for that session at "
        "the end of your answer. Never emit it unless the user asked for a "
        "continuation.",
        "If the question is a general how-to or knowledge question that the "
        "session hits don't cover (e.g. \"how do I get a Mac notification "
        "when my Vercel deployment finishes?\"), just answer it directly "
        "and helpfully from your own knowledge — do not refuse and do not "
        "say the sessions don't cover it. Only say the hits don't answer it "
        "when the user was specifically asking about their own sessions.",
        "Prefer the most recent sessions — the user usually means work from "
        "the last few days to two weeks. Lead with the newest matches and "
        "give their dates; mention older matches only if nothing recent "
        "fits.",
        "Reply in short readable prose (2-6 sentences).",
    ]
    if fs_hits is not None:
        lines.append(
            "If the user asks where a FILE (not a session) is stored/saved, "
            "answer from the File matches list below — quote the path "
            "exactly as given, never invent or guess a path. If that list "
            "is empty or nothing fits, say the file wasn't found on disk.")
    if automation_hits is not None:
        lines.append(
            "If the user asks what TRIGGERS or SCHEDULES something (a "
            "background job, a pre-flight check, a CI workflow) — e.g. is "
            "it a GitHub push, a timer, a cron schedule — answer from the "
            "Automation configs list below, not from session snippets. "
            "Quote the actual trigger key/value verbatim (e.g. "
            "'StartInterval 3600 seconds = hourly', 'StartCalendarInterval "
            "= runs at a fixed time of day', 'on: push' or 'on: schedule: "
            "cron'). If that list is empty or nothing fits, say plainly "
            "that no matching automation config was found on disk — do not "
            "guess or hedge from vague past mentions.")
    lines.append("")
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
    if fs_hits is not None:
        lines += ["", "File matches (from Spotlight, may be incomplete):"]
        lines += [f"- {p}" for p in fs_hits] if fs_hits else ["(none found)"]
    if automation_hits is not None:
        lines += ["", "Automation configs found on disk (may be incomplete):"]
        if automation_hits:
            for h in automation_hits:
                lines.append(f"- {h['kind']}: {h['path']}")
                lines.append(f"  content: {h['snippet']}")
        else:
            lines.append("(none found)")
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
    return {k: hit.get(k) for k in ("id", "title", "repo", "status", "ts_unix", "cwd", "snippet")}


def _ask_range_window(range_key):
    """UI range code -> (days for the recent scan, `since` unix ts for the
    history index, or None for the whole index)."""
    key = str(range_key or "").strip().lower()
    days = _ASK_RANGE_DAYS.get(key, _ASK_RECENT_DAYS)
    since = None if key in ("", "any") else time.time() - days * 86400
    return days, since


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

    days, since = _ask_range_window(payload.get("range"))
    terms = extract_ask_terms(question)
    query = " ".join(terms)
    recent, hist_rows = [], []
    if query:
        # Each layer degrades independently: a locked index or a scan error
        # must not turn the whole Ask into a 500 — the model just gets fewer
        # (or zero) hits and says so.
        try:
            recent = (_core.search_recent_sessions(
                query, days=days, limit=_ASK_HIT_CAP) or {}).get("results") or []
        except Exception:
            recent = []
        try:
            hist_rows = (_core.search_conversation_history(
                query, limit=_ASK_HIT_CAP, since=since) or {}).get("results") or []
        except Exception:
            hist_rows = []

    # Count candidates before the display cap so the UI can show "N found"
    # even though only _ASK_HIT_CAP go to the model/results list.
    all_hits = merge_ask_hits(recent, hist_rows, cap=200)
    hit_count = len(all_hits)
    hits = enrich_ask_hits(all_hits[:_ASK_HIT_CAP])

    fs_hits = None
    if looks_like_file_question(question):
        try:
            fs_hits = _core.search_filesystem_hits(terms)
        except Exception:
            fs_hits = []

    automation_hits = None
    if looks_like_trigger_question(question):
        try:
            repo_roots = []
            seen_repo_roots = set()
            for h in hits:
                cwd = h.get("cwd")
                if cwd and cwd not in seen_repo_roots:
                    seen_repo_roots.add(cwd)
                    repo_roots.append(cwd)
            automation_hits = _core.search_local_automation_hits(terms, repo_roots)
        except Exception:
            automation_hits = []

    try:
        answer = run_ask_engine(
            engine, build_ask_prompt(question, history, hits, fs_hits, automation_hits),
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
        "hit_count": hit_count,
        "actions": parse_ask_actions(answer, [h["id"] for h in hits]),
        "engine": engine["engine"],
        "model": engine["model"],
        "elapsed_ms": int((time.time() - t0) * 1000),
    }, 200
