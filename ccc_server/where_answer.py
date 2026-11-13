# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
""""Where are we?" answer (MEMO-FIX-where): `ccc where <query|sid>` /
GET /api/memory/where/<query>.

Composes `ccc brief` (ccc_server/session_brief.py) across a session's whole
continuation chain (ccc_server/lineage.py), plus any runbook-shaped docs
those sessions referenced, into a bounded prompt for one headless, read-only
Claude call -- then renders the model's answer into safe HTML.

The model is asked for structured JSON only (status / action_items /
continue_prompt), never raw HTML: server-side rendering from a fixed schema
means nothing the model writes can inject markup, regardless of what the
underlying sessions or referenced docs contained. Tool access mirrors
ccc_server/ask.py's validated "read-only headless Claude" pattern --
`--allowedTools` alone does not restrict (see CLAUDE.md), so `--disallowedTools`
is always passed too.

Cached on disk by a hash of the chain's transcript mtimes plus any referenced
docs' mtimes, so re-opening the same "where are we" view without anything
having changed costs one file read, not a fresh model call.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
import time

from ccc_server import core as _core
from ccc_server import lineage as _lineage
from ccc_server import session_brief as _brief
from ccc_server import ship_graph as _sg
from ccc_server import test_isolation_active
from ccc_server.ask import _ASK_TOOL_ALLOWED, _ASK_TOOL_DISALLOWED

MODEL = "sonnet"
TIMEOUT_SEC = 120
MAX_DOC_CHARS = 6000
MAX_DOCS = 4
MAX_PROMPT_CHARS = 40_000

# Referenced-doc discovery is a plain filename regex over brief() text, not a
# tool call: the model only ever sees content this module chose to embed, so
# a stray backtick-quoted path in an old transcript can't make it read
# something outside the sessions' own repos.
_DOC_PATH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:md|txt)")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.M)
# Deep links render as <a href>; only these two schemes are considered safe
# to hand back to a browser unescaped in an href attribute.
_SAFE_LINK_RE = re.compile(r"^(https?://|file://)", re.I)


def _state_dir() -> str:
    if test_isolation_active():
        return tempfile.gettempdir()
    return os.environ.get("CCC_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center"
    )


def _cache_dir() -> str:
    d = os.path.join(_state_dir(), "where-cache")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _referenced_doc_paths(one_brief: dict, repo_root: str) -> list[str]:
    """Runbook/design-doc-shaped paths mentioned in a brief's own text
    (last asks, last reply, files touched) that actually exist on disk --
    e.g. the "apps/shop/docs/runbooks/ads-relaunch-build.md"
    a session's closing summary points a human at."""
    text = " ".join([
        one_brief.get("last_assistant_reply") or "",
        " ".join(one_brief.get("last_user_asks") or []),
        " ".join(one_brief.get("files_touched") or []),
    ])
    candidates = set(_DOC_PATH_RE.findall(text))
    out = []
    for c in candidates:
        if os.path.isabs(c):
            if os.path.isfile(c):
                out.append(os.path.normpath(c))
            continue
        for base in filter(None, [repo_root, one_brief.get("cwd")]):
            p = os.path.join(base, c)
            if os.path.isfile(p):
                out.append(os.path.normpath(p))
                break
    return out


def _read_bounded(path: str, max_chars: int = MAX_DOC_CHARS) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(max_chars + 1)
    except OSError:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def gather_chain(query_or_sid: str) -> dict:
    """Resolve `query_or_sid` to a session, then its whole continuation
    chain's `ccc brief` output plus any runbook-shaped docs those sessions
    referenced. Everything else in this module is prompt-building, the
    model call, and rendering."""
    resolved = _brief.resolve_session(query_or_sid)
    sid = resolved["session_id"]
    if not sid:
        return {"found": False, "query": query_or_sid}

    conn = _sg._get_connection()
    _sg._sync_all(conn, force=False)
    roots = _sg.discover_repo_roots()

    members = _lineage.continuation_chain_members(conn, sid)
    briefs = [_brief.brief(m) for m in members]
    root_sid = members[0]
    parent = _lineage.orchestrator_parent(root_sid)

    docs: dict[str, str] = {}
    for b in briefs:
        repo_root = roots.get(b.get("repo", ""), "") or b.get("cwd", "")
        for p in _referenced_doc_paths(b, repo_root):
            if p not in docs and len(docs) < MAX_DOCS:
                docs[p] = _read_bounded(p)

    return {
        "found": True,
        "query": query_or_sid,
        "session_id": sid,
        "chain_sids": members,
        "briefs": briefs,
        "parent": parent,
        "docs": [{"path": p, "text": t} for p, t in docs.items()],
    }


def _chain_cache_key(gathered: dict) -> str:
    conn = _sg._get_connection()
    parts = []
    for sid in gathered.get("chain_sids") or []:
        path = _brief._transcript_path(conn, sid)
        try:
            mtime = os.path.getmtime(path) if path else 0
        except OSError:
            mtime = 0
        parts.append(f"{sid}:{mtime}")
    for d in gathered.get("docs") or []:
        try:
            mtime = os.path.getmtime(d["path"])
        except OSError:
            mtime = 0
        parts.append(f"{d['path']}:{mtime}")
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _build_prompt(gathered: dict) -> str:
    lines = [
        'You are answering "where are we?" for a chain of Claude Code coding '
        "sessions, for the human who dispatched the work.",
        "Read the session summaries and any referenced documents below, then "
        "respond with ONLY a single JSON object (no markdown fences, no "
        "commentary before or after) matching exactly this shape:",
        '{"status": "<2-4 sentence plain-language status, no jargon>",',
        ' "action_items": [{"text": "<one concrete action a human must take>", '
        '"deep_link": "<url or absolute file path this action concerns, or null>"}],',
        ' "continue_prompt": "<one paragraph a new session could be spawned '
        'with to pick up the very next step -- empty string if nothing is left>"}',
        "",
        "Only list an action_item for something a HUMAN must do outside of "
        "code (e.g. click something in an external console, paste a value, "
        "approve a deploy) -- never further coding work an agent session "
        "could do itself. If a referenced doc names specific values (button "
        "labels, env var names, settings), quote them exactly. If there is "
        "truly nothing left for a human, return an empty action_items list.",
        "",
    ]
    for b in gathered.get("briefs") or []:
        lines.append(
            f"## Session {b.get('session_id')} "
            f"({b.get('start_date')} - {b.get('end_date')}, repo {b.get('repo')})"
        )
        if b.get("last_user_asks"):
            lines.append("Last asks: " + " | ".join(b["last_user_asks"]))
        if b.get("last_assistant_reply"):
            lines.append("Last reply: " + b["last_assistant_reply"])
        if b.get("commits"):
            lines.append(
                "Commits: " + ", ".join(f"{c['sha']} {c['subject']}" for c in b["commits"])
            )
        lines.append("")
    for d in gathered.get("docs") or []:
        lines.append(f"## Referenced doc: {d['path']}")
        lines.append(d["text"])
        lines.append("")
    return "\n".join(lines)[:MAX_PROMPT_CHARS]


def _call_sonnet(prompt: str, cwd: str, runner=None) -> str:
    """One headless Claude Code round-trip, read-only tools only. Mirrors
    ccc_server/ask.py's run_ask_tool_engine -- same "allow AND disallow"
    belt-and-braces (see CLAUDE.md: `--allowedTools` alone does not
    restrict), same Popen-args-not-shell invocation."""
    info = _core._resolve_claude_bin()
    if not info.get("available"):
        raise RuntimeError(info.get("reason") or "Claude Code CLI not found")
    run = runner or subprocess.run
    argv = [
        info["bin"], "-p", "--model", MODEL,
        "--allowedTools", _ASK_TOOL_ALLOWED,
        f"--disallowedTools={_ASK_TOOL_DISALLOWED}",
        "--permission-mode", "dontAsk",
        "--output-format", "json",
        "--strict-mcp-config", '--mcp-config={"mcpServers":{}}',
        prompt,
    ]
    proc = run(argv, capture_output=True, text=True, timeout=TIMEOUT_SEC, cwd=cwd)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[:300]
        raise RuntimeError(detail or f"claude exited {proc.returncode}")
    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise RuntimeError("claude returned empty output")
    try:
        envelope = json.loads(stdout)
    except ValueError as exc:
        raise RuntimeError("claude did not return a JSON envelope") from exc
    result = envelope.get("result") if isinstance(envelope, dict) else None
    if not result:
        raise RuntimeError("claude's JSON envelope had no result")
    return result


def _parse_model_answer(result_text: str) -> dict:
    cleaned = _FENCE_RE.sub("", result_text or "").strip()
    try:
        data = json.loads(cleaned)
    except ValueError:
        return {"status": cleaned[:500], "action_items": [], "continue_prompt": ""}
    if not isinstance(data, dict):
        return {"status": str(data)[:500], "action_items": [], "continue_prompt": ""}
    clean_items = []
    for it in data.get("action_items") or []:
        if isinstance(it, dict) and it.get("text"):
            clean_items.append({
                "text": str(it["text"]),
                "deep_link": str(it["deep_link"]) if it.get("deep_link") else None,
            })
    return {
        "status": str(data.get("status") or "").strip(),
        "action_items": clean_items,
        "continue_prompt": str(data.get("continue_prompt") or "").strip(),
    }


def _safe_href(link) -> str:
    if not link:
        return ""
    link = str(link).strip()
    if _SAFE_LINK_RE.match(link):
        return link
    if link.startswith("/"):
        return "file://" + link
    return ""


def render_html(parsed: dict, gathered: dict) -> str:
    status_html = html.escape(parsed.get("status") or "")

    items_html = []
    for it in parsed.get("action_items") or []:
        text_html = html.escape(it.get("text") or "")
        href = _safe_href(it.get("deep_link"))
        if href:
            items_html.append(
                '<li class="where-action-item"><a href="' + html.escape(href, quote=True)
                + '" target="_blank" rel="noopener">' + text_html + "</a></li>"
            )
        else:
            items_html.append('<li class="where-action-item">' + text_html + "</li>")
    checklist_html = (
        '<ul class="where-checklist">' + "".join(items_html) + "</ul>"
        if items_html else
        '<p class="where-no-actions">Nothing outstanding for you -- next step is more agent work.</p>'
    )

    briefs = gathered.get("briefs") or []
    last_brief = briefs[-1] if briefs else {}
    continue_prompt = parsed.get("continue_prompt") or ""
    continue_html = ""
    if continue_prompt:
        spawn_payload = {
            "prompt": continue_prompt,
            "cwd": last_brief.get("cwd") or "",
            "engine": last_brief.get("engine") or "claude",
            "name": "Continue: " + (gathered.get("session_id") or "")[:8],
            "report_to": gathered.get("parent") or "",
        }
        spawn_json_attr = html.escape(json.dumps(spawn_payload), quote=True)
        continue_html = (
            '<button type="button" class="where-continue-btn" data-spawn-payload="'
            + spawn_json_attr + '">Continue</button>'
        )

    return (
        '<div class="where-answer">'
        + '<div class="where-status">' + status_html + "</div>"
        + checklist_html
        + continue_html
        + "</div>"
    )


def answer_where(query_or_sid: str, runner=None) -> dict:
    """GET /api/memory/where/<query> -- see module docstring for sourcing.
    `found: False` means `query_or_sid` matched no session at all."""
    gathered = gather_chain(query_or_sid)
    if not gathered.get("found"):
        return {"found": False, "query": query_or_sid}

    cache_key = _chain_cache_key(gathered)
    cache_path = os.path.join(_cache_dir(), f"{cache_key}.json")
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("cache_key") == cache_key:
            cached["cached"] = True
            return cached
    except (OSError, ValueError):
        pass

    briefs = gathered.get("briefs") or []
    last_brief = briefs[-1] if briefs else {}
    cwd = last_brief.get("cwd") or tempfile.gettempdir()
    prompt = _build_prompt(gathered)
    result_text = _call_sonnet(prompt, cwd=cwd, runner=runner)
    parsed = _parse_model_answer(result_text)
    html_out = render_html(parsed, gathered)

    out = {
        "found": True,
        "session_id": gathered["session_id"],
        "chain_sids": gathered["chain_sids"],
        "parent": gathered.get("parent") or "",
        "html": html_out,
        # Structured form alongside the rendered HTML so a plain-text
        # consumer (the `ccc where` CLI) doesn't have to scrape markup.
        "status": parsed.get("status") or "",
        "action_items": parsed.get("action_items") or [],
        "continue_prompt": parsed.get("continue_prompt") or "",
        "cache_key": cache_key,
        "cached": False,
        "generated_at": time.time(),
    }
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(out, f)
    except OSError:
        pass
    return out
