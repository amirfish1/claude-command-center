# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Scheduled-job registry: aggregates launchd and systemd state across hosts.

Reads job state read-only from:
1. macOS launchd (local host / laptop): inspecting ~/Library/LaunchAgents/*.plist
   and querying `launchctl list`.
2. Linux systemd (hermes VM): querying `systemctl list-timers --output=json` and
   `systemctl show` via ssh.

CCC surfaces scheduling state, it does NOT own execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import plistlib
import re
import subprocess
import threading
import time

_CACHE_LOCK = threading.Lock()
_CACHE = {"ts": 0.0, "payload": None}
_CACHE_TTL_S = 15.0


def _parse_iso(ts_val):
    if not ts_val:
        return None
    if isinstance(ts_val, (int, float)):
        # microsecond or second unix timestamp
        if ts_val > 1e11:
            ts_val = ts_val / 1e6
        try:
            return datetime.fromtimestamp(ts_val, tz=timezone.utc).isoformat()
        except Exception:
            return None
    return str(ts_val)


def _format_schedule(plist_data):
    if not isinstance(plist_data, dict):
        return "Manual / On demand"
    interval = plist_data.get("StartInterval")
    if interval:
        try:
            sec = int(interval)
            if sec % 3600 == 0:
                h = sec // 3600
                return f"Every {h}h"
            if sec % 60 == 0:
                m = sec // 60
                return f"Every {m}m"
            return f"Every {sec}s"
        except Exception:
            pass

    cal = plist_data.get("StartCalendarInterval")
    if cal:
        if isinstance(cal, list):
            items = []
            for item in cal[:3]:
                if isinstance(item, dict):
                    h = item.get("Hour", "*")
                    m = item.get("Minute", 0)
                    items.append(f"{h:02d}:{m:02d}" if isinstance(h, int) and isinstance(m, int) else f"{h}:{m}")
            more = f" (+{len(cal) - 3} more)" if len(cal) > 3 else ""
            return f"Calendar: {', '.join(items)}{more}"
        elif isinstance(cal, dict):
            h = cal.get("Hour")
            m = cal.get("Minute", 0)
            weekday = cal.get("Weekday")
            day_str = f"Weekday {weekday} " if weekday is not None else "Daily "
            if h is not None:
                return f"{day_str}at {h:02d}:{m:02d}" if isinstance(h, int) and isinstance(m, int) else f"{day_str}at {h}:{m}"
            return f"Hourly at :{m:02d}" if isinstance(m, int) else f"Hourly at :{m}"

    if plist_data.get("RunAtLoad"):
        return "At login / load"

    return "On demand"


def _read_file_tail(filepath, max_lines=50):
    try:
        p = Path(filepath).expanduser()
        if not p.is_file():
            return None
        size = p.stat().st_size
        if size == 0:
            return ""
        # Read from end if file is large
        read_size = min(size, 65536)
        with open(p, "rb") as f:
            if size > read_size:
                f.seek(size - read_size)
            chunk = f.read().decode("utf-8", errors="replace")
        lines = chunk.splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception as e:
        return f"(Error reading log: {e})"


def _collect_local_launchd_jobs():
    """Inspects ~/Library/LaunchAgents/*.plist and cross-references `launchctl list`."""
    launchctl_map = {}
    try:
        proc = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    pid_str, status_str, label = parts[0], parts[1], parts[2]
                    launchctl_map[label] = {
                        "pid": int(pid_str) if pid_str.isdigit() else None,
                        "status": int(status_str) if status_str.lstrip("-").isdigit() else None,
                    }
    except Exception:
        pass

    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    jobs = []
    if not launch_agents_dir.is_dir():
        return jobs

    # Priority / known user labels
    for plist_path in sorted(launch_agents_dir.glob("*.plist")):
        filename = plist_path.name
        # Skip pure system/vendor agents unless user-configured
        if filename.startswith("com.apple.") or filename.startswith("com.google."):
            continue

        try:
            with open(plist_path, "rb") as f:
                data = plistlib.load(f)
        except Exception:
            continue

        label = data.get("Label") or plist_path.stem
        program_args = data.get("ProgramArguments") or ([data["Program"]] if data.get("Program") else [])
        stdout_path = data.get("StandardOutPath")
        stderr_path = data.get("StandardErrorPath")
        schedule_desc = _format_schedule(data)

        l_info = launchctl_map.get(label)
        is_running = l_info and l_info.get("pid") is not None
        exit_code = l_info.get("status") if l_info else None

        last_run_at = None
        log_mtime = None
        target_log = stdout_path or stderr_path
        if target_log:
            try:
                lp = Path(target_log).expanduser()
                if lp.is_file():
                    log_mtime = lp.stat().st_mtime
                    last_run_at = datetime.fromtimestamp(log_mtime, tz=timezone.utc).isoformat()
            except Exception:
                pass

        if is_running:
            state = "running"
        elif exit_code == 0:
            state = "success"
        elif exit_code is not None and exit_code > 0:
            state = "failed"
        elif l_info:
            state = "idle"
        else:
            state = "unloaded"

        job_id = f"laptop:{label}"
        jobs.append({
            "id": job_id,
            "name": label,
            "host": "laptop",
            "manager": "launchd",
            "schedule": schedule_desc,
            "status": state,
            "pid": l_info.get("pid") if l_info else None,
            "exit_code": exit_code,
            "last_run_at": last_run_at,
            "next_run_at": None,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "program_args": program_args[:4] if program_args else [],
            "plist_path": str(plist_path),
        })

    return jobs


def _collect_hermes_systemd_jobs(timeout_s=4):
    """Queries systemd timers and services on hermes via ssh."""
    jobs = []
    host_info = {"status": "offline", "error": None}

    # Step 1: list-timers JSON
    try:
        res = subprocess.run(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", f"ConnectTimeout={timeout_s}",
                "hermes",
                "systemctl list-timers --output=json",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s + 2,
            check=False,
        )
        if res.returncode != 0:
            host_info["error"] = res.stderr.strip() or f"ssh exited with code {res.returncode}"
            return jobs, host_info

        timers_raw = json.loads(res.stdout)
        host_info["status"] = "online"
    except Exception as e:
        host_info["error"] = str(e)
        return jobs, host_info

    # Keep project/user timers; drop the distro's own housekeeping timers.
    target_timers = []
    for item in timers_raw:
        unit = item.get("unit") or ""
        if not any(unit.startswith(sys_prefix) for sys_prefix in ("apt-", "dpkg-", "fstrim", "man-db", "logrotate", "systemd-", "update-notifier-", "motd-", "e2scrub")):
            target_timers.append(item)

    if not target_timers:
        return jobs, host_info

    service_names = [t.get("activates") for t in target_timers if t.get("activates")]
    props_map = {}
    if service_names:
        try:
            cmd = (
                f"systemctl show {' '.join(service_names)} "
                "--property=Id,ActiveState,SubState,Result,ExecMainStatus,ExecMainExitTimestamp"
            )
            res2 = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout_s}", "hermes", cmd],
                capture_output=True,
                text=True,
                timeout=timeout_s + 2,
                check=False,
            )
            if res2.returncode == 0:
                blocks = res2.stdout.strip().split("\n\n")
                for blk in blocks:
                    prop = {}
                    for line in blk.splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            prop[k.strip()] = v.strip()
                    unit_id = prop.get("Id")
                    if unit_id:
                        props_map[unit_id] = prop
        except Exception:
            pass

    for t in target_timers:
        unit = t.get("unit") or ""
        activates = t.get("activates") or ""
        prop = props_map.get(activates, {})

        active_state = prop.get("ActiveState", "unknown")
        sub_state = prop.get("SubState", "")
        result = prop.get("Result", "")
        exec_status_str = prop.get("ExecMainStatus")
        exec_status = int(exec_status_str) if exec_status_str and exec_status_str.isdigit() else None

        if active_state == "active":
            state = "running"
        elif result == "success" or exec_status == 0:
            state = "success"
        elif result and result != "success":
            state = "failed"
        elif active_state == "failed":
            state = "failed"
        else:
            state = "idle"

        next_iso = _parse_iso(t.get("next"))
        last_iso = _parse_iso(t.get("last"))

        name = activates.removesuffix(".service") if activates.endswith(".service") else unit.removesuffix(".timer")
        job_id = f"hermes:{activates or unit}"

        jobs.append({
            "id": job_id,
            "name": name,
            "host": "hermes",
            "manager": "systemd",
            "timer": unit,
            "unit": activates,
            "schedule": f"Timer: {unit}",
            "status": state,
            "pid": None,
            "exit_code": exec_status,
            "last_run_at": last_iso,
            "next_run_at": next_iso,
            "stdout_path": f"journalctl -u {activates}" if activates else None,
            "stderr_path": None,
            "log_cmd": f"journalctl -u {activates} -n 50 --no-pager" if activates else None,
        })

    return jobs, host_info


def collect_scheduled_jobs(force=False, timeout_s=3):
    """Returns coalesced, cached scheduled jobs across laptop and hermes."""
    now = time.time()
    with _CACHE_LOCK:
        if not force and _CACHE["payload"] is not None and (now - _CACHE["ts"]) < _CACHE_TTL_S:
            return _CACHE["payload"]

    laptop_jobs = _collect_local_launchd_jobs()
    hermes_jobs, hermes_host = _collect_hermes_systemd_jobs(timeout_s=timeout_s)

    all_jobs = laptop_jobs + hermes_jobs
    # Sort: failed first, then running, then recent last_run_at descending, then name
    def _sort_key(j):
        status_rank = 0 if j["status"] == "failed" else 1 if j["status"] == "running" else 2
        last_run = j.get("last_run_at") or ""
        return (status_rank, -1 if last_run else 0, last_run, j.get("name", ""))

    all_jobs.sort(key=lambda j: (
        0 if j["status"] == "failed" else 1 if j["status"] == "running" else 2,
        j["host"],
        j["name"],
    ))

    payload = {
        "ok": True,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "hosts": {
            "laptop": {"status": "online", "jobs_count": len(laptop_jobs)},
            "hermes": {**hermes_host, "jobs_count": len(hermes_jobs)},
        },
        "jobs": all_jobs,
        "summary": {
            "total": len(all_jobs),
            "running": sum(1 for j in all_jobs if j["status"] == "running"),
            "failed": sum(1 for j in all_jobs if j["status"] == "failed"),
            "success": sum(1 for j in all_jobs if j["status"] == "success"),
            "idle": sum(1 for j in all_jobs if j["status"] in ("idle", "unloaded")),
        },
    }

    with _CACHE_LOCK:
        _CACHE["ts"] = time.time()
        _CACHE["payload"] = payload

    return payload


def get_scheduled_job_log(job_id, max_lines=50):
    """Fetches the log tail for a given job id (laptop:<label> or hermes:<unit>)."""
    if not job_id:
        return {"ok": False, "error": "Missing job id", "log": ""}

    if job_id.startswith("hermes:"):
        unit = job_id.split(":", 1)[1]
        try:
            res = subprocess.run(
                [
                    "ssh",
                    "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=4",
                    "hermes",
                    f"journalctl -u {unit} -n {max_lines} --no-pager",
                ],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                return {"ok": True, "id": job_id, "log": res.stdout}
            return {
                "ok": True,
                "id": job_id,
                "log": res.stderr.strip() or f"(No log entries found for {unit})",
            }
        except Exception as e:
            return {"ok": False, "id": job_id, "error": str(e), "log": f"Failed to fetch hermes log: {e}"}

    elif job_id.startswith("laptop:"):
        label = job_id.split(":", 1)[1]
        # Locate plist
        launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
        plist_path = launch_agents_dir / f"{label}.plist"
        if not plist_path.is_file():
            # Try to search
            cands = list(launch_agents_dir.glob(f"*{label}*.plist"))
            if cands:
                plist_path = cands[0]

        if not plist_path.is_file():
            return {"ok": False, "error": f"Plist not found for {label}", "log": ""}

        try:
            with open(plist_path, "rb") as f:
                data = plistlib.load(f)
            stdout_path = data.get("StandardOutPath")
            stderr_path = data.get("StandardErrorPath")

            chunks = []
            if stdout_path:
                tail = _read_file_tail(stdout_path, max_lines=max_lines)
                if tail is not None:
                    chunks.append(f"=== STDOUT ({stdout_path}) ===\n{tail}")
            if stderr_path and stderr_path != stdout_path:
                tail = _read_file_tail(stderr_path, max_lines=max_lines)
                if tail is not None and tail.strip():
                    chunks.append(f"=== STDERR ({stderr_path}) ===\n{tail}")

            log_text = "\n\n".join(chunks) if chunks else "(No stdout or stderr files found or configured in plist)"
            return {"ok": True, "id": job_id, "log": log_text}
        except Exception as e:
            return {"ok": False, "id": job_id, "error": str(e), "log": str(e)}

    return {"ok": False, "error": f"Unknown job host prefix in {job_id}", "log": ""}
