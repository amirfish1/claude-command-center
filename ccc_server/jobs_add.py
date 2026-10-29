# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Create a scheduled job through an agent session (POST /api/jobs/add).

CCC never writes systemd units or LaunchAgents itself. This module validates
the request, composes the prompt an agent follows to create the job, and hands
it to the normal spawn path (/api/sessions/spawn, the one `ccc spawn` uses).
One source of truth for the Jobs tab's "+ Add" dialog, `ccc jobs add` and any
agent that POSTs here.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

HOSTS = ("hermes", "laptop")
HERMES_APPS_DIR = "/home/hermes/Apps"
_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_MAX_TEXT = 4000


def _text(payload, key):
    v = payload.get(key)
    return v.strip() if isinstance(v, str) else ""


def _resolve_laptop_repo(repo, known_paths):
    """A known repo path, or a folder name matching exactly one known repo."""
    known = [str(p) for p in known_paths if p]
    if "/" in repo or repo.startswith("~"):
        try:
            cand = str(Path(os.path.expanduser(repo)).resolve())
        except (OSError, RuntimeError):
            return None, f"could not resolve repo path: {repo}"
        if cand in known:
            return cand, None
        return None, f"repo is not a known repo on this laptop: {repo}"
    hits = [p for p in known if os.path.basename(p.rstrip("/")) == repo]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, f"repo name {repo!r} is ambiguous; pass the full path ({', '.join(hits)})"
    return None, f"repo is not a known repo on this laptop: {repo}"


def _resolve_hermes_repo(repo, known_paths):
    """A /home/hermes/Apps/<x> path. A bare folder name, or a known laptop
    repo path, maps to the checkout of the same folder name on the VM."""
    folder = ""
    if repo.startswith(HERMES_APPS_DIR + "/"):
        folder = repo[len(HERMES_APPS_DIR) + 1:].rstrip("/")
    elif "/" not in repo and not repo.startswith("~"):
        folder = repo
    else:
        try:
            cand = str(Path(os.path.expanduser(repo)).resolve())
        except (OSError, RuntimeError):
            cand = ""
        if cand and cand in [str(p) for p in known_paths if p]:
            folder = os.path.basename(cand.rstrip("/"))
    if not folder or not _FOLDER_RE.match(folder) or folder in (".", ".."):
        return None, (f"hermes repo must be a folder name or a {HERMES_APPS_DIR}/<folder> path, "
                      f"got {repo!r}")
    return f"{HERMES_APPS_DIR}/{folder}", None


def validate_job_add(payload, known_paths):
    """Validate a /api/jobs/add body. Returns (fields, None) or (None, error).

    fields: {host, repo, repo_input, what, when, name, model, engine, effort}.
    For laptop jobs ``repo`` is the resolved local path; for hermes jobs it is
    the /home/hermes/Apps/<folder> path on the VM.
    """
    if not isinstance(payload, dict):
        return None, "body must be a JSON object"
    host = _text(payload, "host").lower()
    repo_in = _text(payload, "repo")
    what = _text(payload, "what")
    when = _text(payload, "when")
    missing = [k for k, v in (("host", host), ("repo", repo_in), ("what", what), ("when", when)) if not v]
    if missing:
        return None, "missing " + ", ".join(missing)
    if host not in HOSTS:
        return None, f"host must be one of {', '.join(HOSTS)}, got {host!r}"
    if len(what) > _MAX_TEXT or len(when) > 200:
        return None, "what/when is too long"
    name = _text(payload, "name")
    if name and not _NAME_RE.match(name):
        return None, f"name must be a short kebab-case slug (a-z, 0-9, -), got {name!r}"
    if host == "laptop":
        repo, err = _resolve_laptop_repo(repo_in, known_paths)
    else:
        repo, err = _resolve_hermes_repo(repo_in, known_paths)
    if err:
        return None, err
    return {
        "host": host,
        "repo": repo,
        "repo_input": repo_in,
        "what": what,
        "when": when,
        "name": name,
        "model": _text(payload, "model"),
        "engine": _text(payload, "engine"),
        "effort": _text(payload, "effort"),
    }, None


def compose_job_prompt(f):
    """The prompt the agent session follows to create the job."""
    host = "laptop" if f.get("host") == "laptop" else "hermes"
    name = f.get("name") or ""
    lines = [
        "Create a new scheduled job and confirm it shows up in the CCC Jobs tab.",
        "",
        "What it should do: " + f["what"],
        "When it should run: " + f["when"],
        "Repo / project folder: " + f["repo"],
        "",
        ("Name the job `" + name + "`." if name
         else "Pick a short kebab-case job name from what it does.")
        + " Write the script the job runs inside the repo (committed there, not in /tmp).",
    ]
    if host == "hermes":
        lines += [
            "Host: the Hermes VM (reach it with `ssh hermes`; use sudo there).",
            "- Create /etc/systemd/system/<name>.service and /etc/systemd/system/<name>.timer.",
            "- The service has a one-line Description=, WorkingDirectory= set to the repo checkout on Hermes"
            " (if the folder above is a laptop path, use the matching checkout on the VM), and User=hermes.",
            "- Translate the schedule into OnCalendar= (or OnUnitActiveSec= for \"every N\" schedules) on the timer.",
            "- Run `sudo systemctl daemon-reload` and `sudo systemctl enable --now <name>.timer`.",
        ]
    else:
        lines += [
            "Host: this laptop (launchd).",
            "- Create ~/Library/LaunchAgents/<label>.plist, where <label> uses the same reverse-DNS prefix"
            " the existing scheduled LaunchAgents there use, followed by <name>.",
            "- Use StartCalendarInterval for clock times or StartInterval for \"every N\" schedules;"
            " set WorkingDirectory to the repo.",
            "- StandardOutPath and StandardErrorPath go under ~/Library/Logs/<name>/.",
            "- Load it with `launchctl bootstrap gui/$(id -u) <plist>`.",
        ]
    lines += [
        "",
        "The job script must end by printing one line `CCC_OUTCOME: <one plain sentence about what this run did>`"
        " so the Jobs row shows a readable outcome, and must print any ticket refs or PR URLs it creates.",
        "",
        "Then force-refresh GET /api/jobs on the CCC dashboard (it caches for ~45s) until the new job is listed,"
        " and report its name, schedule, next run time, and the files you created.",
    ]
    return "\n".join(lines)


def job_session_name(f):
    label = f.get("name") or (f.get("what") or "").split("\n", 1)[0][:60]
    return "New job: " + label


def spawn_cwd(f, known_paths, fallback):
    """Where the agent session runs: the job's repo when it is on this laptop
    (for hermes, the local checkout of the same folder if there is one),
    else the caller's cwd / the server's repo."""
    if f["host"] == "laptop":
        return f["repo"]
    folder = os.path.basename(f["repo"].rstrip("/"))
    hits = [str(p) for p in known_paths if p and os.path.basename(str(p).rstrip("/")) == folder]
    if len(hits) == 1:
        return hits[0]
    return fallback


def build_spawn_body(f, payload, cwd):
    """The /api/sessions/spawn body for this job. Attribution fields the
    caller sent (report_to, caller_pids, idempotency_key) pass through."""
    body = {"prompt": compose_job_prompt(f), "name": job_session_name(f), "cwd": cwd}
    for src, dst in (("engine", "engine"), ("model", "model"), ("effort", "reasoning_effort")):
        if f.get(src):
            body[dst] = f[src]
    for key in ("report_to", "caller_pids", "caller_cwd", "idempotency_key"):
        if payload.get(key):
            body[key] = payload[key]
    return body


def post_spawn(port, body, user_agent="", timeout=60):
    """POST the spawn body to this server's own /api/sessions/spawn over
    loopback, so jobs go through the exact path `ccc spawn` uses."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if user_agent:
        headers["User-Agent"] = user_agent
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}/api/sessions/spawn",
        data=json.dumps(body).encode("utf-8"), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("ok", False)
        data.setdefault("error", f"HTTP {e.code}")
        return e.code, data
    except OSError as e:
        return 502, {"ok": False, "error": f"spawn request failed: {e}"}


def handle_jobs_add(payload, known_paths, port, fallback_cwd, user_agent=""):
    """Returns (response, http_status)."""
    f, err = validate_job_add(payload, known_paths)
    if err:
        return {"ok": False, "error": err}, 400
    cwd = (payload.get("cwd") or "").strip() if isinstance(payload.get("cwd"), str) else ""
    body = build_spawn_body(f, payload, spawn_cwd(f, known_paths, cwd or fallback_cwd))
    status, data = post_spawn(port, body, user_agent=user_agent)
    if not isinstance(data, dict) or not data.get("ok"):
        err = (data or {}).get("error") if isinstance(data, dict) else None
        return {"ok": False, "error": err or f"spawn failed (HTTP {status})"}, (status if status >= 400 else 502)
    out = {
        "ok": True,
        "session_id": data.get("session_id") or "",
        "session_name": body["name"],
        "host": f["host"],
        "repo": f["repo"],
    }
    # The UI adopts its optimistic placeholder from these.
    for key in ("spawn_id", "pid", "log", "engine"):
        if data.get(key) is not None:
            out[key] = data[key]
    return out, 200
