"""Background / harness runs: headless `claude -p` sessions run from OS temp dirs.

Eval harnesses and scripts launch `claude -p` with cwd=os.tmpdir(). Their
transcripts land in ``~/.claude/projects/<temp-dir-slug>`` where no CCC view
looks, so their tokens slipped past every dashboard. This module scans ONLY
those temp-dir project folders (never the whole projects tree), attributes each
transcript to a spawner via its spawn marker (``spawned_via`` / ``caller`` +
``kind``), and buckets markerless runs under ``unknown``.

Cost model: the directory listing is one scandir per temp project; token totals
reuse the per-file (mtime, size) cache behind the stats pipeline, so a warm poll
re-parses nothing. Totals only: session ids never leave this module.
"""

from __future__ import annotations

import os
import time

from ccc_server import core as _core

UNKNOWN = "unknown"
_TEMP_SLUG_PREFIXES = ("-private-var-folders-", "-var-folders-", "-private-tmp", "-tmp")
_HOUR = 3600
_DAY = 86400
_CACHE = {"ts": 0.0, "payload": None}
_CACHE_TTL = 15


def is_temp_project_slug(name):
    """True for a ~/.claude/projects folder whose cwd was an OS temp dir."""
    return str(name or "").startswith(_TEMP_SLUG_PREFIXES)


def _temp_transcripts(projects_root):
    """Yield (sid, path, mtime) for every transcript under temp-dir projects."""
    try:
        dirs = [e for e in os.scandir(projects_root) if is_temp_project_slug(e.name)]
    except OSError:
        return
    for d in dirs:
        try:
            files = list(os.scandir(d.path))
        except OSError:
            continue
        for f in files:
            if not f.name.endswith(".jsonl"):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            yield f.name[: -len(".jsonl")], f.path, st.st_mtime


def _tokens(path):
    agg = _core._stats_get_file_agg(path) or {}
    total = {"in": 0, "cache": 0, "out": 0, "messages": 0}
    for day in (agg.get("by_date") or {}).values():
        total["in"] += day.get("in_tokens", 0)
        total["cache"] += day.get("cache_tokens", 0)
        total["out"] += day.get("out_tokens", 0)
        total["messages"] += day.get("messages", 0)
    return total


def _spawner_key(marker):
    marker = marker or {}
    via = marker.get("spawned_via") or marker.get("caller") or UNKNOWN
    return via, marker.get("kind") or ""


def background_runs_payload(now=None, projects_root=None, markers=None, use_cache=True):
    now = time.time() if now is None else now
    if use_cache and _CACHE["payload"] is not None and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["payload"]
    root = projects_root or _core.PROJECTS_ROOT
    markers = _core._load_spawn_markers() if markers is None else markers
    groups = {}
    for sid, path, mtime in _temp_transcripts(root):
        via, kind = _spawner_key(markers.get(sid))
        g = groups.setdefault((via, kind), {
            "spawner": via, "kind": kind, "runs": 0, "runs_last_hour": 0,
            "runs_last_day": 0, "in_tokens": 0, "cache_tokens": 0,
            "out_tokens": 0, "tokens_last_day": 0, "last_run_ts": 0.0,
        })
        t = _tokens(path)
        age = now - mtime
        g["runs"] += 1
        g["in_tokens"] += t["in"]
        g["cache_tokens"] += t["cache"]
        g["out_tokens"] += t["out"]
        if age <= _HOUR:
            g["runs_last_hour"] += 1
        if age <= _DAY:
            g["runs_last_day"] += 1
            g["tokens_last_day"] += t["in"] + t["cache"] + t["out"]
        g["last_run_ts"] = max(g["last_run_ts"], mtime)
    rows = sorted(groups.values(), key=lambda g: (-g["runs_last_hour"], -g["tokens_last_day"]))
    payload = {
        "ok": True,
        "groups": rows,
        "totals": {
            "runs": sum(g["runs"] for g in rows),
            "runs_last_hour": sum(g["runs_last_hour"] for g in rows),
            "runs_last_day": sum(g["runs_last_day"] for g in rows),
            "tokens_last_day": sum(g["tokens_last_day"] for g in rows),
        },
        "alerts": [
            {"spawner": g["spawner"], "kind": g["kind"], "runs_last_hour": g["runs_last_hour"]}
            for g in rows if g["runs_last_hour"] >= _core.BACKGROUND_RUNS_HOURLY_ALERT
        ],
    }
    if use_cache:
        _CACHE.update(ts=now, payload=payload)
    return payload
