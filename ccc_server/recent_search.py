# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Fast recent-session content search across every agent harness.

Powers the sidebar conversation search (/api/search-recall-sessions). The
previous implementation shelled out to the Total Recall CLI per keystroke —
an 8s-timeout subprocess that only indexed claude-code/codex. This replaces
it with an in-process scan of transcript files modified within the last N
days (default 2), covering Claude Code, Codex, Kimi Code, Gemini and Cursor
uniformly: if the bytes are on disk and recent, the session is findable.

Design notes:
- Newest-first, stop at `limit` hits: for "find my recent session about X"
  the freshest matches are the right ones, and early exit keeps per-keystroke
  cost bounded (~250MB of 2-day transcripts on a busy machine).
- Oversized transcripts are searched head+tail instead of a full read, so a
  single 200MB session can't blow the per-query budget.
- Result shape mirrors search_total_recall_sessions (session_id, cwd,
  ts_unix, snippet, _source) so the sidebar augmentation consumes it
  unchanged.
"""

from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DAYS = 2.0
_MAX_DAYS = 30.0
# A single transcript larger than this is searched as head+tail rather than
# read whole — matches in the middle of a gigantic session are sacrificed to
# keep the worst-case per-file read bounded.
_MAX_FILE_BYTES = 8 * 1024 * 1024
_HEAD_BYTES = 4 * 1024 * 1024
_TAIL_BYTES = 4 * 1024 * 1024
# Safety budget for one query across all files; scanning stops when exceeded.
_MAX_TOTAL_BYTES = 400 * 1024 * 1024
_SNIPPET_RADIUS = 160
_CWD_SNIFF_BYTES = 65536

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_CODEX_FILENAME_UUID_RE = re.compile(
    r"-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)
_KIMI_SESSION_DIR_RE = re.compile(r"session_([0-9a-f-]{36})", re.IGNORECASE)
_CWD_RE = re.compile(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _candidate_roots():
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME") or (home / ".codex"))
    return [
        home / ".claude" / "projects",
        codex_home / "sessions",
        home / ".kimi-code" / "sessions",
        home / ".gemini" / "tmp",
        home / ".cursor" / "projects",
    ]


def _iter_recent_files(cutoff):
    """Yield (path, mtime) for transcript files touched since `cutoff`,
    newest first across all harness roots."""
    seen = []
    for root in _candidate_roots():
        try:
            if not root.is_dir():
                continue
            for pattern in ("*.jsonl", "*.json"):
                for path in root.rglob(pattern):
                    try:
                        st = path.stat()
                    except OSError:
                        continue
                    if st.st_mtime >= cutoff and st.st_size > 0:
                        seen.append((path, st.st_mtime, st.st_size))
        except OSError:
            continue
    seen.sort(key=lambda item: item[1], reverse=True)
    return seen


def _session_id_for(path):
    """Best-effort session UUID from the file's own name or its parents."""
    m = _CODEX_FILENAME_UUID_RE.search(path.name)
    if m:
        return m.group(1)
    m = _UUID_RE.search(path.stem)
    if m:
        return m.group(0)
    for parent in path.parents:
        m = _KIMI_SESSION_DIR_RE.search(parent.name)
        if m:
            return m.group(1)
        m = _UUID_RE.search(parent.name)
        if m:
            return m.group(0)
        # Stop at the harness root — anything higher is the home dir.
        if parent.name in ("projects", "sessions", "tmp"):
            break
    return ""


def _read_searchable_bytes(path, size):
    """Full bytes for ordinary files; head+tail for oversized ones."""
    try:
        with open(path, "rb") as f:
            if size <= _MAX_FILE_BYTES:
                return f.read()
            head = f.read(_HEAD_BYTES)
            try:
                f.seek(-_TAIL_BYTES, os.SEEK_END)
            except OSError:
                pass
            return head + b"\n" + f.read(_TAIL_BYTES)
    except OSError:
        return None


def _sniff_cwd(blob):
    m = _CWD_RE.search(blob[:_CWD_SNIFF_BYTES].decode("utf-8", errors="replace"))
    if not m:
        return ""
    raw = m.group(1)
    try:
        return bytes(raw, "utf-8").decode("unicode_escape")
    except (UnicodeDecodeError, ValueError):
        return raw


def _make_snippet(blob, lower_blob, phrase, needles, mark_re):
    """Escaped context window with <mark> wraps — the same snippet contract
    the bm25 history results produce. Anchors on the full phrase when
    present, else on the FIRST query term (users lead with the key word:
    "Becky conversation" → anchor "becky", not the noisy "conversation").
    Matching ran on the lowercased copy; the display window is sliced from
    the ORIGINAL bytes at the same offsets (bytes.lower() is
    length-preserving) so titles keep their case. errors='replace' covers
    windows starting mid-codepoint."""
    first = lower_blob.find(phrase)
    if first < 0:
        first = lower_blob.find(needles[0])
    if first < 0:
        # First term only matched via a multi-byte fold; fall back to any term.
        for needle in needles:
            idx = lower_blob.find(needle)
            if idx >= 0 and (first < 0 or idx < first):
                first = idx
    if first < 0:
        return ""
    start = max(0, first - _SNIPPET_RADIUS)
    end = min(len(blob), first + _SNIPPET_RADIUS)
    text = blob[start:end].decode("utf-8", errors="replace")
    window = re.sub(r"\s+", " ", text).strip()
    if start > 0:
        window = "…" + window
    if end < len(blob):
        window = window + "…"
    escaped = html.escape(window)
    return mark_re.sub(lambda m: "<mark>" + m.group(0) + "</mark>", escaped)


def search_recent_sessions(query, days=_DEFAULT_DAYS, limit=20, cwd_like=None):
    """Search recent transcripts across all harnesses for session-level hits.

    `days` bounds the mtime window (default 2 — the "last day or two" case
    the sidebar search is for). All query terms must appear in the file
    (AND semantics, case-insensitive); results come back newest-first.
    """
    q = (query or "").strip()
    terms = [t.lower() for t in q.split() if t.strip()]
    if not terms:
        return {"results": []}
    needles = [t.encode("utf-8", errors="replace") for t in terms]
    try:
        days = float(days)
    except (TypeError, ValueError):
        days = _DEFAULT_DAYS
    days = max(0.25, min(days, _MAX_DAYS))
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 20
    cwd_filter = (cwd_like or "").strip()

    mark_re = re.compile(
        "|".join(re.escape(html.escape(t)) for t in sorted(terms, key=len, reverse=True)),
        re.IGNORECASE,
    )
    cutoff = time.time() - days * 86400

    out = []
    seen_sessions = set()
    total_read = 0
    for path, mtime, size in _iter_recent_files(cutoff):
        if len(out) >= limit or total_read >= _MAX_TOTAL_BYTES:
            break
        session_id = _session_id_for(path)
        if not session_id or session_id in seen_sessions:
            continue
        blob = _read_searchable_bytes(path, size)
        if not blob:
            continue
        total_read += len(blob)
        lower = blob.lower()
        if not all(needle in lower for needle in needles):
            continue
        cwd = _sniff_cwd(blob)
        if cwd_filter and cwd_filter not in cwd:
            continue
        snippet = _make_snippet(
            blob, lower, " ".join(terms).encode("utf-8", errors="replace"),
            needles, mark_re,
        ) or html.escape(session_id)
        seen_sessions.add(session_id)
        out.append({
            "uuid": f"recall:{session_id}",
            "session_id": session_id,
            "type": "recall",
            "cwd": cwd,
            "git_branch": "",
            "timestamp": datetime.fromtimestamp(mtime, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "ts_unix": mtime,
            "snippet": snippet,
            "score": len(out),
            "_source": "recall",
            "transcript_path": str(path),
        })
    return {"results": out}
