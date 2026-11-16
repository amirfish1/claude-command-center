"""Small, read-only worker usage snapshots, independent of the archive index.

Only timestamped usage receipts count as token activity. File writes, process
liveness and tool output are not evidence of a model call. Reads are bounded
to a transcript tail and shared by every browser tab through one refresh.
"""
import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path

TAIL_BYTES = 256 * 1024
_lock = threading.Lock()
_refreshing = False
_checked = 0.0
_snapshot = {"pending": True, "workers": [], "sampled_at": None}
_tails = {}
_paths = {}


def _timestamp(value):
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        return ts if math.isfinite(ts) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def _positive_usage(usage):
    return isinstance(usage, dict) and any(
        isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
        for k, v in usage.items() if k.endswith("tokens"))


def read_tail(path):
    """Return the last actual token receipt and the latest explicit checklist."""
    path = Path(path)
    stat = path.stat()
    key = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if key in _tails:
        return _tails[key]
    with path.open("rb") as stream:
        offset = max(0, stat.st_size - TAIL_BYTES)
        stream.seek(offset)
        data = stream.read(TAIL_BYTES)
    lines = data.splitlines()
    if offset:
        lines = lines[1:]  # A partial leading record cannot establish usage.
    last_token_at, plan = 0, None
    seen = set()
    for raw in lines:
        try:
            rec = json.loads(raw)
            if not isinstance(rec, dict):
                continue
            ts = _timestamp(rec.get("timestamp"))
            payload = rec.get("payload") or {}
            message = rec.get("message") or {}
            if not isinstance(payload, dict) or not isinstance(message, dict):
                continue
            if rec.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") or {}
                usage = info.get("last_token_usage") or {}
                identity = json.dumps(info.get("total_token_usage") or usage, sort_keys=True)
                if _positive_usage(usage) and identity not in seen:
                    last_token_at = max(last_token_at, ts)
                    seen.add(identity)
            if rec.get("type") == "assistant" and message.get("model") != "<synthetic>":
                identity = message.get("id")
                if identity and identity not in seen and _positive_usage(message.get("usage")):
                    last_token_at = max(last_token_at, ts)
                    seen.add(identity)
            tools = message.get("content") if isinstance(message.get("content"), list) else []
            if rec.get("type") == "response_item" and payload.get("type") == "function_call":
                tools = [payload]
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                name = str(tool.get("name") or "").split(".")[-1]
                if name not in ("update_plan", "TodoWrite"):
                    continue
                args = tool.get("input") or tool.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                steps = args.get("plan" if name == "update_plan" else "todos")
                if isinstance(steps, list) and steps and all(isinstance(s, dict) for s in steps):
                    plan = {"done": sum(s.get("status") == "completed" for s in steps), "total": len(steps)}
        except (ValueError, TypeError, AttributeError):
            continue
    result = {"last_token_at": last_token_at or None, "plan": plan}
    # Keep only the newest version of each file, with a finite working set.
    for old in list(_tails):
        if old[0] == str(path):
            del _tails[old]
    if len(_tails) >= 256:
        _tails.pop(next(iter(_tails)))
    _tails[key] = result
    return result


def build_snapshot(workers, resolve):
    rows, seen = [], set()
    for worker in workers:
        sid = worker.get("session_id") or worker.get("worker_id")
        if not sid or sid in seen or worker.get("released_at") or worker.get("alive") is not True:
            continue
        seen.add(sid)
        evidence = {"last_token_at": None, "plan": None}
        try:
            path = _paths.get(sid)
            if not path or not Path(path).is_file():
                path = resolve(worker)
                if path:
                    if len(_paths) >= 256:
                        _paths.pop(next(iter(_paths)))
                    _paths[sid] = path
            if path:
                evidence = read_tail(path)
        except (OSError, ValueError, TypeError):
            pass
        rows.append({"worker_id": worker.get("worker_id"), "session_id": worker.get("session_id"),
                     "repo_path": worker.get("repo_path") or "", "queue": worker.get("queue") or "",
                     "engine": worker.get("engine") or "", **evidence})
    return {"pending": False, "workers": rows, "sampled_at": time.time()}


def activity_snapshot(load_workers, resolve):
    global _refreshing
    with _lock:
        if not _refreshing and time.monotonic() - _checked >= 10:
            _refreshing = True
            def refresh():
                global _snapshot, _checked, _refreshing
                try:
                    result = build_snapshot(load_workers(), resolve)
                    with _lock:
                        _snapshot = result
                except Exception:
                    pass  # Keep the dated snapshot; clients label it stale.
                finally:
                    with _lock:
                        _checked = time.monotonic()
                        _refreshing = False
            try:
                threading.Thread(target=refresh, daemon=True, name="worker-activity-refresh").start()
            except RuntimeError:
                _refreshing = False
        return dict(_snapshot)
