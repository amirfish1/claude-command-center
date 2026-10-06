# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Background job runner for first-run setup.

Owns the `/api/setup/*` surface: a shared in-process job registry, line
streaming, cancellation, and the HTTP handlers (wired from server.py with two
small dispatch blocks).

The runner is deliberately generic — a job is a title plus an ordered list of
(step id, label, callable) triples — so sibling features with their own
endpoints (the free router installer, the first magic task, model evals) can
report progress through the same `/api/setup/jobs/<id>` shape instead of
inventing a second polling API. A callable receives the Job and uses
`job.emit()`, `job.set_step_progress()`, `job.run_cmd()`, `job.download()`,
and `job.check_cancelled()`.

Stdlib-only. No imports from server or ccc_server.core — this module is a
leaf so tests can exercise it without importing server.py.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections import deque
from pathlib import Path

MAX_JOB_LINES = 200
# Finished jobs stay addressable briefly so a UI that polls late still sees
# the outcome; the registry is capped so a long-lived server can't grow it.
MAX_RETAINED_JOBS = 64
_JOB_IDS = re.compile(r"^/api/setup/jobs/([A-Za-z0-9_-]+)$")
_JOB_CANCEL = re.compile(r"^/api/setup/jobs/([A-Za-z0-9_-]+)/cancel$")
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07]*(?:\x07|\x1b\\)|\(.|.)")

_LOCK = threading.Lock()
_JOBS = {}
_JOB_ORDER = deque()
_SETUP_JOB_KIND = "setup"


class CancelledError(Exception):
    """Raised inside a step callable when the user cancels the job."""


def _clean_line(text):
    """One printable log line: ANSI escapes out, whitespace tamed."""
    line = _ANSI_RE.sub("", str(text)).replace("\r", "").rstrip()
    return line.strip()


class Job:
    """One running (or finished) install job.

    `steps` is a list of (step_id, label, callable). The callable runs the
    step body and may emit lines / set progress / launch subprocesses.
    """

    def __init__(self, title, steps, kind="generic"):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.kind = kind
        self.status = "running"
        self.step = None
        self.step_label = ""
        self.progress = 0.0
        self.lines = deque(maxlen=MAX_JOB_LINES)
        self.error = None
        self.cancelled = False
        self.created_ts = time.time()
        self.started_ts = None
        self.finished_ts = None
        self._steps = [
            {"id": sid, "label": label, "state": "pending", "detail": ""}
            for sid, label, _fn in steps
        ]
        self._fns = [fn for _sid, _label, fn in steps]
        self._step_index = 0
        self._step_frac = 0.0
        self._cancel_event = threading.Event()
        self._procs = set()
        self._procs_lock = threading.Lock()
        self._lines_lock = threading.Lock()
        self._last_progress_line = None
        self._thread = threading.Thread(
            target=self._run, name=f"setup-job-{self.id}", daemon=True
        )

    # ------------------------------------------------------------- job API

    def start(self):
        self._thread.start()
        return self.id

    def wait(self, timeout=None):
        """Block until the job's thread exits. Returns the job."""
        self._thread.join(timeout)
        return self

    def emit(self, text):
        """Append a line (or several) to the streamed log shown in the UI."""
        with self._lines_lock:
            for raw in str(text).split("\n"):
                for piece in raw.split("\r"):
                    line = _clean_line(piece)
                    if line:
                        self.lines.append(line)
            self._last_progress_line = None

    def progress_line(self, text):
        """Emit a line that rewrites itself (download %, countdowns)."""
        line = _clean_line(text)
        if not line:
            return
        with self._lines_lock:
            if self._last_progress_line is not None and self.lines:
                self.lines[-1] = line
            else:
                self.lines.append(line)
            self._last_progress_line = line

    def set_step_progress(self, frac):
        try:
            frac = float(frac)
        except (TypeError, ValueError):
            return
        self._step_frac = max(0.0, min(1.0, frac))
        self._recount_progress()

    def set_step_detail(self, text):
        if 0 <= self._step_index < len(self._steps):
            self._steps[self._step_index]["detail"] = str(text or "")

    def check_cancelled(self):
        if self._cancel_event.is_set():
            raise CancelledError()

    def cancel(self):
        """Stop the job: flag the step and terminate any live children."""
        self._cancel_event.set()
        with self._procs_lock:
            procs = list(self._procs)
        for proc in procs:
            _terminate_proc(proc)

    # ------------------------------------------------------- child helpers

    def child_env(self, extra=None):
        """os.environ + freshly installed runtimes first on PATH.

        Steps install into ~/.ccc/runtime and ~/.local/bin mid-job, so each
        spawned command sees tools that did not exist when the server booted
        (LaunchAgents also inherit a sparse PATH).
        """
        env = dict(os.environ)
        home = Path.home()
        prepend = [
            str(home / ".ccc" / "runtime" / "node" / "bin"),
            str(home / ".local" / "bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
        ]
        existing = env.get("PATH") or ""
        parts = [p for p in prepend if os.path.isdir(p)]
        env["PATH"] = os.pathsep.join(parts + [existing]) if existing else os.pathsep.join(parts)
        if extra:
            env.update({k: str(v) for k, v in extra.items()})
        return env

    def run_cmd(self, argv, *, env=None, cwd=None, timeout_s=1800, quiet=False):
        """Run a command, streaming its output into the job log.

        Returns the exit code; raises CancelledError when cancelled, and
        TimeoutError when the command outlives timeout_s. Children get their
        own process group on POSIX so cancel kills shell pipelines too.
        """
        self.check_cancelled()
        shown = " ".join(str(a) for a in argv)
        if not quiet:
            self.emit(f"$ {shown}")
        spawn_env = self.child_env(env)
        popen_kwargs = {}
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                [str(a) for a in argv],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                errors="replace",
                bufsize=1,
                env=spawn_env,
                cwd=str(cwd) if cwd else None,
                **popen_kwargs,
            )
        except FileNotFoundError:
            self.emit(f"Command not found: {argv[0]}")
            return 127
        except OSError as exc:
            self.emit(f"Could not start {argv[0]}: {exc}")
            return 126
        with self._procs_lock:
            self._procs.add(proc)
        try:
            deadline = time.monotonic() + timeout_s if timeout_s else None
            for raw in proc.stdout:
                self.check_cancelled()
                if deadline and time.monotonic() > deadline:
                    _terminate_proc(proc)
                    raise TimeoutError(f"{argv[0]} timed out after {timeout_s}s")
                self.emit(raw)
            while proc.poll() is None:
                self.check_cancelled()
                if deadline and time.monotonic() > deadline:
                    _terminate_proc(proc)
                    raise TimeoutError(f"{argv[0]} timed out after {timeout_s}s")
                time.sleep(0.05)
            self.check_cancelled()
            return proc.returncode or 0
        finally:
            with self._procs_lock:
                self._procs.discard(proc)

    def download(self, url, dest, label="Downloading"):
        """Fetch url to dest with a rewritten progress line. Returns bytes."""
        self.check_cancelled()
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "ccc-setup/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            received = 0
            last_note = 0.0
            tmp = dest.with_suffix(dest.suffix + ".part")
            try:
                with open(tmp, "wb") as fh:
                    while True:
                        self.check_cancelled()
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        received += len(chunk)
                        now = time.monotonic()
                        if now - last_note > 0.6:
                            last_note = now
                            if total:
                                frac = received / total
                                self.progress_line(
                                    f"{label} {frac * 100:.0f}% "
                                    f"({received // (1024 * 1024)} of {total // (1024 * 1024)} MB)"
                                )
                                self.set_step_progress(min(0.9, frac * 0.9))
                            else:
                                self.progress_line(f"{label} {received // (1024 * 1024)} MB")
                tmp.replace(dest)
            except BaseException:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
        self.set_step_progress(0.9)
        return dest

    # ------------------------------------------------------------- driver

    def _recount_progress(self):
        if not self._steps:
            self.progress = 1.0 if self.status == "done" else self.progress
            return
        done_states = {"done", "skipped"}
        completed = sum(1 for s in self._steps if s["state"] in done_states)
        total = len(self._steps)
        frac = self._step_frac if self._steps[self._step_index]["state"] == "running" else 0.0
        self.progress = max(0.0, min(1.0, (completed + frac) / total))

    def _finish_step(self, state, detail=""):
        entry = self._steps[self._step_index]
        entry["state"] = state
        entry["detail"] = detail or entry["detail"]
        self._step_frac = 0.0
        self._recount_progress()

    def _run(self):
        self.started_ts = time.time()
        try:
            if not self._fns:
                self.emit("Nothing to do.")
            for i, fn in enumerate(self._fns):
                self._step_index = i
                entry = self._steps[i]
                entry["state"] = "running"
                self.step = entry["id"]
                self.step_label = entry["label"]
                self._step_frac = 0.0
                self._recount_progress()
                self.check_cancelled()
                result = fn(self)
                self.check_cancelled()
                if result is False:
                    self._finish_step("skipped")
                else:
                    self._finish_step("done")
            self.step = None
            self.step_label = ""
            self.progress = 1.0
            self.status = "done"
        except CancelledError:
            self.cancelled = True
            self.status = "error"
            self.error = "Cancelled"
            if 0 <= self._step_index < len(self._steps):
                if self._steps[self._step_index]["state"] == "running":
                    self._steps[self._step_index]["state"] = "error"
                    self._steps[self._step_index]["detail"] = "Cancelled"
            self.emit("Cancelled.")
        except Exception as exc:  # a step's own errors surface as job errors
            self.status = "error"
            self.error = str(exc) or exc.__class__.__name__
            if 0 <= self._step_index < len(self._steps):
                if self._steps[self._step_index]["state"] == "running":
                    self._steps[self._step_index]["state"] = "error"
                    self._steps[self._step_index]["detail"] = self.error
            self.emit(f"Error: {self.error}")
        finally:
            self.finished_ts = time.time()
            _invalidate_plan_cache()


def _terminate_proc(proc):
    """Terminate a child (and its process group on POSIX), escalating to kill."""
    try:
        if os.name == "posix":
            import signal

            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
        else:
            proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            if os.name == "posix":
                import signal

                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------- registry


def start_job(title, steps, kind="generic"):
    """Create and launch a job. `steps` = [(id, label, fn)]. Returns job_id."""
    job = Job(title, steps, kind=kind)
    with _LOCK:
        _JOBS[job.id] = job
        _JOB_ORDER.append(job.id)
        while len(_JOB_ORDER) > MAX_RETAINED_JOBS:
            old = _JOB_ORDER.popleft()
            old_job = _JOBS.get(old)
            if old_job is not None and old_job.status != "running":
                _JOBS.pop(old, None)
            else:
                _JOB_ORDER.append(old)
                break
    job.start()
    return job.id


def get_job(job_id):
    with _LOCK:
        return _JOBS.get(job_id)


def describe_job(job_id):
    job = get_job(job_id)
    if job is None:
        return None
    steps = []
    for entry in job._steps:
        steps.append(
            {
                "id": entry["id"],
                "label": entry["label"],
                "state": entry["state"],
                "detail": entry["detail"],
            }
        )
    return {
        "id": job.id,
        "title": job.title,
        "kind": job.kind,
        "status": job.status,
        "step": job.step,
        "step_label": job.step_label,
        "progress": round(job.progress, 4),
        "lines": list(job.lines),
        "error": job.error,
        "cancelled": job.cancelled,
        "steps": steps,
        "created_ts": job.created_ts,
        "started_ts": job.started_ts,
        "finished_ts": job.finished_ts,
    }
    with job._lines_lock:
        payload["lines"] = list(job.lines)
    return payload


def cancel_job(job_id):
    job = get_job(job_id)
    if job is None:
        return None
    if job.status == "running":
        job.cancel()
    return job


def _invalidate_plan_cache():
    try:
        from ccc_server import setup_steps

        setup_steps.invalidate_plan_cache()
    except Exception:
        pass


def run_setup(step_ids):
    """Start a setup job over registry steps. Returns (job_id, error, http).

    Posting a step id is the consent click: the UI lists each step's name and
    one-tap consent, then posts only the approved ids. External steps (owned
    by sibling features such as the free router installer) are skipped with
    an explanatory line rather than failing the run.
    """
    from ccc_server import setup_steps

    registry = {s["id"]: s for s in setup_steps.STEPS}
    unknown = [sid for sid in step_ids if sid not in registry]
    if unknown:
        return None, {"error": "unknown steps", "unknown": unknown}, 400, False
    with _LOCK:
        for job in _JOBS.values():
            if job.kind == _SETUP_JOB_KIND and job.status == "running":
                return job.id, None, 200, True
    if not step_ids:
        return None, {"error": "no steps requested"}, 400, False

    def _wrap(step):
        def _fn(job):
            if step.get("external"):
                job.emit(
                    f"{step['label']} has its own setup screen · skipping here."
                )
                return False
            install = step.get("install")
            if install is None:
                job.emit(f"{step['label']}: nothing to install.")
                return False
            install(job)
            return True

        return _fn

    steps = [(sid, registry[sid]["label"], _wrap(registry[sid])) for sid in step_ids]
    job_id = start_job("Getting your computer ready", steps, kind=_SETUP_JOB_KIND)
    return job_id, None, 200, False


# ------------------------------------------------------------------ routes


def handle_get(handler, parsed):
    """Serve /api/setup/plan, /api/setup/jobs/<id>, and the /setup page."""
    path = parsed.path.rstrip("/")
    if path == "/api/setup/plan":
        from ccc_server import setup_steps

        qs = urllib.parse.parse_qs(parsed.query)
        refresh = (qs.get("refresh", [""])[0] or "").strip() in ("1", "true", "yes")
        handler.send_json(setup_steps.build_plan(force=refresh))
        return
    match = _JOB_IDS.match(path)
    if match:
        payload = describe_job(match.group(1))
        if payload is None:
            handler.send_json({"error": "job not found", "job_id": match.group(1)}, 404)
        else:
            handler.send_json(payload)
        return
    if path in ("/setup", "/setup.html"):
        handler.send_html(_load_setup_html())
        return
    handler.send_json({"error": f"not found: {path}"}, 404)


def handle_post(handler):
    """Serve /api/setup/run and /api/setup/jobs/<id>/cancel."""
    path = urllib.parse.urlparse(handler.path).path.rstrip("/")
    if path == "/api/setup/run":
        data = _read_json_body(handler)
        if data is None:
            handler.send_json({"error": "invalid JSON body"}, 400)
            return
        step_ids = data.get("steps") or []
        if isinstance(step_ids, str):
            step_ids = [step_ids]
        if not isinstance(step_ids, list):
            handler.send_json({"error": "steps must be a list of step ids"}, 400)
            return
        job_id, error, status, already = run_setup([str(s) for s in step_ids])
        if error is not None:
            handler.send_json(error, status)
            return
        payload = {"job_id": job_id}
        if already:
            payload["already_running"] = True
        handler.send_json(payload)
        return
    match = _JOB_CANCEL.match(path)
    if match:
        job = cancel_job(match.group(1))
        if job is None:
            handler.send_json({"error": "job not found", "job_id": match.group(1)}, 404)
        else:
            handler.send_json({"ok": True, "job_id": job.id, "status": job.status})
        return
    handler.send_json({"error": f"not found: {path}"}, 404)


def _read_json_body(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0") or 0)
        if not 0 < length <= 1024 * 1024:
            return {}
        import json

        body = handler.rfile.read(length)
        data = json.loads(body) if body else {}
        return data if isinstance(data, dict) else None
    except (ValueError, OSError):
        return None


def _load_setup_html():
    try:
        return (Path(__file__).resolve().parent.parent / "static" / "setup.html").read_text()
    except OSError as exc:
        return f"<h1>setup.html missing</h1><pre>{exc}</pre>"


__all__ = [
    "CancelledError",
    "Job",
    "start_job",
    "get_job",
    "describe_job",
    "cancel_job",
    "run_setup",
    "handle_get",
    "handle_post",
]
