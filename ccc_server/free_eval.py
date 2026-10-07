# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Free-model benchmark: races catalog models through five tiny coding tasks.

Talks Anthropic wire format to the local freellmapi router
(``POST {base}/v1/messages``), reads the OpenAI-shaped catalog from
``GET {base}/v1/models``, and pins the winning tool-capable model as Claude
Code's default through the router's admin ``anthropic-map`` setting.

Every router touch point degrades quietly: no state file, no unified key, an
unreachable port -> ``models_payload()`` returns ``[]`` and ``start_eval()``
answers with a friendly error instead of a traceback. Secrets (unified key,
admin password) are never logged, never returned in responses, and never
written to the persisted leaderboard file.

Stdlib-only, like the rest of server.py's runtime.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

DEFAULT_ROUTER_PORT = 3017  # the CCC-managed freellmapi port (PLAN.md)
REQUEST_TIMEOUT_S = 75      # free models can be slow; keep a hard bound
TASK_WALL_BUDGET_S = 300    # a single task never burns more than 5 minutes
MAX_TOOL_TURNS = 8          # agent loop bound per task
MAX_TOOL_CALLS_PER_TURN = 4
MAX_EVAL_MODELS = 8         # candidate cap for one benchmark run
MAX_JOB_LINES = 200         # mirrors the setup-job contract shape
CATALOG_TTL_OK_S = 20.0     # GET /api/free-router/models polls hit this cache
CATALOG_TTL_ERR_S = 8.0     # unreachable routers stay quiet, but not forever
CHECK_CMD_TIMEOUT_S = 25

ENV_ROUTER_URL = "CCC_FREE_ROUTER_URL"
ENV_ROUTER_KEY = "CCC_FREE_ROUTER_KEY"
ENV_ADMIN_EMAIL = "CCC_FREE_ROUTER_ADMIN_EMAIL"
ENV_ADMIN_PASSWORD = "CCC_FREE_ROUTER_ADMIN_PASSWORD"
ENV_ROUTER_STATE = "CCC_FREE_ROUTER_STATE"
ENV_EVAL_STATE = "CCC_FREE_EVAL_STATE"
ENV_MAX_MODELS = "CCC_FREE_EVAL_MAX_MODELS"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _router_state_path() -> Path:
    override = os.environ.get(ENV_ROUTER_STATE)
    if override:
        return Path(override)
    return Path.home() / ".ccc" / "free-router.json"


def _eval_state_path() -> Path:
    override = os.environ.get(ENV_EVAL_STATE)
    if override:
        return Path(override)
    return Path.home() / ".ccc" / "free-eval.json"


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib urllib; never raises)
# ---------------------------------------------------------------------------

def _http_json(method, url, body=None, headers=None, timeout=REQUEST_TIMEOUT_S):
    """Return ``(status, payload, error)``. status 0 means transport failure."""
    req = urllib.request.Request(url, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8", "replace")), None
            except ValueError:
                return resp.status, None, "invalid JSON response"
    except urllib.error.HTTPError as e:
        try:
            parsed = json.loads(e.read().decode("utf-8", "replace"))
        except (ValueError, OSError):
            parsed = None
        return e.code, parsed, _http_error_message(parsed) or f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, None, str(e)


def _http_error_message(payload):
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
        if payload.get("message"):
            return str(payload["message"])
    return None


# ---------------------------------------------------------------------------
# Router discovery
# ---------------------------------------------------------------------------

def _load_router_state():
    """L01 writes ~/.ccc/free-router.json; read it tolerantly (any spelling)."""
    try:
        data = json.loads(_router_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_string(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _module_router_status():
    """Best-effort read of L01's ccc_server.free_router when it exists."""
    try:
        from ccc_server import free_router  # noqa: PLC0415 - optional lane
    except Exception:
        return None
    for attr in ("router_status", "status", "get_status"):
        fn = getattr(free_router, attr, None)
        if not callable(fn):
            continue
        try:
            result = fn()
        except Exception:
            continue
        if isinstance(result, dict) and (result.get("base_url") or result.get("port")):
            return result
    return None


def _module_secret(*names):
    """Pull a key-like string out of L01's module if it exposes one."""
    try:
        from ccc_server import free_router  # noqa: PLC0415 - optional lane
    except Exception:
        return ""
    for name in names:
        value = getattr(free_router, name, None)
        try:
            candidate = value() if callable(value) else value
        except Exception:
            continue
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


_CONFIG_LOCK = threading.Lock()
_CONFIG_CACHE = {"ts": 0.0, "cfg": "unset", "probed": False}
_CONFIG_TTL_S = 15.0


def router_config(probe=True, force=False):
    """Resolve ``{base_url, unified_key, admin_email, admin_password, source}``.

    Order: explicit env, L01's module, the ~/.ccc/free-router.json state file,
    then a fast probe of the CCC-managed port. ``probe=False`` skips the
    network hop (status display only). Cached briefly so polling endpoints
    never pay the discovery cost per request; ``force`` bypasses the cache
    for callers about to do real work.
    """
    now = time.monotonic()
    if not force:
        with _CONFIG_LOCK:
            fresh = (now - _CONFIG_CACHE["ts"]) < _CONFIG_TTL_S
            if _CONFIG_CACHE["cfg"] != "unset" and fresh:
                cached = _CONFIG_CACHE["cfg"]
                if cached is not None or not probe or _CONFIG_CACHE["probed"]:
                    return dict(cached) if cached else None
    cfg = _resolve_router_config(probe)
    with _CONFIG_LOCK:
        _CONFIG_CACHE.update({"ts": time.monotonic(), "probed": probe,
                              "cfg": dict(cfg) if cfg else None})
    return cfg


def _resolve_router_config(probe):
    cfg = {
        "base_url": "",
        "unified_key": "",
        "admin_email": "",
        "admin_password": "",
        "source": "",
    }
    env_url = (os.environ.get(ENV_ROUTER_URL) or "").strip().rstrip("/")
    if env_url:
        cfg["base_url"] = env_url
        cfg["source"] = "env"

    state = _load_router_state()
    state_port = state.get("port")
    if not cfg["base_url"]:
        module_status = _module_router_status()
        if module_status:
            base = str(module_status.get("base_url") or "").strip().rstrip("/")
            if not base and module_status.get("port"):
                base = f"http://127.0.0.1:{module_status['port']}"
            if base:
                cfg["base_url"] = base
                cfg["source"] = "module"
    if not cfg["base_url"] and state_port:
        cfg["base_url"] = f"http://127.0.0.1:{state_port}"
        cfg["source"] = "state-file"
    if not cfg["base_url"] and probe:
        candidate = f"http://127.0.0.1:{DEFAULT_ROUTER_PORT}"
        status, _payload, err = _http_json("GET", candidate + "/api/ping", timeout=1.5)
        if status and status < 500 and err is None:
            cfg["base_url"] = candidate
            cfg["source"] = "probe"
    if not cfg["base_url"]:
        return None

    cfg["unified_key"] = (
        (os.environ.get(ENV_ROUTER_KEY) or "").strip()
        or _module_secret("unified_key", "get_unified_key", "api_key")
        or _first_string(state, "unified_key", "api_key", "unifiedApiKey", "apiKey")
    )
    admin = state.get("admin") if isinstance(state.get("admin"), dict) else {}
    cfg["admin_email"] = (
        (os.environ.get(ENV_ADMIN_EMAIL) or "").strip()
        or _first_string(admin, "email")
        or _first_string(state, "admin_email", "adminEmail")
    )
    cfg["admin_password"] = (
        os.environ.get(ENV_ADMIN_PASSWORD)
        or _first_string(admin, "password")
        or _first_string(state, "admin_password", "adminPassword")
    )
    if not cfg["unified_key"] and cfg["admin_email"] and cfg["admin_password"]:
        cfg["unified_key"] = _fetch_unified_key(cfg) or ""
    return cfg


def _inference_headers(cfg):
    """Auth headers for /v1/* — freellmapi accepts Bearer or x-api-key."""
    headers = {"anthropic-version": "2023-06-01"}
    if cfg.get("unified_key"):
        headers["Authorization"] = f"Bearer {cfg['unified_key']}"
        headers["x-api-key"] = cfg["unified_key"]
    return headers


def _admin_login(cfg):
    """POST /api/auth/login -> session token, or None."""
    if not cfg.get("admin_email") or not cfg.get("admin_password"):
        return None
    status, payload, _err = _http_json(
        "POST",
        cfg["base_url"] + "/api/auth/login",
        body={"email": cfg["admin_email"], "password": cfg["admin_password"]},
        timeout=10,
    )
    if status == 200 and isinstance(payload, dict) and payload.get("token"):
        return str(payload["token"])
    return None


def _fetch_unified_key(cfg):
    """GET /api/settings/api-key behind an admin login."""
    token = _admin_login(cfg)
    if not token:
        return None
    status, payload, _err = _http_json(
        "GET",
        cfg["base_url"] + "/api/settings/api-key",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if status == 200 and isinstance(payload, dict) and payload.get("apiKey"):
        return str(payload["apiKey"])
    return None


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

_CATALOG_LOCK = threading.Lock()
_CATALOG_CACHE = {"key": None, "ts": 0.0, "models": [], "error": None}


def _is_virtual_model_id(model_id):
    """Router pseudo-entries, not real catalog models."""
    mid = model_id.strip().lower()
    if mid in ("auto", "fusion", "default"):
        return True
    if mid.startswith(("auto:", "claude/")):
        return True
    # Claude-family discovery aliases (claude-sonnet-4-5 etc.) ride along so
    # Claude Code's picker can see anything at all — they are not models.
    if mid.startswith("claude-"):
        return True
    return False


def _normalize_catalog_row(raw):
    mid = str(raw.get("id") or "").strip()
    params = raw.get("supported_parameters")
    tools = isinstance(params, list) and "tools" in params
    status = str(raw.get("execution_status") or "")
    return {
        "id": mid,
        "name": str(raw.get("name") or mid),
        "platform": str(raw.get("owned_by") or ""),
        "context": raw.get("context_window") or raw.get("context_length"),
        "available": bool(raw.get("available")),
        "execution_status": status,
        "ready": status == "ready" or (not status and bool(raw.get("available"))),
        "supports_tools": tools,
    }


def fetch_catalog(cfg):
    """GET {base}/v1/models -> normalized rows. Raises nothing; returns
    ``(rows, error)``."""
    if not cfg or not cfg.get("base_url"):
        return [], "no router"
    status, payload, err = _http_json(
        "GET",
        cfg["base_url"] + "/v1/models",
        headers={key: value for key, value in _inference_headers(cfg).items()
                 if key != "anthropic-version"},
        timeout=12,
    )
    if status != 200 or not isinstance(payload, dict):
        if status == 401:
            return [], "the router needs its unified key"
        return [], err or f"HTTP {status}"
    rows = []
    for raw in payload.get("data") or []:
        if not isinstance(raw, dict):
            continue
        mid = str(raw.get("id") or "").strip()
        if not mid or _is_virtual_model_id(mid):
            continue
        rows.append(_normalize_catalog_row(raw))
    return rows, None


def catalog_cached(cfg):
    """TTL-cached catalog for the polling models endpoint."""
    key = (cfg or {}).get("base_url") or "none"
    now = time.monotonic()
    with _CATALOG_LOCK:
        entry = _CATALOG_CACHE
        ttl = CATALOG_TTL_OK_S if entry["error"] is None else CATALOG_TTL_ERR_S
        if entry["key"] == key and (now - entry["ts"]) < ttl:
            return list(entry["models"]), entry["error"]
    rows, error = fetch_catalog(cfg)
    with _CATALOG_LOCK:
        _CATALOG_CACHE.update(
            {"key": key, "ts": time.monotonic(), "models": list(rows), "error": error}
        )
    return rows, error


# ---------------------------------------------------------------------------
# Sandbox + tools
# ---------------------------------------------------------------------------

def _safe_join(workdir, rel):
    """Resolve ``rel`` inside ``workdir``; None when it escapes."""
    try:
        base = Path(workdir).resolve()
        target = (base / str(rel or "")).resolve()
        target.relative_to(base)
        return target
    except (OSError, ValueError):
        return None


def _list_workspace(workdir):
    base = Path(workdir)
    try:
        names = sorted(
            str(p.relative_to(base))
            for p in base.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )
    except OSError:
        names = []
    return names


EVAL_TOOLS = [
    {
        "name": "list_files",
        "description": "List the files in the workspace. Call with no arguments.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a UTF-8 text file in the workspace, replacing existing content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_checks",
        "description": (
            "Run the task's checks in the workspace and report PASS or FAIL "
            "with details. Call this when you believe the task is done."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

SYSTEM_PROMPT = (
    "You are completing a tiny coding task inside a scratch workspace. "
    "Use the provided tools to look around, change files, and verify. "
    "Always act with tools instead of explaining what you would do. "
    "When you think the task is done, call run_checks. If it says FAIL, "
    "fix what it reports and call run_checks again."
)


def _exec_tool(task, workdir, name, tool_input):
    """Run one tool call inside the sandbox; always returns a string."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if name == "list_files":
        names = _list_workspace(workdir)
        return "\n".join(names) if names else "(workspace is empty)"
    if name == "read_file":
        target = _safe_join(workdir, tool_input.get("path"))
        if target is None or not target.is_file():
            return f"error: no such file: {tool_input.get('path')}"
        try:
            return target.read_text(encoding="utf-8", errors="replace")[:8000]
        except OSError as e:
            return f"error: {e}"
    if name == "write_file":
        target = _safe_join(workdir, tool_input.get("path"))
        if target is None:
            return f"error: bad path: {tool_input.get('path')}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(tool_input.get("content") or ""), encoding="utf-8")
            return f"wrote {target.relative_to(Path(workdir).resolve())}"
        except OSError as e:
            return f"error: {e}"
    if name == "run_checks":
        ok, detail = task["check"](workdir)
        return "PASS" if ok else f"FAIL: {detail}"
    return f"error: unknown tool {name!r}"


def _run_check_command(argv, cwd):
    """Run a fixed check command; ``(ok, detail)``. The model never chooses
    the command — run_checks replays whatever the task pinned at setup."""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=CHECK_CMD_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out.strip().splitlines()[-6:] if out.strip() else []
    if proc.returncode == 0:
        return True, "ok"
    return False, "\n".join(tail) or f"exit {proc.returncode}"


# ---------------------------------------------------------------------------
# The five tasks
# ---------------------------------------------------------------------------

def _write(workdir, rel, content):
    target = Path(workdir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _task_edit_file():
    def setup(wd):
        _write(
            wd,
            "greet.py",
            'def greet():\n    return "Helo, world!"\n\n'
            'if __name__ == "__main__":\n    print(greet())\n',
        )

    def check(wd):
        try:
            proc = subprocess.run(
                [sys.executable, "greet.py"],
                cwd=str(wd), capture_output=True, text=True,
                timeout=CHECK_CMD_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, str(e)
        if proc.returncode != 0:
            return False, (proc.stderr or "script failed").strip()[-300:]
        out = (proc.stdout or "").strip()
        if out == "Hello, world!":
            return True, "ok"
        return False, f"greet.py printed {out!r}, expected 'Hello, world!'"

    return {
        "id": "edit_file",
        "title": "Fix a typo in a file",
        "setup": setup,
        "prompt": (
            "greet.py should print exactly `Hello, world!` but it has a typo. "
            "Read the file, fix it so running `python3 greet.py` prints "
            "`Hello, world!`, then call run_checks."
        ),
        "check": check,
        "needs_tools": True,
    }


def _task_fix_test():
    def setup(wd):
        _write(
            wd,
            "calc.py",
            "def add(a, b):\n    return a + b\n\n"
            "def subtract(a, b):\n    return a + b\n",
        )
        _write(
            wd,
            "test_calc.py",
            "import unittest\n\nimport calc\n\n\n"
            "class CalcTests(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(calc.add(2, 3), 5)\n\n"
            "    def test_subtract(self):\n"
            "        self.assertEqual(calc.subtract(5, 3), 2)\n\n\n"
            'if __name__ == "__main__":\n    unittest.main()\n',
        )

    def check(wd):
        return _run_check_command(
            [sys.executable, "-m", "unittest", "test_calc"], wd
        )

    return {
        "id": "fix_test",
        "title": "Make a failing test pass",
        "setup": setup,
        "prompt": (
            "test_calc.py runs a test that fails: subtract(5, 3) should "
            "return 2 but the implementation in calc.py is wrong. Fix calc.py "
            "so `python3 -m unittest test_calc` passes, then call run_checks."
        ),
        "check": check,
        "needs_tools": True,
    }


def _task_add_function():
    def setup(wd):
        _write(
            wd,
            "string_tools.py",
            'def shout(text):\n    return text.upper() + "!"\n',
        )
        _write(
            wd,
            "check_strings.py",
            "import string_tools\n\n"
            'assert string_tools.shout("hey") == "HEY!"\n'
            'assert string_tools.whisper("LOUD NOISES") == "loud noises"\n'
            'assert string_tools.whisper("MiXeD") == "mixed"\n'
            'print("ok")\n',
        )

    def check(wd):
        return _run_check_command([sys.executable, "check_strings.py"], wd)

    return {
        "id": "add_function",
        "title": "Add a new function",
        "setup": setup,
        "prompt": (
            "Add a function `whisper(text)` to string_tools.py that returns "
            "the text lowercased. check_strings.py verifies it (and that "
            "shout still works) via `python3 check_strings.py`. Make it pass, "
            "then call run_checks."
        ),
        "check": check,
        "needs_tools": True,
    }


def _task_read_write():
    def setup(wd):
        _write(wd, "data/notes.txt",
               "Team standup notes.\nThe codeword is plum.\nRemember it.\n")

    def check(wd):
        answer = Path(wd) / "answer.txt"
        if not answer.is_file():
            return False, "answer.txt does not exist yet"
        content = answer.read_text(encoding="utf-8", errors="replace").strip().lower()
        if content == "plum":
            return True, "ok"
        return False, f"answer.txt contains {content!r}, expected 'plum'"

    return {
        "id": "tool_read_write",
        "title": "Read one file, write another",
        "setup": setup,
        "prompt": (
            "data/notes.txt hides a codeword. Read the file, find the "
            "codeword, and write just the codeword (lowercase, nothing else) "
            "to answer.txt. Then call run_checks."
        ),
        "check": check,
        "needs_tools": True,
    }


def _task_multi_step():
    def setup(wd):
        _write(wd, "a.txt", "19\n")
        _write(wd, "b.txt", "23\n")

    def check(wd):
        target = Path(wd) / "sum.txt"
        if not target.is_file():
            return False, "sum.txt does not exist yet"
        content = target.read_text(encoding="utf-8", errors="replace").strip()
        if content == "42":
            return True, "ok"
        return False, f"sum.txt contains {content!r}, expected '42'"

    return {
        "id": "multi_step",
        "title": "Read two files, combine, write",
        "setup": setup,
        "prompt": (
            "a.txt and b.txt each contain a single number. Read both, add "
            "them, and write the sum (just the number) to sum.txt. Then call "
            "run_checks."
        ),
        "check": check,
        "needs_tools": True,
    }


def eval_tasks():
    """Fresh task dicts (setup/check closures are single-use per sandbox)."""
    return [
        _task_edit_file(),
        _task_fix_test(),
        _task_add_function(),
        _task_read_write(),
        _task_multi_step(),
    ]


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def _chat(cfg, model_id, messages):
    """One POST /v1/messages. ``(status, payload, error, latency_ms)``."""
    body = {
        "model": model_id,
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "messages": messages,
        "tools": EVAL_TOOLS,
        "tool_choice": {"type": "auto"},
        "stream": False,
    }
    started = time.monotonic()
    status, payload, err = _http_json(
        "POST",
        cfg["base_url"] + "/v1/messages",
        body=body,
        headers=_inference_headers(cfg),
        timeout=REQUEST_TIMEOUT_S,
    )
    return status, payload, err, int((time.monotonic() - started) * 1000)


def run_task(cfg, model_id, task, workdir, deadline=None):
    """Drive one task to completion. Returns a per-task result dict."""
    task["setup"](workdir)
    listing = ", ".join(_list_workspace(workdir)) or "(empty)"
    messages = [
        {
            "role": "user",
            "content": task["prompt"] + f"\n\nWorkspace files: {listing}",
        }
    ]
    started = time.monotonic()
    request_ms = []
    turns = 0
    tool_calls = 0
    error = None

    for _turn in range(MAX_TOOL_TURNS):
        if deadline and time.monotonic() > deadline:
            error = "time budget reached"
            break
        status, payload, err, ms = _chat(cfg, model_id, messages)
        request_ms.append(ms)
        if status != 200 or not isinstance(payload, dict):
            error = err or f"HTTP {status}"
            break
        content = payload.get("content") or []
        messages.append({"role": "assistant", "content": content})
        calls = [
            block for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ][:MAX_TOOL_CALLS_PER_TURN]
        if not calls:
            break
        results = []
        for call in calls:
            tool_calls += 1
            output = _exec_tool(
                task, workdir, call.get("name"), call.get("input")
            )
            results.append({
                "type": "tool_result",
                "tool_use_id": str(call.get("id") or f"call_{tool_calls}"),
                "content": output,
            })
        messages.append({"role": "user", "content": results})
        turns += 1
        # A model that passed checks on its own run_checks can stop early:
        # the final grade below re-runs them, so exiting here is honest.
        if any(str(r["content"]).startswith("PASS") for r in results):
            break
    else:
        error = error or "too many tool turns"

    ok, detail = task["check"](workdir)
    latency_ms = int((time.monotonic() - started) * 1000)
    return {
        "task": task["id"],
        "title": task["title"],
        "passed": bool(ok),
        "detail": detail if not ok else "ok",
        "error": error,
        "latency_ms": latency_ms,
        "median_request_ms": int(statistics.median(request_ms)) if request_ms else None,
        "requests": len(request_ms),
        "turns": turns,
        "tool_calls": tool_calls,
        "used_tools": tool_calls > 0,
    }


def _score_model(per_task):
    """Composite 0-100: 80% pass rate + 20% speed."""
    passed = sum(1 for t in per_task if t["passed"])
    pass_rate = passed / max(1, len(per_task))
    # 0 ms is a real (sub-millisecond) median, not "no data".
    medians = [t["median_request_ms"] for t in per_task
               if t.get("median_request_ms") is not None]
    med = statistics.median(medians) if medians else None
    if med is None:
        speed = 0.0
    elif med <= 1500:
        speed = 1.0
    elif med >= 20000:
        speed = 0.0
    else:
        speed = (20000 - med) / (20000 - 1500)
    score = round(100 * (0.8 * pass_rate + 0.2 * speed), 1)
    return {
        "score": score,
        "passed": passed,
        "tasks": len(per_task),
        "pass_rate": round(pass_rate, 3),
        "median_request_ms": int(med) if med is not None else None,
    }


def run_model_eval(cfg, model_row, log=None):
    """Race one model through all five tasks. Never raises."""
    model_id = model_row["id"]
    per_task = []
    deadline = time.monotonic() + len(eval_tasks()) * TASK_WALL_BUDGET_S
    for task in eval_tasks():
        with tempfile.TemporaryDirectory(prefix="ccc-free-eval-") as wd:
            try:
                result = run_task(cfg, model_id, task, wd, deadline=deadline)
            except Exception as e:  # a task must never kill the whole run
                result = {
                    "task": task["id"], "title": task["title"],
                    "passed": False, "detail": str(e)[:200], "error": str(e)[:200],
                    "latency_ms": 0, "median_request_ms": None, "requests": 0,
                    "turns": 0, "tool_calls": 0, "used_tools": False,
                }
            per_task.append(result)
            if log:
                mark = "PASS" if result["passed"] else "FAIL"
                log(
                    f"  {mark} {task['title']} "
                    f"({result['latency_ms'] / 1000:.1f}s"
                    + (f", {result['tool_calls']} tool calls" if result["tool_calls"] else "")
                    + ")"
                )
    agg = _score_model(per_task)
    used_tools = any(t["used_tools"] for t in per_task)
    return {
        "id": model_id,
        "name": model_row.get("name") or model_id,
        "platform": model_row.get("platform") or "",
        "context": model_row.get("context"),
        "supports_tools": bool(model_row.get("supports_tools")) or used_tools,
        "tools_observed": used_tools,
        "ready": bool(model_row.get("ready", True)),
        "per_task": per_task,
        "evaluated_at": _now_iso(),
        **agg,
    }


# ---------------------------------------------------------------------------
# Persistence: ~/.ccc/free-eval.json
# ---------------------------------------------------------------------------

_STORE_LOCK = threading.Lock()
_STORE_CACHE = {"sig": None, "data": None}

_STORE_EMPTY = {"version": 1, "updated_at": None, "router": None,
                "tasks": ["edit_file", "fix_test", "add_function",
                          "tool_read_write", "multi_step"],
                "models": {}, "best": None}


def _load_store():
    """(mtime,size)-cached read of the leaderboard file."""
    path = _eval_state_path()
    try:
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    with _STORE_LOCK:
        if _STORE_CACHE["sig"] == sig and _STORE_CACHE["data"] is not None:
            return json.loads(json.dumps(_STORE_CACHE["data"]))
    data = None
    if sig is not None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError):
            data = None
    if data is None:
        data = dict(_STORE_EMPTY)
        data["models"] = {}
    data.setdefault("version", 1)
    data.setdefault("models", {})
    with _STORE_LOCK:
        _STORE_CACHE.update({"sig": sig, "data": data})
    return json.loads(json.dumps(data))


def _save_store(store):
    path = _eval_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(store, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        st = path.stat()
        with _STORE_LOCK:
            _STORE_CACHE.update(
                {"sig": (st.st_mtime_ns, st.st_size), "data": json.loads(json.dumps(store))}
            )
    except OSError:
        pass


def _merge_results(store, results, cfg, pinned_info):
    models = store.setdefault("models", {})
    for result in results:
        models[result["id"]] = result
    store["updated_at"] = _now_iso()
    store["router"] = cfg.get("base_url")
    if pinned_info:
        store["best"] = pinned_info
    _save_store(store)


# ---------------------------------------------------------------------------
# Ranking + pinning
# ---------------------------------------------------------------------------

def _ranked(store_models):
    """Evaluated entries best-first: score, then faster median request."""
    evaluated = [m for m in store_models.values() if m.get("evaluated_at")]
    evaluated.sort(
        key=lambda m: (
            -(m.get("score") or 0),
            # 0 ms is a real median; only a missing one sorts last.
            1 << 30 if m.get("median_request_ms") is None else m["median_request_ms"],
            m.get("id") or "",
        )
    )
    return evaluated


def recommend(store, catalog):
    """The model we would pin: top scorer that is ready and tool-capable."""
    by_id = {row["id"]: row for row in catalog}
    for entry in _ranked(store.get("models", {})):
        row = by_id.get(entry["id"])
        ready = row["ready"] if row else bool(entry.get("ready"))
        tools = bool(entry.get("supports_tools"))
        if ready and tools and (entry.get("score") or 0) > 0:
            return entry, row
    return None, None


def _admin_put_map(cfg, token, patch):
    status, payload, err = _http_json(
        "PUT",
        cfg["base_url"] + "/api/settings/anthropic-map",
        body=patch,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    return status == 200 and isinstance(payload, dict), err


def pin_model(cfg, model_id, log=None):
    """Point anthropic-map's default/sonnet at ``model_id``.

    ``(pinned, reason)`` — never raises, never logs credentials."""
    token = _admin_login(cfg)
    if not token:
        return False, "no admin credentials on file to update the router"
    patch = {"default": model_id, "sonnet": model_id}
    ok, err = _admin_put_map(cfg, token, patch)
    if not ok:
        return False, err or "router refused the model map update"
    if log:
        log(f"Pinned {model_id} as the default free model.")
    return True, ""


def _pin_best(cfg, store, catalog, log=None):
    entry, row = recommend(store, catalog)
    if not entry:
        return {"model": None, "pinned": False,
                "reason": "no tool-capable model passed", "at": _now_iso()}
    model_id = entry["id"]
    pinned, reason = pin_model(cfg, model_id, log=log)
    return {"model": model_id, "pinned": pinned,
            "reason": reason or None, "at": _now_iso()}


# ---------------------------------------------------------------------------
# Job runner (POST /api/free-router/eval -> {job_id})
# ---------------------------------------------------------------------------

_JOBS_LOCK = threading.Lock()
_JOBS = {}
_JOB_ORDER = []
_MAX_JOBS = 8


def _job_log(job, line):
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    with _JOBS_LOCK:
        job["lines"].append(f"[{stamp}] {line}")
        if len(job["lines"]) > MAX_JOB_LINES:
            del job["lines"][: len(job["lines"]) - MAX_JOB_LINES]


def _job_update(job, **fields):
    with _JOBS_LOCK:
        job.update(fields)


def _new_job(kind):
    job_id = f"eval-{uuid.uuid4().hex[:12]}"
    job = {
        "id": job_id,
        "kind": kind,
        "status": "running",
        "step": "starting",
        "progress": 0.0,
        "lines": [],
        "error": None,
        "result": None,
        "started_at": _now_iso(),
        "ended_at": None,
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        _JOB_ORDER.append(job_id)
        while len(_JOB_ORDER) > _MAX_JOBS:
            _JOBS.pop(_JOB_ORDER.pop(0), None)
    return job


def job_status(job_id):
    """Mirror of the L02 job shape so shared pollers just work."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        return {
            "job_id": job["id"],
            "status": job["status"],
            "step": job["step"],
            "progress": job["progress"],
            "lines": list(job["lines"]),
            "error": job["error"],
            "result": job["result"],
            "started_at": job["started_at"],
            "ended_at": job["ended_at"],
        }


def _running_job():
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job["status"] == "running":
                return job["id"]
    return None


def _provider_score_hint():
    """L03's free_providers coding_score, when that lane is present."""
    try:
        from ccc_server import free_providers  # noqa: PLC0415 - optional lane
    except Exception:
        return {}
    for attr in ("providers", "list_providers", "catalog"):
        fn = getattr(free_providers, attr, None)
        try:
            rows = fn() if callable(fn) else fn
        except Exception:
            continue
        if isinstance(rows, list):
            return {
                str(r.get("platform")): r.get("coding_score")
                for r in rows if isinstance(r, dict)
            }
    if isinstance(getattr(free_providers, "PROVIDERS", None), list):
        return {
            str(r.get("platform")): r.get("coding_score")
            for r in free_providers.PROVIDERS if isinstance(r, dict)
        }
    return {}


def _pick_candidates(catalog, requested):
    if requested:
        wanted = {str(m).strip() for m in requested if str(m).strip()}
        by_id = {row["id"]: row for row in catalog}
        picked = [by_id[mid] for mid in wanted if mid in by_id]
        for mid in sorted(wanted - set(by_id)):
            picked.append({"id": mid, "name": mid, "platform": "",
                           "context": None, "available": False,
                           "execution_status": "", "ready": True,
                           "supports_tools": False})
        return picked
    pool = [row for row in catalog if row["ready"]]
    if not pool:
        # A catalog that reports nothing servable is a wall, not a race.
        return []
    hints = _provider_score_hint()

    def sort_key(row):
        hint = hints.get(row.get("platform") or "")
        return (
            not row["ready"],
            not row["supports_tools"],
            -(hint if isinstance(hint, (int, float)) else -1),
            -(row.get("context") or 0),
            row["id"],
        )

    ordered = sorted(pool, key=sort_key)
    cap = MAX_EVAL_MODELS
    try:
        cap = max(1, int(os.environ.get(ENV_MAX_MODELS) or MAX_EVAL_MODELS))
    except ValueError:
        pass
    return ordered[:cap]


def start_eval(model_ids=None):
    """``(payload, http_status)`` for POST /api/free-router/eval."""
    cfg = router_config(probe=True, force=True)
    if not cfg:
        return ({"ok": False,
                 "error": "The free router is not set up on this machine yet."},
                503)
    running = _running_job()
    if running:
        return ({"ok": False, "error": "A benchmark is already running.",
                 "job_id": running}, 409)
    job = _new_job("eval")
    thread = threading.Thread(
        target=_run_eval_job, args=(job, cfg, model_ids),
        name="free-eval", daemon=True,
    )
    thread.start()
    return {"job_id": job["id"]}, 200


def _run_eval_job(job, cfg, requested):
    log = lambda line: _job_log(job, line)  # noqa: E731
    try:
        log("Warming up: reading the router's model catalog.")
        catalog, error = fetch_catalog(cfg)  # fresh read — a race is heavyweight anyway
        if not catalog:
            _job_update(job, status="error", step="catalog",
                        error=error or "The router returned no models.",
                        ended_at=_now_iso())
            return
        candidates = _pick_candidates(catalog, requested)
        if not candidates:
            if requested:
                msg = "None of the requested models exist in the catalog."
            else:
                msg = ("No model can serve a request right now. "
                       "Add a provider key to the router first.")
            _job_update(job, status="error", step="catalog",
                        error=msg, ended_at=_now_iso())
            return
        skipped = len(catalog) - len(candidates) if not requested else 0
        log(f"Racing {len(candidates)} model(s) through 5 tiny coding tasks."
            + (f" ({skipped} more in the catalog were skipped.)" if skipped > 0 else ""))
        results = []
        total = len(candidates)
        for index, row in enumerate(candidates, 1):
            _job_update(job, step=f"{row['id']} ({index}/{total})",
                        progress=(index - 1) / total)
            log(f"Racing {row['id']} ({index}/{total})")
            try:
                result = run_model_eval(cfg, row, log=log)
            except Exception as e:
                result = {"id": row["id"], "name": row.get("name") or row["id"],
                          "platform": row.get("platform") or "",
                          "context": row.get("context"),
                          "supports_tools": bool(row.get("supports_tools")),
                          "tools_observed": False, "ready": bool(row.get("ready")),
                          "per_task": [], "evaluated_at": _now_iso(),
                          "score": 0.0, "passed": 0, "tasks": 0,
                          "pass_rate": 0.0, "median_request_ms": None,
                          "error": str(e)[:200]}
                log(f"  FAIL {row['id']} crashed out: {str(e)[:120]}")
            results.append(result)
            log(f"  Score for {row['id']}: {result.get('score', 0)}"
                f" ({result.get('passed', 0)}/{result.get('tasks', 5)} tasks)")
        store = _load_store()
        _merge_results(store, results, cfg, None)
        _job_update(job, step="picking the winner", progress=0.98)
        pinned_info = _pin_best(cfg, store, catalog, log=log)
        store = _load_store()
        store["best"] = pinned_info
        _save_store(store)
        best_model = pinned_info.get("model")
        if best_model and pinned_info.get("pinned"):
            log(f"Winner: {best_model}. It is now the default free model.")
        elif best_model:
            log(f"Winner: {best_model}. (Could not pin it: "
                f"{pinned_info.get('reason') or 'unknown'}.)")
        else:
            log("No model passed enough tasks to earn the crown.")
        _job_update(job, status="done", step="done", progress=1.0,
                    result={"evaluated": len(results), "best": pinned_info},
                    ended_at=_now_iso())
    except Exception as e:
        log(f"The race crashed: {str(e)[:160]}")
        _job_update(job, status="error", step=job.get("step") or "error",
                    error=str(e)[:300], ended_at=_now_iso())


def prefer_model(model_id):
    """``(payload, status)`` for POST /api/free-router/prefer."""
    model_id = str(model_id or "").strip()
    if not model_id:
        return {"ok": False, "error": "missing model"}, 400
    cfg = router_config(probe=True, force=True)
    if not cfg:
        return ({"ok": False, "error": "The free router is not reachable."},
                503)
    catalog, _err = catalog_cached(cfg)
    if catalog and model_id not in {row["id"] for row in catalog}:
        return ({"ok": False, "error": f"{model_id} is not in the router catalog."},
                404)
    pinned, reason = pin_model(cfg, model_id)
    if not pinned:
        return {"ok": False, "error": reason}, 502
    store = _load_store()
    store["best"] = {"model": model_id, "pinned": True, "at": _now_iso()}
    _save_store(store)
    return {"ok": True, "model": model_id}, 200


# ---------------------------------------------------------------------------
# GET /api/free-router/models payload (contract: bare list)
# ---------------------------------------------------------------------------

def models_payload():
    cfg = router_config(probe=False)
    catalog = []
    if cfg:
        catalog, _catalog_error = catalog_cached(cfg)
    store = _load_store()
    evaluated = _ranked(store.get("models", {}))
    rank_by_id = {entry["id"]: index + 1 for index, entry in enumerate(evaluated)}
    evals = store.get("models", {})
    best_info = store.get("best") or {}
    best = best_info.get("model")
    best_pinned = bool(best_info.get("pinned"))

    rows = []
    seen = set()
    for row in catalog:
        mid = row["id"]
        seen.add(mid)
        ev = evals.get(mid) or {}
        rows.append(_row(row, ev, rank_by_id.get(mid), best, best_pinned))
    for mid, ev in evals.items():
        if mid in seen:
            continue
        ghost = {"id": mid, "name": ev.get("name") or mid,
                 "platform": ev.get("platform") or "",
                 "context": ev.get("context"), "available": False,
                 "execution_status": "", "ready": False,
                 "supports_tools": bool(ev.get("supports_tools"))}
        rows.append(_row(ghost, ev, rank_by_id.get(mid), best, best_pinned,
                     in_catalog=False))

    def sort_key(row):
        rank = row["rank"] or 10_000
        return (
            rank,
            not row["ready"],
            not row["supports_tools"],
            -(row.get("context") or 0),
            row["id"],
        )

    rows.sort(key=sort_key)
    return rows


def _row(catalog_row, ev, rank, best, best_pinned, in_catalog=True):
    evaluated = bool(ev.get("evaluated_at"))
    is_best = bool(best and catalog_row["id"] == best)
    return {
        "id": catalog_row["id"],
        "name": catalog_row.get("name") or catalog_row["id"],
        "platform": catalog_row.get("platform") or ev.get("platform") or "",
        "supports_tools": bool(
            catalog_row.get("supports_tools") or ev.get("supports_tools")
        ),
        "context": catalog_row.get("context") or ev.get("context"),
        "rank": rank,
        "score": ev.get("score") if evaluated else None,
        "ready": bool(catalog_row.get("ready")),
        "available": bool(catalog_row.get("available")),
        "execution_status": catalog_row.get("execution_status") or "",
        "evaluated": evaluated,
        "passed": ev.get("passed") if evaluated else None,
        "tasks": ev.get("tasks") if evaluated else None,
        "latency_ms": sum(
            t.get("latency_ms") or 0 for t in ev.get("per_task") or []
        ) or None,
        "median_request_ms": ev.get("median_request_ms"),
        "evaluated_at": ev.get("evaluated_at"),
        "best": is_best,
        "pinned": is_best and best_pinned,
        "in_catalog": in_catalog,
        "per_task": [
            {"task": t.get("task"), "passed": t.get("passed"),
             "latency_ms": t.get("latency_ms"), "tool_calls": t.get("tool_calls")}
            for t in (ev.get("per_task") or [])
        ],
    }


def eval_info():
    """Small status blob for the leaderboard UI (additive, not contract)."""
    cfg = router_config(probe=True)
    catalog_error = None
    if cfg:
        _rows, catalog_error = catalog_cached(cfg)
    store = _load_store()
    running = _running_job()
    return {
        "router_configured": bool(cfg),
        "router_url": (cfg or {}).get("base_url") or None,
        "router_source": (cfg or {}).get("source") or None,
        "has_unified_key": bool((cfg or {}).get("unified_key")),
        "has_admin": bool((cfg or {}).get("admin_password")),
        "catalog_error": catalog_error,
        "running_job": running,
        "task_count": len(eval_tasks()),
        "tasks": [{"id": t["id"], "title": t["title"]} for t in eval_tasks()],
        "last_eval_at": store.get("updated_at"),
        "best": store.get("best"),
    }
