"""Read-only progress for GitHub Actions jobs executing on this host.

Discover active runner workers rather than polling every repository. Runner
configuration and a bounded diagnostic prefix supply identity only; job/step
state comes from GitHub through the existing gh authentication.
"""
import json
from contextlib import contextmanager
import os
import platform
import shutil
import sqlite3
from pathlib import Path
import re
import subprocess
import threading
import time

from ccc_server.paths import COMMAND_CENTER_STATE_DIR

DB_PATH = COMMAND_CENTER_STATE_DIR / "local-ci.sqlite3"

_lock = threading.Lock()
_cached = {"jobs": []}
_checked = 0.0


def _runner_roots(names):
    result = subprocess.run(["ps", "-eo", "comm=,args="], capture_output=True,
                            text=True, timeout=3)
    roots = set()
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or Path(parts[0]).name not in names:
            continue
        executable = Path(parts[1])
        if executable.is_absolute():
            roots.add(executable.parent.parent)
    return sorted(roots)


def _worker_roots():
    return _runner_roots({"Runner.Worker"})


def _identity(root, log_file=None):
    config = json.loads((root / ".runner").read_text(encoding="utf-8-sig"))
    match = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)/?",
                         config.get("gitHubUrl", ""))
    if not match:
        return None
    latest = log_file or max((root / "_diag").glob("Worker_*.log"), key=lambda p: p.name)
    with latest.open(encoding="utf-8", errors="replace") as log:
        prefix = log.read(131072)
    run = re.search(r'"k":\s*"run_id",\s*"v":\s*"(\d+)"', prefix)
    attempt = re.search(r'"k":\s*"run_attempt",\s*"v":\s*"(\d+)"', prefix)
    return {"repo": match[1], "runner": config.get("agentName", ""),
            "run_id": run[1] if run else None, "attempt": int(attempt[1]) if attempt else 1}


@contextmanager
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=3)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS ci_runs (id TEXT PRIMARY KEY, data TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT '', dismissed INTEGER NOT NULL DEFAULT 0)")
            yield db
    finally:
        db.close()


def _load_records():
    with _db() as db:
        return {key: json.loads(data) for key, data in db.execute("SELECT id, data FROM ci_runs")}


def _save_records(records):
    with _db() as db:
        for key, record in records.items():
            # Dismissal is a separate column: polling cannot undo another tab's dismissal.
            db.execute("INSERT INTO ci_runs(id,data,completed_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, completed_at=excluded.completed_at",
                       (key, json.dumps(record), record.get("completed_at") or ""))
        db.execute("DELETE FROM ci_runs WHERE id NOT IN (SELECT id FROM ci_runs ORDER BY (completed_at = '') DESC, completed_at DESC, rowid DESC LIMIT 64)")


def _recent_completed():
    with _db() as db:
        rows = db.execute("SELECT id,data,dismissed FROM ci_runs WHERE completed_at != '' ORDER BY completed_at DESC, rowid DESC LIMIT 2").fetchall()
    return [dict(json.loads(data), id=key) for key, data, dismissed in rows if not dismissed]


def dismiss_local_ci(run_key):
    """Persist dismissal across devices, without changing GitHub or hiding live jobs."""
    global _checked
    if not isinstance(run_key, str) or not run_key or len(run_key) > 250:
        raise ValueError("A completed run id is required")
    with _lock:
        with _db() as db:
            result = db.execute("UPDATE ci_runs SET dismissed=1 WHERE id=? AND completed_at != ''", (run_key,))
        _checked = 0
        return result.rowcount > 0


def _api(route):
    try:
        response = subprocess.run(["gh", "api", "--hostname", "github.com", route],
                                  capture_output=True, text=True, timeout=5)
        return json.loads(response.stdout) if response.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _recent_identities():
    # Recover recent local runs even if the page was closed when they finished.
    identities = []
    for root in _runner_roots({"Runner.Listener"}):
        seen = set()
        for log in sorted((root / "_diag").glob("Worker_*.log"), reverse=True)[:16]:
            try:
                identity = _identity(root, log)
                if identity and identity.get("run_id"):
                    key = (identity["repo"], identity["run_id"], identity.get("attempt", 1))
                    if key not in seen:
                        identities.append(identity)
                        seen.add(key)
                    if len(seen) >= 2:
                        break
            except (OSError, ValueError, KeyError):
                continue
    return identities


def _collect():
    records = _load_records()
    active = []
    for root in _worker_roots():
        try:
            identity = _identity(root)
            if identity:
                active.append(identity)
        except (OSError, ValueError, KeyError):
            continue
    discovered = active + _recent_identities()
    active_names = {}
    for identity in discovered:
        if not identity.get("run_id"):
            continue
        repo, run_id = identity["repo"], identity["run_id"]
        attempt = identity.get("attempt", 1)
        key = f"{repo}:{run_id}:{attempt}"
        record = records.setdefault(key, dict(identity, attempt=attempt, status="pending", runners=[]))
        if identity["runner"] not in record["runners"]:
            record["runners"].append(identity["runner"])
        if identity in active:
            active_names.setdefault(key, set()).add(identity["runner"])
    rows = [dict(identity, name="CI", percent=None, completed_steps=0, total_steps=0,
                 current_step="Reading job progress", url="")
            for identity in active if not identity.get("run_id")]
    started = time.monotonic()
    for key, record in sorted(records.items(), key=lambda item: item[0] not in active_names):
        if record.get("status") == "completed":
            continue
        local_names = active_names.get(key, set())
        if not local_names and time.time() - record.get("polled_at", 0) < 30:
            continue
        if time.monotonic() - started > 12:
            # Bound bootstrap work; the next refresh handles the remaining runs.
            continue
        repo, run_id, attempt = record["repo"], record["run_id"], record["attempt"]
        route = f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}"
        run = _api(route)
        record["polled_at"] = time.time()
        url = f"https://github.com/{repo}/actions/runs/{run_id}"
        if run and run.get("status") == "completed":
            record.update(status="completed", name=run.get("name") or "CI",
                          conclusion=run.get("conclusion") or "unknown", percent=100,
                          completed_at=run.get("updated_at") or run.get("run_started_at") or "",
                          url=url, current_step=run.get("conclusion") or "Completed")
            continue
        if not local_names:
            continue
        payload = _api(route + "/jobs?per_page=100") or {}
        jobs = [job for job in payload.get("jobs", []) if job.get("runner_name") in local_names
                and job.get("status") == "in_progress"]
        if not jobs:
            rows.append(dict(record, id=key, name="CI", percent=None, completed_steps=0,
                             total_steps=0, current_step="Reading job progress", url=url))
        for job in jobs:
            steps = job.get("steps") or []
            done = sum(step.get("status") == "completed" for step in steps)
            current = next((step.get("name") for step in steps
                            if step.get("status") == "in_progress"), "Starting job")
            rows.append(dict(record, id=key, name=job.get("name") or "CI",
                             runner=job.get("runner_name") or record.get("runner", ""),
                             percent=min(99, int(done * 100 / len(steps))) if steps else None,
                             completed_steps=done, total_steps=len(steps), current_step=current, url=url))
    _save_records(records)
    return {"jobs": rows, "completed": _recent_completed(), "checked_at": time.time()}


def local_ci_progress():
    """Shared ten-second cache; concurrent dashboard requests never fan out."""
    global _cached, _checked
    with _lock:
        if time.monotonic() - _checked >= 10:
            try:
                _cached = _collect()
            except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired):
                _cached = {"jobs": [], "completed": [], "unavailable": True}
            _checked = time.monotonic()
        return _cached


def local_ci_setup():
    """On-demand setup checks; never register runners or return auth output."""
    system = platform.system()
    gh = shutil.which("gh")
    result = {"platform": system, "supported": system in ("Linux", "Darwin"),
              "github_cli": bool(gh), "github_authenticated": False,
              "runners": [], "runner_check_available": True}
    if gh:
        try:
            auth = subprocess.run([gh, "api", "--hostname", "github.com", "user", "--silent"],
                                  capture_output=True, timeout=5,
                                  env=dict(os.environ, GH_PROMPT_DISABLED="1"))
            result["github_authenticated"] = auth.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        active = set(_worker_roots())
        roots = set(_runner_roots({"Runner.Listener"})) | active
        for root in sorted(roots):
            try:
                config = json.loads((root / ".runner").read_text(encoding="utf-8-sig"))
                result["runners"].append({"name": config.get("agentName", "Runner"),
                                          "state": "running_job" if root in active else "listening"})
            except (OSError, ValueError):
                continue
    except (OSError, subprocess.TimeoutExpired):
        result["runner_check_available"] = False
    return result
