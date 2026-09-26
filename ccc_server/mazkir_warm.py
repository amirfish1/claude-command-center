# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Warm Mazkir: one long-lived `claude -p` fed one Ask at a time.

A cold Ask boots a fresh `claude -p` plus its two MCP servers before the model
sees the question (measured 2026-09-25: 5.1 s to first token cold, 1.9 s warm).
WarmPool keeps one booted process in stream-json mode instead:

- Per-request isolation: after every answer the pool sends `/clear` and waits
  for the CLI's `conversation_reset` + `result`. If that confirmation doesn't
  arrive, the process is retired, never reused.
- Idle timeout: retired after IDLE_SEC without a request (a reaper thread).
- Crash recovery: a dead process is replaced on the next request; a crash
  mid-request raises WarmError so the caller can retry cold.
- Recycled after MAX_REQUESTS answers to bound memory growth.
- One request at a time: a concurrent Ask gets WarmBusy and runs cold rather
  than queueing behind a long answer.

Stdlib only. `popen` is injectable so tests can drive a fake stream-json child.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import subprocess
import threading
import time

IDLE_SEC = float(os.environ.get("CCC_ASK_WARM_IDLE_SEC", "900"))
MAX_REQUESTS = int(os.environ.get("CCC_ASK_WARM_MAX_REQUESTS", "20"))
CLEAR_TIMEOUT_SEC = 5.0
BUSY_WAIT_SEC = 2.0  # covers the post-answer /clear still holding the lock

_EOF = object()


class WarmError(RuntimeError):
    """The warm process died or misbehaved mid-request."""


class WarmBusy(RuntimeError):
    """Another Ask holds the warm process."""


class WarmTimeout(WarmError):
    """No result within the request timeout."""


def warm_enabled() -> bool:
    return os.environ.get("CCC_ASK_WARM", "1").strip().lower() not in ("0", "false", "no", "off")


def stream_argv(base_argv: list[str]) -> list[str]:
    """Turn the one-shot Mazkir argv into its stream-json form."""
    argv = list(base_argv)
    if "--output-format" in argv:
        argv[argv.index("--output-format") + 1] = "stream-json"
    else:
        argv += ["--output-format", "stream-json"]
    return argv + ["--input-format", "stream-json", "--verbose", "--include-partial-messages"]


def _user_line(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"


class WarmProcess:
    """One stream-json `claude -p` child plus its stdout/stderr reader threads."""

    def __init__(self, argv, cwd=None, env=None, popen=None, on_session=None):
        self.argv = list(argv)
        self.on_session = on_session
        self.spawned_at = time.time()
        self.requests = 0
        self.session_ids: list[str] = []
        self._events: queue.Queue = queue.Queue()
        self._stderr = collections.deque(maxlen=40)
        self.proc = (popen or subprocess.Popen)(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, cwd=cwd, env=env)
        threading.Thread(target=self._read_stdout, daemon=True, name="mazkir-warm-out").start()
        threading.Thread(target=self._read_stderr, daemon=True, name="mazkir-warm-err").start()

    def _read_stdout(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    self._events.put(ev)
        except (OSError, ValueError):
            pass
        self._events.put(_EOF)

    def _read_stderr(self):
        try:
            for line in self.proc.stderr:
                self._stderr.append(line.rstrip())
        except (OSError, ValueError):
            pass

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)[-300:]

    def _drain(self):
        while True:
            try:
                ev = self._events.get_nowait()
            except queue.Empty:
                return
            if ev is _EOF:
                self._events.put(_EOF)
                return

    def _send(self, text: str):
        try:
            self.proc.stdin.write(_user_line(text))
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise WarmError(f"warm mazkir stdin closed: {e}") from e

    def _next(self, deadline: float):
        remaining = deadline - time.time()
        if remaining <= 0:
            raise WarmTimeout("warm mazkir timed out")
        try:
            ev = self._events.get(timeout=remaining)
        except queue.Empty:
            raise WarmTimeout("warm mazkir timed out") from None
        if ev is _EOF:
            self._events.put(_EOF)
            raise WarmError("warm mazkir exited mid-request: " + (self.stderr_tail() or "no stderr"))
        return ev

    def _note_session(self, ev: dict):
        sid = ev.get("session_id")
        if sid and sid not in self.session_ids:
            self.session_ids.append(sid)
            if self.on_session:
                try:
                    self.on_session(sid)
                except Exception:
                    pass

    def ask(self, prompt: str, timeout: float, t_start: float | None = None) -> dict:
        """Send one question, block until its `result` event.

        `ttft_ms` is measured from `t_start` (the HTTP request's start, so it
        includes prefetch) to the first streamed text delta.
        """
        t_start = t_start or time.time()
        deadline = time.time() + timeout
        self._drain()
        self._send(prompt)
        self.requests += 1
        ttft_ms = None
        while True:
            ev = self._next(deadline)
            typ = ev.get("type")
            if typ == "system" and ev.get("subtype") == "init":
                self._note_session(ev)
            elif typ == "stream_event" and ttft_ms is None:
                delta = (ev.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    ttft_ms = int((time.time() - t_start) * 1000)
            elif typ == "result":
                self._note_session(ev)
                return {
                    "answer": str(ev.get("result") or "").strip(),
                    "num_turns": ev.get("num_turns"),
                    "cost_usd": ev.get("total_cost_usd"),
                    "is_error": bool(ev.get("is_error")),
                    "duration_ms": ev.get("duration_ms"),
                    "claude_session_id": ev.get("session_id"),
                    "ttft_ms": ttft_ms,
                }

    def clear(self, timeout: float = CLEAR_TIMEOUT_SEC) -> bool:
        """`/clear` the conversation; True only once the CLI confirms the reset."""
        deadline = time.time() + timeout
        try:
            self._drain()
            self._send("/clear")
            reset = False
            while True:
                ev = self._next(deadline)
                if ev.get("type") == "conversation_reset":
                    reset = True
                elif ev.get("type") == "system" and ev.get("subtype") == "init":
                    self._note_session(ev)
                elif ev.get("type") == "result":
                    return reset
        except WarmError:
            return False

    def close(self):
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class WarmPool:
    """Owns at most one WarmProcess; see the module docstring for the rules."""

    def __init__(self, idle_sec: float = IDLE_SEC, max_requests: int = MAX_REQUESTS,
                 popen=None, clear_async: bool = True):
        self.idle_sec = idle_sec
        self.max_requests = max_requests
        self.popen = popen
        self.clear_async = clear_async
        self._lock = threading.Lock()   # held for a whole request + its /clear
        self._state = threading.Lock()  # guards self._proc / self._key / timestamps
        self._proc: WarmProcess | None = None
        self._key = None
        self._last_used = 0.0
        self._reaper = None
        self.stats = collections.Counter()

    # -- lifecycle ---------------------------------------------------------
    def _spawn(self, argv, cwd, env, on_session) -> WarmProcess:
        self.stats["spawns"] += 1
        return WarmProcess(argv, cwd=cwd, env=env, popen=self.popen, on_session=on_session)

    def _retire_locked(self, reason: str):
        p, self._proc, self._key = self._proc, None, None
        if p is not None:
            self.stats["retired_" + reason] += 1
            threading.Thread(target=p.close, daemon=True, name="mazkir-warm-close").start()

    def _ensure_locked(self, argv, cwd, env, on_session) -> tuple[WarmProcess, bool]:
        """Return (process, was_warm). Caller holds self._lock."""
        key = (tuple(argv), cwd)
        with self._state:
            p = self._proc
            if p is not None and (not p.alive() or self._key != key):
                self._retire_locked("dead" if not p.alive() else "config")
                p = None
            if p is not None and p.requests >= self.max_requests:
                self._retire_locked("recycled")
                p = None
            if p is not None:
                return p, True
            p = self._spawn(argv, cwd, env, on_session)
            self._proc, self._key = p, key
            self._last_used = time.time()
        self._start_reaper()
        return p, False

    def _start_reaper(self):
        if self._reaper is not None and self._reaper.is_alive():
            return
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True, name="mazkir-warm-reaper")
        self._reaper.start()

    def _reap_loop(self):
        while True:
            time.sleep(max(0.05, min(30.0, self.idle_sec / 4)))
            if not self._lock.acquire(blocking=False):
                continue
            try:
                with self._state:
                    if self._proc is None:
                        return
                    if time.time() - self._last_used >= self.idle_sec:
                        self._retire_locked("idle")
                        return
            finally:
                self._lock.release()

    # -- public ------------------------------------------------------------
    def warm(self, argv, cwd=None, env=None, on_session=None) -> bool:
        """Boot the process ahead of the first Ask. False when busy."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self._ensure_locked(argv, cwd, env, on_session)
            with self._state:
                self._last_used = time.time()
            return True
        finally:
            self._lock.release()

    def ask(self, argv, prompt, timeout, cwd=None, env=None, on_session=None,
            t_start=None) -> dict:
        if not self._lock.acquire(timeout=BUSY_WAIT_SEC):
            self.stats["busy"] += 1
            raise WarmBusy("warm mazkir is answering another Ask")
        t_start = t_start or time.time()
        release_now = True
        try:
            p, _ = self._ensure_locked(argv, cwd, env, on_session)
            # "warm" = booted before this request began. A process spawned by
            # warm() during this request's prefetch is "warm_boot" upstream.
            was_warm = p.spawned_at < t_start
            try:
                res = p.ask(prompt, timeout, t_start=t_start)
            except WarmError:
                with self._state:
                    if self._proc is p:
                        self._retire_locked("error")
                raise
            res["warm"] = was_warm
            res["process_requests"] = p.requests
            with self._state:
                self._last_used = time.time()
            self.stats["answers"] += 1
            if self.clear_async:
                release_now = False
                threading.Thread(target=self._clear_then_release, args=(p,), daemon=True,
                                 name="mazkir-warm-clear").start()
            else:
                self._clear(p)
            return res
        finally:
            if release_now:
                self._lock.release()

    def _clear(self, p: WarmProcess):
        ok = p.clear()
        if not ok:
            with self._state:
                if self._proc is p:
                    self._retire_locked("clear_failed")

    def _clear_then_release(self, p: WarmProcess):
        try:
            self._clear(p)
        finally:
            self._lock.release()

    def status(self) -> dict:
        with self._state:
            p = self._proc
            return {
                "enabled": warm_enabled(),
                "alive": bool(p and p.alive()),
                "pid": p.proc.pid if p else None,
                "requests": p.requests if p else 0,
                "age_s": int(time.time() - p.spawned_at) if p else None,
                "idle_s": int(time.time() - self._last_used) if p else None,
                "idle_timeout_s": self.idle_sec,
                "stats": dict(self.stats),
            }

    def shutdown(self):
        with self._state:
            self._retire_locked("shutdown")


_POOL: WarmPool | None = None
_POOL_LOCK = threading.Lock()


def pool() -> WarmPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = WarmPool()
            import atexit
            atexit.register(_POOL.shutdown)
        return _POOL
