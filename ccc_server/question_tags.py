# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Index of owner-question tags `[Q<n>:<slug>]` across Claude transcripts.

Agents tag every question to the owner as `[Q33:gap-month]`; the FIRST
assistant message carrying a tag holds the full explanation and later
reminders repeat only the tag. This module finds that first message (plus
later mentions) so the UI can resolve a chip back to its explanation and list
questions that are still open.

A question is answered when the owner's reply mentions its tag, or an agent
writes `[Q<n>] answered`.

Cost model: no model calls. A transcript is only parsed when it changed
((mtime, size) differs from the persisted cache) and only when a cheap bytes
regex finds a tag in it; per-file results are cached on disk so the cold scan
of every transcript happens once.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

_TAG_BYTES = re.compile(rb"\[Q\d+[:\]]")
_TAG = re.compile(r"\[Q(\d+):([a-z0-9-]+)\]")
_ANSWERED = re.compile(r"\[Q(\d+)\]\s*answered", re.IGNORECASE)
_BARE = re.compile(r"\[Q(\d+)(?::[a-z0-9-]+)?\]")
_TEXT_CAP = 12000

_lock = threading.Lock()
_files: dict[str, dict] = {}  # path -> {"m": mtime, "s": size, "ev": [...]}
_loaded = False
_scanning = False
_last_scan = 0.0
_SCAN_MIN_GAP = 20.0


def _state_path() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "command-center" / "question-tags-cache.json"


def _projects_root() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "projects"


def _load_cache() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(_state_path().read_text())
        if isinstance(data, dict) and data.get("v") == 1:
            _files.update(data.get("files") or {})
    except (OSError, ValueError):
        pass


def _save_cache() -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".qtags-")
        with os.fdopen(fd, "w") as f:
            json.dump({"v": 1, "files": _files}, f)
        os.replace(tmp, p)
    except OSError:
        pass


def _blocks_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
            parts.append(b["text"])
    return "\n".join(parts)


def scan_file(path: str) -> list[dict]:
    """Tag events in one transcript: assistant/user text that mentions a tag."""
    sid = Path(path).stem
    events = []
    try:
        with open(path, "rb") as f:
            for raw in f:
                if not _TAG_BYTES.search(raw):
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                role = obj.get("type")
                if role not in ("assistant", "user") or obj.get("isSidechain"):
                    continue
                text = _blocks_text((obj.get("message") or {}).get("content"))
                if not _BARE.search(text):
                    continue
                events.append({
                    "role": role,
                    "uuid": obj.get("uuid") or "",
                    "ts": obj.get("timestamp") or "",
                    "sid": obj.get("sessionId") or sid,
                    "text": text[:_TEXT_CAP],
                })
    except OSError:
        return []
    return events


def refresh(max_age: float = _SCAN_MIN_GAP, force: bool = False) -> bool:
    """Re-scan changed transcripts. Returns False if a scan is already running
    or ran within `max_age` seconds."""
    global _scanning, _last_scan
    with _lock:
        _load_cache()
        if _scanning or (not force and time.time() - _last_scan < max_age):
            return False
        _scanning = True
    try:
        seen = set()
        dirty = False
        root = _projects_root()
        try:
            dirs = [d for d in root.iterdir() if d.is_dir()]
        except OSError:
            dirs = []
        for d in dirs:
            try:
                entries = list(d.glob("*.jsonl"))
            except OSError:
                continue
            for fp in entries:
                key = str(fp)
                seen.add(key)
                try:
                    st = fp.stat()
                except OSError:
                    continue
                rec = _files.get(key)
                if rec and rec["m"] == st.st_mtime and rec["s"] == st.st_size:
                    continue
                _files[key] = {"m": st.st_mtime, "s": st.st_size, "ev": scan_file(key)}
                dirty = True
        for key in [k for k in _files if k not in seen]:
            del _files[key]
            dirty = True
        if dirty:
            _save_cache()
        return True
    finally:
        with _lock:
            _scanning = False
            _last_scan = time.time()


def build_index(files: dict | None = None) -> dict:
    """Pure: fold per-file events into {"questions": [...]} ordered by n."""
    events = []
    for rec in (files if files is not None else _files).values():
        events.extend(rec.get("ev") or [])
    events.sort(key=lambda e: e.get("ts") or "")
    qs: dict[int, dict] = {}
    for e in events:
        text = e["text"]
        if e["role"] == "assistant":
            for m in _TAG.finditer(text):
                n = int(m.group(1))
                q = qs.get(n)
                if q is None:
                    qs[n] = q = {
                        "n": n, "tag": m.group(0), "slug": m.group(2),
                        "first": {"sid": e["sid"], "ts": e["ts"], "uuid": e["uuid"], "text": text},
                        "mentions": [], "answered": False, "answered_by": "",
                    }
                elif e["uuid"] != q["first"]["uuid"] and len(q["mentions"]) < 50:
                    q["mentions"].append({"sid": e["sid"], "ts": e["ts"], "uuid": e["uuid"]})
            for m in _ANSWERED.finditer(text):
                q = qs.get(int(m.group(1)))
                if q and not q["answered"]:
                    q["answered"], q["answered_by"] = True, "agent"
        else:
            for m in _BARE.finditer(text):
                q = qs.get(int(m.group(1)))
                if q and not q["answered"] and e["ts"] >= q["first"]["ts"]:
                    q["answered"], q["answered_by"] = True, "owner"
    return {"questions": [qs[n] for n in sorted(qs)]}


def payload() -> dict:
    """API payload. The cold scan runs in a background thread; `scanning`
    tells the UI the list may still be filling in."""
    with _lock:
        _load_cache()
        stale = time.time() - _last_scan >= _SCAN_MIN_GAP and not _scanning
    if stale:
        if not _last_scan:
            threading.Thread(target=refresh, daemon=True, name="qtags-scan").start()
        else:
            refresh()
    out = build_index()
    out["ok"] = True
    out["scanning"] = _scanning
    return out
