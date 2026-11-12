"""Read-only progress for GitHub Actions jobs executing on this host.

Discover active runner workers rather than polling every repository. Runner
configuration and a bounded diagnostic prefix supply identity only; job/step
state comes from GitHub through the existing gh authentication.
"""
import json
from pathlib import Path
import re
import subprocess
import threading
import time

_lock = threading.Lock()
_cached = {"jobs": []}
_checked = 0.0


def _worker_roots():
    result = subprocess.run(["ps", "-eo", "comm=,args="], capture_output=True,
                            text=True, timeout=3)
    roots = set()
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or Path(parts[0]).name != "Runner.Worker":
            continue
        executable = Path(parts[1])
        if executable.is_absolute():
            roots.add(executable.parent.parent)
    return sorted(roots)


def _identity(root):
    config = json.loads((root / ".runner").read_text(encoding="utf-8-sig"))
    match = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)/?",
                         config.get("gitHubUrl", ""))
    if not match:
        return None
    latest = max((root / "_diag").glob("Worker_*.log"), key=lambda p: p.name)
    with latest.open(encoding="utf-8", errors="replace") as log:
        prefix = log.read(131072)
    run = re.search(r'"k":\s*"run_id",\s*"v":\s*"(\d+)"', prefix)
    return {"repo": match[1], "runner": config.get("agentName", ""),
            "run_id": run[1] if run else None}


def _collect():
    rows = []
    runs = {}
    for root in _worker_roots():
        try:
            identity = _identity(root)
            if not identity:
                continue
            repo, run_id = identity["repo"], identity["run_id"]
            fallback = dict(identity, name="CI", percent=None, completed_steps=0,
                            total_steps=0, current_step="Reading job progress", url="")
            if not run_id:
                rows.append(fallback)
                continue
            fallback["url"] = f"https://github.com/{repo}/actions/runs/{run_id}"
            key = (repo, run_id)
            if key not in runs:
                try:
                    response = subprocess.run(
                        ["gh", "api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"],
                        capture_output=True, text=True, timeout=5)
                    runs[key] = json.loads(response.stdout)["jobs"] if response.returncode == 0 else []
                except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
                    runs[key] = []
            jobs = [job for job in runs[key] if job.get("runner_name") == identity["runner"]
                    and job.get("status") == "in_progress"]
            if not jobs:
                rows.append(fallback)
            for job in jobs:
                steps = job.get("steps") or []
                done = sum(step.get("status") == "completed" for step in steps)
                current = next((step.get("name") for step in steps
                                if step.get("status") == "in_progress"), "Starting job")
                rows.append(dict(identity, name=job.get("name") or "CI",
                                 percent=min(99, int(done * 100 / len(steps))) if steps else None,
                                 completed_steps=done, total_steps=len(steps),
                                 current_step=current, url=fallback["url"]))
        except (OSError, ValueError, KeyError):
            continue
    return {"jobs": rows, "checked_at": time.time()}


def local_ci_progress():
    """Shared ten-second cache; concurrent dashboard requests never fan out."""
    global _cached, _checked
    with _lock:
        if time.monotonic() - _checked >= 10:
            try:
                _cached = _collect()
            except (OSError, subprocess.TimeoutExpired):
                _cached = {"jobs": [], "unavailable": True}
            _checked = time.monotonic()
        return _cached
