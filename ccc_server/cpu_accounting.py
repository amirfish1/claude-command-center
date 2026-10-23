"""Where does the dashboard process spend its CPU?

The server is one Python process behind ThreadingHTTPServer, so every request
and background loop shares one GIL. When the process sits near 100% of a core,
every endpoint slows down at once -- a 130ms conversation open becomes 10s
(CCC-143). The [SLOW] log only shows the victims' wall time; this module
records the culprits.

* Per request: thread CPU (``time.thread_time``) and wall time, bucketed by a
  route with ids collapsed (``/api/conversations/:id``).
* Per minute: every live thread's CPU from ``/proc/self/task/<tid>/stat``,
  keyed by Python thread name, so background loops are attributed too.

Once a window closes, one ``[CPU]`` line goes to stdout (service.out.log) and
one JSON row is appended to ``cpu-by-route.jsonl`` in the state dir. Stdlib
only; every hook is best-effort and never raises into the request path.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

try:
    from ccc_server.paths import COMMAND_CENTER_STATE_DIR
except Exception:  # pragma: no cover - import-path fallback
    COMMAND_CENTER_STATE_DIR = Path.home() / ".claude" / "command-center"

WINDOW_S = float(os.environ.get("CCC_CPU_WINDOW_S", "60") or 60)
LOG_PATH = COMMAND_CENTER_STATE_DIR / "cpu-by-route.jsonl"
LOG_MAX_BYTES = 8 * 1024 * 1024
TOP_N = 12

_ID_SEG = re.compile(
    r"^(?:[0-9a-f]{8}-[0-9a-f-]{20,}|[0-9a-f]{12,}|\d+|"
    r"(?:devincli|hermes|codex|kimi|gemini|cursor|pkood|spawning|backlog|issue)[-_].+)$",
    re.I,
)

_lock = threading.Lock()
_routes = {}            # route -> [cpu_ms, wall_ms, count]
_window_t0 = time.monotonic()
_proc_cpu0 = time.process_time()
_thread_cpu0 = {}       # native tid -> cpu seconds at window start
_CLK = None


def route_of(path):
    """Collapse ids in a request path so routes aggregate."""
    p = (path or "?").split("?", 1)[0]
    segs = p.split("/")
    return "/".join(":id" if s and _ID_SEG.match(s) else s for s in segs) or "/"


def _clk_tck():
    global _CLK
    if _CLK is None:
        try:
            _CLK = os.sysconf("SC_CLK_TCK")
        except (ValueError, OSError, AttributeError):
            _CLK = 100
    return _CLK


def _thread_cpu_s(tid):
    """utime+stime of one native thread, in seconds (Linux only)."""
    try:
        with open(f"/proc/self/task/{tid}/stat", "rb") as f:
            raw = f.read().decode("ascii", "replace")
    except OSError:
        return None
    # comm can hold spaces/parens; fields resume after the last ')'.
    rest = raw.rsplit(")", 1)[-1].split()
    try:
        return (int(rest[11]) + int(rest[12])) / _clk_tck()
    except (IndexError, ValueError):
        return None


def _thread_snapshot():
    out = {}
    for t in threading.enumerate():
        tid = getattr(t, "native_id", None)
        if tid is None:
            continue
        cpu = _thread_cpu_s(tid)
        if cpu is not None:
            out[tid] = (t.name, cpu)
    return out


def _normalize_thread_name(name):
    # "Thread-123 (process_request_thread)" -> "process_request_thread"
    m = re.match(r"^Thread-\d+ \((.+)\)$", name or "")
    return m.group(1) if m else (name or "?")


def record(path, cpu_s, wall_s, now=None):
    """Account one finished request; closes the window when it is due."""
    try:
        route = route_of(path)
        with _lock:
            row = _routes.get(route)
            if row is None:
                row = _routes[route] = [0.0, 0.0, 0]
            row[0] += cpu_s * 1000.0
            row[1] += wall_s * 1000.0
            row[2] += 1
        maybe_flush(now)
    except Exception:
        pass


def maybe_flush(now=None):
    """Emit the window summary if WINDOW_S has elapsed. Returns the row."""
    global _window_t0, _proc_cpu0, _thread_cpu0, _routes
    now = time.monotonic() if now is None else now
    with _lock:
        if now - _window_t0 < WINDOW_S:
            return None
        span = now - _window_t0
        routes, _routes = _routes, {}
        proc_now = time.process_time()
        proc_ms = (proc_now - _proc_cpu0) * 1000.0
        _proc_cpu0 = proc_now
        _window_t0 = now
        prev = _thread_cpu0
        snap = _thread_snapshot()
        _thread_cpu0 = {tid: cpu for tid, (_n, cpu) in snap.items()}
    threads = {}
    for tid, (name, cpu) in snap.items():
        base = prev.get(tid)
        if base is None:
            continue  # thread born inside the window: its requests are in routes
        d = (cpu - base) * 1000.0
        key = _normalize_thread_name(name)
        # Keep-alive handler threads span windows; their CPU is already in
        # the per-request route totals.
        if d >= 1 and key != "process_request_thread":
            threads[key] = threads.get(key, 0.0) + d
    req_ms = sum(r[0] for r in routes.values())
    top_routes = sorted(routes.items(), key=lambda kv: -kv[1][0])[:TOP_N]
    top_threads = sorted(threads.items(), key=lambda kv: -kv[1])[:TOP_N]
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "span_s": round(span, 1),
        "proc_cpu_ms": round(proc_ms),
        "core_pct": round(100.0 * proc_ms / (span * 1000.0), 1) if span > 0 else 0,
        "req_cpu_ms": round(req_ms),
        "routes": [
            {"route": r, "cpu_ms": round(v[0]), "wall_ms": round(v[1]), "n": v[2]}
            for r, v in top_routes
        ],
        "threads": [{"name": n, "cpu_ms": round(ms)} for n, ms in top_threads],
    }
    _emit(row)
    return row


def _emit(row):
    try:
        parts = ", ".join(f"{r['route']} {r['cpu_ms']}ms/{r['n']}" for r in row["routes"][:6])
        bg = ", ".join(f"{t['name']} {t['cpu_ms']}ms" for t in row["threads"][:4])
        print(
            f"[CPU] {time.strftime('%H:%M:%S')} {row['core_pct']}% core "
            f"(proc {row['proc_cpu_ms']}ms, requests {row['req_cpu_ms']}ms) "
            f"top: {parts} | threads: {bg}",
            flush=True,
        )
    except Exception:
        pass
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            if LOG_PATH.stat().st_size > LOG_MAX_BYTES:
                LOG_PATH.replace(LOG_PATH.with_suffix(".jsonl.1"))
        except OSError:
            pass
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        pass
