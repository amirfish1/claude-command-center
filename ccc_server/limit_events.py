from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import threading
import time

from ccc_server import core as _core

_lock = threading.RLock()
_cache = {}
_cache_path = None
_dirty = False
_LIMIT = re.compile(r"rate.?limit|usage.?limit|quota|usage_limit_exceeded|reached[^.]*limit|too many requests|429", re.I)
_WEEKLY = re.compile(r"week(?:ly)?|seven.day|7.day", re.I)


def signature(path):
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def load_store(path, cache, lock):
    with lock:
        sig = signature(path)
        if cache.get("data") is not None and cache.get("signature") == sig:
            return cache["data"]
        try:
            data = json.loads(path.read_text()) if sig else {}
        except (OSError, ValueError):
            data = {}
        cache.update(data=data if isinstance(data, dict) else {}, signature=sig)
        return cache["data"]


def capture_candidates(engine, now):
    entries = list(getattr(_core, "_spawned_sessions", []) or [])
    entries += list(_core._disk_spawn_entries_cached() or [])
    out = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("engine") != engine:
            continue
        sid = entry.get("resumed_sid") or entry.get("session_id")
        raw_path = entry.get("log")
        if not isinstance(sid, str) or not isinstance(raw_path, str):
            continue
        if engine == "devin" and not sid.startswith("devincli-"):
            sid = "devincli-" + sid
        path = Path(raw_path)
        sig = signature(path)
        if sig and now - sig[1] / 1e9 <= 86400:
            out[(sid, str(path))] = (sid, path)
    return list(out.values())


def _timestamp(event, path):
    dt = _core._stats_parse_ts(event.get("ts") or event.get("timestamp"))
    if dt is not None:
        return dt.timestamp()
    sig = signature(path)
    return sig[1] / 1e9 if sig else time.time()


def _epoch(raw, after):
    if isinstance(raw, str):
        dt = _core._stats_parse_ts(raw)
        raw = dt.timestamp() if dt else None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw) if math.isfinite(raw) and raw > after else None
    return None


def _exhausted(raw):
    return isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 100


def _text(event):
    value = event.get("result") or event.get("error") or event.get("errors") or ""
    if isinstance(value, dict):
        value = value.get("message") or value.get("codex_error_info") or ""
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    message = event.get("message")
    content = message.get("content", []) if isinstance(message, dict) else []
    if isinstance(content, str):
        value = str(value) + " " + content
    elif isinstance(content, list):
        value = str(value) + " " + " ".join(
            str(b.get("text") or "") for b in content
            if isinstance(b, dict) and b.get("type") == "text")
    return str(value).strip()


def claude_stop(sid, path):
    for line in reversed(_core._tail_read_lines(path)):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "result":
            if not event.get("is_error"):
                return None
        elif kind == "assistant":
            if not event.get("isApiErrorMessage"):
                return None
        else:
            continue
        blob = _text(event)
        if not _LIMIT.search(blob):
            return None
        detected = _timestamp(event, path)
        live = _core._live_weekly_usage() or {}
        weekly = bool(_WEEKLY.search(blob))
        blocks = [
            ("five_hour", live.get("session_pct"), live.get("session_resets_at")),
            ("weekly", live.get("weekly_pct"), live.get("weekly_resets_at")),
        ]
        exhausted = [(name, _epoch(reset, detected)) for name, pct, reset in blocks
                     if _exhausted(pct) or (weekly and name == "weekly")]
        known = [(name, epoch) for name, epoch in exhausted if epoch]
        if known:
            window, reset = max(known, key=lambda pair: pair[1])
            estimated = False
        else:
            window = "weekly" if weekly or _exhausted(live.get("weekly_pct")) else "five_hour"
            reset = _epoch(live.get("weekly_resets_at" if window == "weekly" else "session_resets_at"), detected)
            if reset is None and not exhausted:
                reset = _epoch(live.get("weekly_resets_at"), detected)
                if reset:
                    window = "weekly"
            estimated = reset is None
            reset = reset or detected + (7 * 86400 if window == "weekly" else 5 * 3600)
        return _attach({"engine": "claude", "detected_at": detected,
                        "resume_at": reset, "resume_at_estimated": estimated,
                        "limit_window": window}, sid, path)
    return None


def codex_stop(sid, path):
    limits = {}
    found = None
    for line in _core._tail_read_lines(path):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        kind = payload.get("type")
        if kind == "token_count":
            if isinstance(payload.get("rate_limits"), dict):
                limits = payload["rate_limits"]
            continue
        if kind in ("turn.completed", "task_started", "turn.started"):
            found = None
            continue
        if kind not in ("task_complete", "turn.failed"):
            continue
        err = payload.get("error")
        if payload.get("last_agent_message") is not None or not isinstance(err, dict):
            found = None
            continue
        blob = " ".join(
            str(err.get(key)) for key in ("message", "codex_error_info")
            if isinstance(err.get(key), str) and err.get(key))
        if not _LIMIT.search(blob):
            found = None
            continue
        detected = _timestamp(event, path)
        blocks = [("five_hour", limits.get("primary") or {}),
                  ("weekly", limits.get("secondary") or {})]
        exhausted = [(name, _epoch(block.get("resets_at"), detected))
                     for name, block in blocks if isinstance(block, dict) and (
                         _exhausted(block.get("used_percent")) or
                         (_WEEKLY.search(blob) and name == "weekly"))]
        known = [(name, epoch) for name, epoch in exhausted if epoch]
        if known:
            window, reset = max(known, key=lambda pair: pair[1])
        else:
            weekly = bool(_WEEKLY.search(blob)) or any(name == "weekly" for name, _ in exhausted)
            window = "weekly" if weekly else "five_hour"
            block = limits.get("secondary" if weekly else "primary") or {}
            reset = _epoch(block.get("resets_at"), detected) if isinstance(block, dict) else None
            if reset is None and not exhausted:
                secondary = limits.get("secondary") or {}
                reset = _epoch(secondary.get("resets_at"), detected) if isinstance(secondary, dict) else None
                if reset:
                    window = "weekly"
        estimated = reset is None
        found = {"engine": "codex", "detected_at": detected,
                 "resume_at": reset or detected + (7 * 86400 if window == "weekly" else 5 * 3600),
                 "resume_at_estimated": estimated, "limit_window": window}
    return _attach(found, sid, path) if found else None


def _attach(found, sid, path):
    entries = list(getattr(_core, "_spawned_sessions", []) or [])
    entries += list(_core._disk_spawn_entries_cached() or [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw = sid.removeprefix("devincli-")
        if entry.get("resumed_sid") in (sid, raw) or entry.get("session_id") in (sid, raw):
            found.update(display_name=entry.get("name"), cwd=entry.get("cwd"),
                         model=entry.get("model"), effort=entry.get("reasoning_effort"),
                         context_tokens=0)
            return found
    return _core._usage_limit_attach_continuation_fields(found, sid, path)


def cached_stop(engine, sid, path, detector):
    global _cache_path, _cache, _dirty
    cache_path = _core.COMMAND_CENTER_STATE_DIR / "limit-detection-cache.json"
    key = engine + ":" + str(path)
    sig = signature(path)
    if sig is None:
        return None
    with _lock:
        if cache_path != _cache_path:
            try:
                data = json.loads(cache_path.read_text())
            except (OSError, ValueError):
                data = {}
            _cache = data if isinstance(data, dict) else {}
            _cache_path = cache_path
        rec = _cache.get(key) or {}
        if rec.get("signature") == list(sig):
            return dict(rec["found"]) if isinstance(rec.get("found"), dict) else None
        found = detector(sid, path)
        if found:
            found = dict(found)
            found["source_text_snippet"] = engine + " usage / rate limit reached"
            found["transcript_path"] = str(path)
        _cache[key] = {"signature": list(sig), "found": found, "checked_at": time.time()}
        cutoff = time.time() - 8 * 86400
        _cache = {k: v for k, v in _cache.items()
                  if isinstance(v, dict) and v.get("checked_at", 0) >= cutoff}
        _dirty = True
        return found


def flush():
    global _dirty
    with _lock:
        if not _dirty or _cache_path is None:
            return
        _cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = _cache_path.with_name(f"{_cache_path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(_cache))
        os.replace(tmp, _cache_path)
        _dirty = False
