# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""First magic task — a novice's first real agent run.

Creates ~/CCC-Playground (a tiny git repo holding a small web page plus a
deliberately failing stdlib test), then runs one of three starter prompts
through a headless ``claude -p`` stream-json process. The run shows up in the
dashboard by itself because ``claude -p`` logs its transcript under
~/.claude/projects keyed off the playground cwd.

Free-model routing is opt-in per child process: when the free router lane
(ccc_server.free_router) is present and healthy, spawn_env() provides
ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL and the job is marked runtime="free".
When it is missing (lane not merged) or unhealthy, the run uses the ambient
Claude login and reports honestly ("on your plan") instead of claiming $0.

HTTP surface (wired in server.py):

    GET  /api/onboarding/first-task           -> status payload
    GET  /api/onboarding/first-task/<job_id>  -> job snapshot
    POST /api/onboarding/first-task           -> {task_id} -> {job_id}
    POST /api/onboarding/first-task/cancel    -> {job_id}
    POST /api/onboarding/first-task/open      -> {job_id?} open the page

Stdlib-only. Jobs live in memory; a small JSON state file remembers which
starter tasks already completed so the onboarding step can show them done.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

try:
    from ccc_server import paths as _paths
except Exception:  # pragma: no cover - direct module use in tooling
    _paths = None


# --------------------------------------------------------------------------
# Paths / state
# --------------------------------------------------------------------------

def _state_dir() -> Path:
    if _paths is not None:
        return _paths.COMMAND_CENTER_STATE_DIR
    return Path.home() / ".claude" / "command-center"


def _state_file() -> Path:
    override = (os.environ.get("CCC_FIRST_TASK_STATE") or "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return _state_dir() / "first-task.json"


def playground_path() -> Path:
    """Where the playground lives. CCC_PLAYGROUND_DIR overrides (dev/tests)."""
    override = (os.environ.get("CCC_PLAYGROUND_DIR") or "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return Path.home() / "CCC-Playground"


def _load_state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        sf = _state_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        tmp = sf.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, sf)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Playground scaffold
# --------------------------------------------------------------------------

_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>My First Project</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="card">
  <p class="eyebrow">Made with Claude Command Center</p>
  <h1 id="greeting">Hello, world!</h1>
  <p class="sub">This little page is your playground. Ask your agent to change it.</p>
  <button id="sparkleBtn" type="button">✨ A little magic</button>
  <p id="sparkleOut" class="sparkle-out" aria-live="polite"></p>
</div>
<script src="script.js"></script>
</body>
</html>
"""

_STYLE_CSS = """* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: linear-gradient(135deg, #fff7ed 0%, #fce7f3 50%, #dbeafe 100%);
  color: #1f2328;
}
.card {
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 20px;
  box-shadow: 0 20px 60px rgba(80, 60, 120, 0.15);
  padding: 48px 56px;
  text-align: center;
  max-width: 480px;
}
.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-size: 12px;
  color: #8250df;
  margin: 0 0 8px;
}
h1 { margin: 0 0 12px; font-size: 40px; }
.sub { color: #57606a; margin: 0 0 24px; }
button {
  font: inherit;
  font-weight: 600;
  border: 0;
  border-radius: 999px;
  padding: 12px 28px;
  cursor: pointer;
  color: #fff;
  background: linear-gradient(135deg, #8250df, #d269d9);
  box-shadow: 0 6px 20px rgba(130, 80, 223, 0.35);
}
button:active { transform: translateY(1px); }
.sparkle-out { min-height: 1.4em; color: #8250df; font-weight: 600; }
"""

_SCRIPT_JS = """// My First Project: a tiny bit of magic.
(function () {
  var btn = document.getElementById('sparkleBtn');
  var out = document.getElementById('sparkleOut');
  var cheers = ['Nice!', 'Sparkly!', 'You did that!', 'Magic ✨', 'So good!'];
  var n = 0;
  if (btn && out) {
    btn.addEventListener('click', function () {
      out.textContent = cheers[n % cheers.length];
      n += 1;
    });
  }
})();
"""

_TEST_PAGE_PY = '''#!/usr/bin/env python3
"""test_page.py: a little health check for My First Project.

Runs with nothing but Python: ``python3 test_page.py``
Prints a checklist and exits non-zero if anything fails.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"


def check(name, ok, hint=""):
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if not ok and hint:
        line += f"  -- {hint}"
    print(line)
    return bool(ok)


def main():
    ok = True
    ok &= check("index.html exists", INDEX.is_file(), "create index.html in this folder")
    if not INDEX.is_file():
        sys.exit(1)
    html = INDEX.read_text(encoding="utf-8", errors="replace")
    ok &= check("page has a <title>", bool(re.search(r"<title>[^<]+</title>", html)),
                "add <title>My First Project</title> inside <head>")
    ok &= check("page wraps content in a <main> landmark",
                bool(re.search(r"<main[\\s>]", html)),
                'wrap the page content in <main>...</main> so screen readers can find it')
    ok &= check("greeting element present", 'id="greeting"' in html,
                'keep an element with id="greeting"')
    ok &= check("stylesheet linked", 'href="style.css"' in html,
                'link style.css in <head>')
    ok &= check("script linked", 'src="script.js"' in html,
                'load script.js before </body>')
    print("All good!" if ok else "Some checks failed.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
'''

_README_MD = """# My First Project

A tiny playground page that Claude Command Center made for your very first
agent run. Open `index.html` in a browser, then ask your agent to change it.

- `index.html`, `style.css`, `script.js`: the page
- `test_page.py`: a small checklist, run `python3 test_page.py`
"""


def _playground_files() -> dict:
    return {
        "index.html": _INDEX_HTML,
        "style.css": _STYLE_CSS,
        "script.js": _SCRIPT_JS,
        "test_page.py": _TEST_PAGE_PY,
        "README.md": _README_MD,
    }


def ensure_playground(playground: Path = None) -> dict:
    """Create the playground repo if missing. Idempotent — never overwrites
    files the user (or a previous agent run) changed."""
    pg = Path(playground) if playground else playground_path()
    created = False
    files_written = []
    try:
        pg.mkdir(parents=True, exist_ok=True)
        for name, content in _playground_files().items():
            target = pg / name
            if not target.exists():
                target.write_text(content, encoding="utf-8")
                files_written.append(name)
        if files_written:
            created = True
        git_ok = False
        if (pg / ".git").is_dir():
            git_ok = True
        else:
            git = shutil.which("git")
            if git:
                try:
                    subprocess.run(
                        [git, "init", "-q"], cwd=str(pg), timeout=15,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    subprocess.run(
                        [git, "add", "-A"], cwd=str(pg), timeout=15,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    subprocess.run(
                        [git, "-c", "user.name=ccc", "-c", "user.email=ccc@localhost",
                         "commit", "-qm", "First playground"],
                        cwd=str(pg), timeout=15,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    git_ok = (pg / ".git").is_dir()
                except Exception:
                    git_ok = (pg / ".git").is_dir()
        state = _load_state()
        pg_state = state.setdefault("playground", {})
        pg_state["path"] = str(pg)
        if created and not pg_state.get("created_at"):
            pg_state["created_at"] = time.time()
        _save_state(state)
        return {
            "ok": True, "path": str(pg), "created": created,
            "files_written": files_written, "git": git_ok,
        }
    except Exception as e:
        return {"ok": False, "path": str(pg), "error": str(e)}


# --------------------------------------------------------------------------
# Task registry
# --------------------------------------------------------------------------

_PROMPT_PREAMBLE = (
    "You are helping a brand-new programmer finish their very first task. "
    "Work only inside this folder (a small playground website called "
    "CCC-Playground). Keep every change small, pretty, and easy to "
    "understand. Do not install anything. Do not use the network."
)

TASKS = [
    {
        "id": "hello-3-langs",
        "title": "Say hello in 3 languages",
        "blurb": "Your agent teaches the page to greet in English, Spanish, and French.",
        "est_seconds": 90,
        "open_file": "index.html",
        "prompt": _PROMPT_PREAMBLE + (
            "\n\nTask: make the page say hello in three languages — English, "
            "Spanish, and French (\"Hello\", \"Hola\", \"Bonjour\"). You may "
            "edit index.html, style.css, and script.js. Two easy options: show "
            "the three greetings in a small list under the heading, or make the "
            "greeting cycle between them every 2 seconds with JavaScript. "
            "Either is great.\n\nWhen you finish, reply with exactly one line: "
            "DONE: <one short sentence saying what changed>"
        ),
        "checks": [
            {"kind": "files_contain", "label": "Page says Hola and Bonjour",
             "paths": ["index.html", "script.js"], "patterns": ["hola", "bonjour"]},
        ],
    },
    {
        "id": "fix-failing-test",
        "title": "Fix the failing test",
        "blurb": "One check in test_page.py is already failing. Your agent finds it and fixes the page.",
        "est_seconds": 90,
        "open_file": "index.html",
        "prompt": _PROMPT_PREAMBLE + (
            "\n\nTask: this folder has a test file, test_page.py. Run it with "
            "`python3 test_page.py` — one check fails because index.html is "
            "missing a <main> element. Fix index.html so the test passes (wrap "
            "the page content in <main>...</main>) without changing how the "
            "page looks. Run the test again to confirm every check passes.\n\n"
            "When you finish, reply with exactly one line: "
            "DONE: <one short sentence saying what changed>"
        ),
        "checks": [
            {"kind": "command", "label": "test_page.py passes",
             "command": ["python3", "test_page.py"], "timeout_s": 30},
        ],
    },
    {
        "id": "dark-mode",
        "title": "Add a dark mode toggle",
        "blurb": "A button that flips the page into a moody dark theme.",
        "est_seconds": 120,
        "open_file": "index.html",
        "prompt": _PROMPT_PREAMBLE + (
            "\n\nTask: add a dark mode toggle to the page — a small button "
            "(for example \"🌙 Dark\") that switches the page between light and "
            "dark themes and back. Put the button in index.html, the dark "
            "styles in style.css (a `dark` class on <body> works well), and "
            "the click handler in script.js. Make the dark theme look nice.\n\n"
            "When you finish, reply with exactly one line: "
            "DONE: <one short sentence saying what changed>"
        ),
        "checks": [
            {"kind": "files_contain", "label": "Dark theme wired up",
             "paths": ["style.css", "script.js"], "patterns": ["dark"]},
        ],
    },
]

_TASK_BY_ID = {t["id"]: t for t in TASKS}


def tasks_public(state: dict = None) -> list:
    """Task list shaped for the UI (no prompt text, done flags from state)."""
    if state is None:
        state = _load_state()
    done = set(state.get("tasks_done") or [])
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "blurb": t["blurb"],
            "est_seconds": t["est_seconds"],
            "done": t["id"] in done,
        }
        for t in TASKS
    ]


# --------------------------------------------------------------------------
# Runtime env (free router from L01 when present)
# --------------------------------------------------------------------------

def _free_router_env() -> dict:
    """Return the free-router child env (L01 contract) or {} when the lane is
    not merged / not ready. Never raises."""
    try:
        from ccc_server import free_router  # lane L01
    except Exception:
        return {}
    try:
        env = free_router.spawn_env()
        return dict(env) if isinstance(env, dict) else {}
    except Exception:
        return {}


def _resolve_claude_bin() -> dict:
    """Locate a usable Claude Code CLI. Mirrors server._resolve_claude_bin's
    search order (env, PATH, common install dirs) without importing server."""
    env_bin = (os.environ.get("CCC_CLAUDE_BIN") or "").strip()
    if env_bin:
        expanded = os.path.expanduser(env_bin)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return {"available": True, "bin": expanded}
        return {"available": False, "bin": None,
                "reason": "CCC_CLAUDE_BIN is set but isn't executable"}
    found = shutil.which("claude")
    if found:
        return {"available": True, "bin": found}
    if _paths is not None:
        try:
            for candidate in _paths._iter_common_cli_candidates("claude"):
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return {"available": True, "bin": str(candidate)}
        except Exception:
            pass
    return {"available": False, "bin": None,
            "reason": "Claude Code isn't installed yet"}


# --------------------------------------------------------------------------
# Pricing — "what would this have cost at API prices"
# --------------------------------------------------------------------------

# Sonnet-class sticker prices (USD per 1M tokens). Used to price the work a
# run did regardless of which runtime paid for it.
_PRICE_PER_MTOK = {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}


def api_value_usd(usage: dict) -> float:
    """Price a token-usage dict at list API rates. Accepts both the flat
    `usage` shape and one modelUsage entry."""
    if not isinstance(usage, dict):
        return 0.0
    inp = int(usage.get("input_tokens") or usage.get("inputTokens") or 0)
    out = int(usage.get("output_tokens") or usage.get("outputTokens") or 0)
    cw = int(usage.get("cache_creation_input_tokens") or usage.get("cacheCreationInputTokens") or 0)
    cr = int(usage.get("cache_read_input_tokens") or usage.get("cacheReadInputTokens") or 0)
    return (
        inp * _PRICE_PER_MTOK["input"]
        + out * _PRICE_PER_MTOK["output"]
        + cw * _PRICE_PER_MTOK["cache_write"]
        + cr * _PRICE_PER_MTOK["cache_read"]
    ) / 1_000_000.0


# --------------------------------------------------------------------------
# Job lifecycle
# --------------------------------------------------------------------------

_JOB_KEEP = 12
_JOB_TIMEOUT_S = 360          # free models can be slow; still bounded
_LINE_KEEP = 200
_JOBS = {}
_JOBS_LOCK = threading.RLock()


def _public_job(job: dict) -> dict:
    """JSON-safe snapshot for the API."""
    with _JOBS_LOCK:
        return {
            "job_id": job["job_id"],
            "task_id": job["task_id"],
            "task_title": job.get("task_title"),
            "status": job["status"],
            "phase": job.get("phase") or "starting",
            "progress": round(float(job.get("progress") or 0.0), 3),
            "lines": list(job.get("lines") or []),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "runtime": job.get("runtime"),
            "model": job.get("model"),
            "session_id": job.get("session_id"),
            "playground": job.get("playground"),
            "result": job.get("result"),
            "usage": job.get("usage"),
            "error": job.get("error"),
        }


def _append_line(job: dict, kind: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    with _JOBS_LOCK:
        lines = job.setdefault("lines", [])
        lines.append({"t": round(time.time(), 2), "kind": kind, "text": text[:400]})
        if len(lines) > _LINE_KEEP:
            del lines[: len(lines) - _LINE_KEEP]
        job["events"] = job.get("events", 0) + 1
        job["progress"] = min(0.92, 0.05 + math.sqrt(job["events"]) * 0.09)


_TOOL_VERBS = {
    "Read": "Reading",
    "Edit": "Editing",
    "MultiEdit": "Editing",
    "Write": "Creating",
    "NotebookEdit": "Editing",
    "Bash": "Running",
    "Grep": "Searching files",
    "Glob": "Finding files",
    "LS": "Looking at the folder",
    "TodoWrite": "Planning steps",
    "WebFetch": "Fetching a page",
    "WebSearch": "Searching the web",
}


def _friendly_tool_line(block: dict) -> str:
    name = str(block.get("name") or "")
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    verb = _TOOL_VERBS.get(name, f"Using {name}" if name else "Working")
    path = inp.get("file_path") or inp.get("path") or ""
    if path:
        return f"{verb} {Path(str(path)).name}…"
    cmd = inp.get("command") or inp.get("pattern") or ""
    if cmd:
        cmd = " ".join(str(cmd).split())
        return f"{verb}: {cmd[:80]}{'…' if len(cmd) > 80 else ''}"
    return f"{verb}…"


def _consume_event(job: dict, ev: dict) -> None:
    """Translate one stream-json event into friendly progress lines."""
    etype = ev.get("type")
    if etype == "system":
        if ev.get("subtype") == "init":
            job["session_id"] = ev.get("session_id") or job.get("session_id")
            model = ev.get("model")
            if model:
                job["model"] = model
            _append_line(job, "info", "Your agent is warming up…")
        return
    if etype == "assistant":
        msg = ev.get("message") or {}
        for block in (msg.get("content") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = " ".join(str(block.get("text") or "").split())
                if text:
                    _append_line(job, "say", text)
            elif block.get("type") == "tool_use":
                _append_line(job, "tool", _friendly_tool_line(block))
        return
    if etype == "result":
        job["result_event"] = ev
        usage = ev.get("usage") or {}
        # modelUsage carries the full per-model token breakdown including
        # cache reads/writes, which dominate real runs. Sum across models.
        model_usage = ev.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:
            merged = {"input_tokens": 0, "output_tokens": 0,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
            for mu in model_usage.values():
                if not isinstance(mu, dict):
                    continue
                merged["input_tokens"] += int(mu.get("inputTokens") or mu.get("input_tokens") or 0)
                merged["output_tokens"] += int(mu.get("outputTokens") or mu.get("output_tokens") or 0)
                merged["cache_creation_input_tokens"] += int(
                    mu.get("cacheCreationInputTokens") or mu.get("cache_creation_input_tokens") or 0)
                merged["cache_read_input_tokens"] += int(
                    mu.get("cacheReadInputTokens") or mu.get("cache_read_input_tokens") or 0)
            if merged["input_tokens"] or merged["output_tokens"]:
                usage = merged
        job["usage"] = {
            "input_tokens": int(usage.get("input_tokens") or usage.get("inputTokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or usage.get("outputTokens") or 0),
            "cache_creation_input_tokens": int(
                usage.get("cache_creation_input_tokens") or usage.get("cacheCreationInputTokens") or 0),
            "cache_read_input_tokens": int(
                usage.get("cache_read_input_tokens") or usage.get("cacheReadInputTokens") or 0),
            "duration_ms": ev.get("duration_ms"),
            "num_turns": ev.get("num_turns"),
            "reported_cost_usd": ev.get("total_cost_usd"),
            "is_error": bool(ev.get("is_error")),
        }
        result_text = str(ev.get("result") or "").strip()
        if result_text:
            last_line = (job.get("lines") or [{}])[-1].get("text")
            tail = result_text.splitlines()[-1][:300]
            if tail != last_line:
                _append_line(job, "say", tail)
        return
    # user/tool_result and housekeeping events: not interesting to a novice.


def _run_checks(job: dict, playground: Path) -> dict:
    """Best-effort verification after the agent finishes. Each check returns
    {label, ok, detail}; overall verified = all ok."""
    task = _TASK_BY_ID.get(job["task_id"]) or {}
    results = []
    for check in task.get("checks") or []:
        label = check.get("label") or "check"
        try:
            if check["kind"] == "files_contain":
                haystack = ""
                for rel in check.get("paths") or []:
                    try:
                        haystack += (playground / rel).read_text(
                            encoding="utf-8", errors="replace").lower() + "\n"
                    except OSError:
                        pass
                missing = [p for p in check.get("patterns") or []
                           if str(p).lower() not in haystack]
                ok = not missing
                results.append({
                    "label": label, "ok": ok,
                    "detail": "" if ok else "missing: " + ", ".join(missing),
                })
            elif check["kind"] == "command":
                proc = subprocess.run(
                    check["command"], cwd=str(playground),
                    timeout=float(check.get("timeout_s") or 30),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True,
                )
                ok = proc.returncode == 0
                tail = (proc.stdout or "").strip().splitlines()
                results.append({
                    "label": label, "ok": ok,
                    "detail": "" if ok else (tail[-1][:200] if tail else f"exit {proc.returncode}"),
                })
        except Exception as e:
            results.append({"label": label, "ok": False, "detail": str(e)[:200]})
    return {"checks": results, "verified": bool(results) and all(r["ok"] for r in results)}


def _finish_job(job: dict, *, status: str, error: str = None) -> None:
    playground = Path(job["playground"])
    if status == "done":
        job["phase"] = "verifying"
        job["progress"] = 0.97
        job["result"] = _run_checks(job, playground)
        job["result"]["open_file"] = (_TASK_BY_ID.get(job["task_id"]) or {}).get("open_file") or "index.html"
        usage = job.get("usage") or {}
        # API-priced worth of the work: the larger of the token-derived price
        # and what the CLI itself reported (total_cost_usd already reflects
        # full-context billing that usage deltas can undercount).
        value = api_value_usd(usage)
        try:
            value = max(value, float(usage.get("reported_cost_usd") or 0))
        except (TypeError, ValueError):
            pass
        usage["api_value_usd"] = round(value, 4)
        usage["cost_usd"] = 0.0 if job.get("runtime") == "free" else usage.get("reported_cost_usd") or 0.0
        job["usage"] = usage
        if job["result"]["verified"]:
            _append_line(job, "info", "Everything checks out.")
        state = _load_state()
        done = state.setdefault("tasks_done", [])
        if job["task_id"] not in done:
            done.append(job["task_id"])
        runs = state.setdefault("runs", [])
        runs.append({
            "job_id": job["job_id"], "task_id": job["task_id"],
            "finished_at": time.time(), "runtime": job.get("runtime"),
            "ok": True, "api_value_usd": usage["api_value_usd"],
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        })
        del runs[:-50]
        _save_state(state)
    job["status"] = status
    job["phase"] = "done" if status == "done" else status
    job["progress"] = 1.0 if status == "done" else job.get("progress", 0)
    job["finished_at"] = time.time()
    if error:
        job["error"] = error


def _kill_proc(job: dict) -> None:
    proc = job.get("proc")
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _run_job(job: dict, prompt: str, cmd_env: dict, playground: Path) -> None:
    proc = None
    try:
        proc = subprocess.Popen(
            job["argv"],
            cwd=str(playground), env=cmd_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, text=True, errors="replace",
        )
        job["proc"] = proc
        # Watchdog: a stalled agent emits no stdout, so the read loop alone can
        # never notice the deadline. A timer that kills the process group
        # closes stdout and unblocks it.
        timed_out = {"v": False}

        def _watchdog():
            timed_out["v"] = True
            _append_line(job, "info", "Taking a while. Wrapping up…")
            _kill_proc(job)

        timer = threading.Timer(_JOB_TIMEOUT_S, _watchdog)
        timer.daemon = True
        timer.start()
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if job.get("cancelled") or timed_out["v"]:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    # Non-JSON stdout (CLI warnings, banners): keep the tail quiet.
                    if not line.startswith("{"):
                        _append_line(job, "info", line[:200])
                    continue
                if isinstance(ev, dict):
                    _consume_event(job, ev)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                timed_out["v"] = True
        finally:
            timer.cancel()
        if job.get("cancelled"):
            _kill_proc(job)
            _finish_job(job, status="cancelled", error="Cancelled")
            return
        if timed_out["v"] or (proc.poll() is None):
            _kill_proc(job)
            _finish_job(job, status="error",
                        error="Your agent took too long this time. Try again, it happens.")
            return
        if proc.returncode != 0:
            usage = job.get("usage") or {}
            err = None
            if usage.get("is_error"):
                err = "The agent hit an error."
            _append_line(job, "error", f"Agent exited (code {proc.returncode}).")
            _finish_job(job, status="error",
                        error=err or "The agent stopped before finishing. Try running it again.")
            return
        usage = job.get("usage") or {}
        if usage.get("is_error"):
            _finish_job(job, status="error",
                        error="The agent reported an error. Try again.")
            return
        _finish_job(job, status="done")
    except Exception as e:
        if proc is not None:
            _kill_proc(job)
        _append_line(job, "error", str(e)[:200])
        _finish_job(job, status="error", error=f"Couldn't run the agent: {e}")


def start_task(task_id: str, *, claude_bin: str = None, extra_env: dict = None,
               playground: Path = None) -> tuple:
    """Start a first-task job. Returns (job_snapshot, error_dict)."""
    task = _TASK_BY_ID.get(str(task_id or "").strip())
    if task is None:
        return None, {"ok": False, "error": "unknown task",
                      "tasks": [t["id"] for t in TASKS]}
    with _JOBS_LOCK:
        for j in _JOBS.values():
            if j["status"] == "running":
                return None, {"ok": False, "error": "a first task is already running",
                              "job_id": j["job_id"], "code": "busy"}
    pg = Path(playground) if playground else playground_path()
    ensured = ensure_playground(pg)
    if not ensured.get("ok"):
        return None, {"ok": False, "error": f"couldn't create the playground: {ensured.get('error')}"}

    if not claude_bin:
        info = _resolve_claude_bin()
        if not info.get("available"):
            return None, {"ok": False, "error": info.get("reason") or "Claude Code isn't installed yet",
                          "code": "claude_unavailable"}
        claude_bin = info["bin"]

    env = dict(os.environ)
    # CCC sets these inside its own spawned children; a first task is a fresh
    # session, not a relay target.
    for k in ("CCC_RELAY_QUESTIONS", "CCC_QUESTION_RELAY_DIR"):
        env.pop(k, None)
    free_env = _free_router_env()
    runtime = "standard"
    if free_env:
        env.update({k: str(v) for k, v in free_env.items()})
        runtime = "free"
    if extra_env:
        env.update(extra_env)

    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "task_id": task["id"],
        "task_title": task["title"],
        "status": "running",
        "phase": "starting",
        "progress": 0.02,
        "lines": [],
        "started_at": time.time(),
        "runtime": runtime,
        "model": env.get("ANTHROPIC_MODEL"),
        "playground": str(pg),
        "argv": [claude_bin, "-p", task["prompt"], "--verbose",
                 "--output-format", "stream-json", "--dangerously-skip-permissions"],
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        # Trim finished history.
        finished = [k for k, j in _JOBS.items() if j["status"] != "running"]
        for k in finished[: max(0, len(finished) - _JOB_KEEP)]:
            _JOBS.pop(k, None)
    _append_line(job, "info", f"Starting: {task['title']}")
    thread = threading.Thread(target=_run_job, args=(job, task["prompt"], env, pg), daemon=True)
    job["thread"] = thread
    thread.start()
    return _public_job(job), None


def get_job(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(str(job_id or ""))
    return _public_job(job) if job else None


def cancel_job(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(str(job_id or ""))
    if not job:
        return {"ok": False, "error": "no such job"}
    if job["status"] != "running":
        return {"ok": True, "status": job["status"], "already_finished": True}
    job["cancelled"] = True
    _kill_proc(job)
    _finish_job(job, status="cancelled", error="Cancelled")
    return {"ok": True, "status": "cancelled"}


def open_result(job_id: str = None, opener=None) -> dict:
    """Open the playground page in the browser. Path is clamped to the
    playground directory so this can never `open` an arbitrary file."""
    pg = playground_path()
    try:
        pg_real = Path(os.path.realpath(str(pg)))
    except Exception:
        pg_real = pg
    rel = "index.html"
    if job_id:
        with _JOBS_LOCK:
            job = _JOBS.get(str(job_id))
        if job:
            task = _TASK_BY_ID.get(job["task_id"]) or {}
            rel = task.get("open_file") or rel
            pg = Path(job.get("playground") or pg)
            try:
                pg_real = Path(os.path.realpath(str(pg)))
            except Exception:
                pg_real = pg
    target = (pg_real / rel).resolve()
    try:
        if pg_real != target and pg_real not in target.parents:
            return {"ok": False, "error": "path outside the playground"}
    except Exception:
        return {"ok": False, "error": "path outside the playground"}
    if not target.is_file():
        return {"ok": False, "error": "nothing to open yet. Run a task first.",
                "path": str(target)}
    if opener is None:
        if sys.platform == "darwin":
            opener = lambda p: subprocess.Popen(["open", str(p)])
        elif os.name == "nt":
            opener = lambda p: os.startfile(str(p))  # noqa: S606 - user's own file
        else:
            opener = lambda p: subprocess.Popen(["xdg-open", str(p)])
    try:
        opener(target)
        return {"ok": True, "path": str(target)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------
# Status payload
# --------------------------------------------------------------------------

_free_ready_cache = {"t": 0.0, "ready": False, "base_url": None}
_FREE_READY_TTL = 10.0


def _free_ready() -> tuple:
    """Cached probe: is the free router usable? Cheap for polling UIs."""
    now = time.time()
    if now - _free_ready_cache["t"] < _FREE_READY_TTL:
        return _free_ready_cache["ready"], _free_ready_cache["base_url"]
    env = _free_router_env()
    ready = bool(env)
    base_url = env.get("ANTHROPIC_BASE_URL") if ready else None
    _free_ready_cache.update({"t": now, "ready": ready, "base_url": base_url})
    return ready, base_url


def status() -> dict:
    pg = playground_path()
    state = _load_state()
    files = []
    try:
        files = sorted(p.name for p in pg.iterdir() if p.is_file()) if pg.is_dir() else []
    except OSError:
        files = []
    with _JOBS_LOCK:
        jobs = sorted(_JOBS.values(), key=lambda j: j.get("started_at") or 0)
    active = _public_job(jobs[-1]) if jobs and jobs[-1]["status"] == "running" else None
    last = None
    for j in reversed(jobs):
        if j["status"] != "running":
            last = _public_job(j)
            break
    free_ready, free_base = _free_ready()
    return {
        "ok": True,
        "playground": {
            "path": str(pg),
            "exists": pg.is_dir(),
            "files": files,
            "created_at": (state.get("playground") or {}).get("created_at"),
        },
        "tasks": tasks_public(state),
        "any_done": bool(state.get("tasks_done")),
        "active_job": active,
        "last_job": last,
        "runtime": {
            "free_ready": free_ready,
            "free_base_url": free_base,
            "claude_installed": _resolve_claude_bin().get("available", False),
        },
    }
