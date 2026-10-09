# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Free-model leaderboard suite: 15 fixed coding tasks x $0 models.

The in-app benchmark (``free_eval``) races five tasks through the local free
router to pick a default model. This module is the offline leaderboard: the
same five tasks plus ten more, run against every $0 model reachable from
three backends, with token counts recorded per task.

Backends:
  router      CCC's local free router (Anthropic wire format), if configured.
  openrouter  OpenRouter models whose live price is 0 for prompt, completion
              and request (``:free`` ids). Requests also carry
              ``provider.max_price = 0`` so a price change cannot bill.
  github      GitHub Models free tier, authenticated with the ``gh`` token.

Models that would bill are never called: paid presets (GLM, Kimi, DeepSeek,
Qwen, MiniMax) and BYOK keys are not backends here.

Every checker is deterministic: it runs fixed code against the files the
model left in a scratch directory. ``scripts/run-leaderboard-eval.py`` is the
CLI. Stdlib-only.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ccc_server import free_eval
from ccc_server.free_eval import (
    EVAL_TOOLS,
    MAX_TOOL_CALLS_PER_TURN,
    MAX_TOOL_TURNS,
    SYSTEM_PROMPT,
    _exec_tool,
    _http_json,
    _list_workspace,
    _run_check_command,
    _write,
)

SUITE_VERSION = 1
TASK_WALL_BUDGET_S = 240
MAX_TOKENS = 1024
RATE_LIMIT_RETRIES = 3

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
GITHUB_MODELS_BASE = "https://models.github.ai/inference"
GITHUB_DEFAULT_MODELS = (
    "openai/gpt-4.1-mini",
    "openai/gpt-4.1-nano",
    "openai/gpt-4o-mini",
    "mistral-ai/mistral-small-2503",
    "meta/llama-4-scout-17b-16e-instruct",
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Tasks: free_eval's five plus ten more
# ---------------------------------------------------------------------------

def _python_check(script):
    def check(wd):
        return _run_check_command([sys.executable, script], wd)
    return check


def _file_equals(name, expected):
    def check(wd):
        target = Path(wd) / name
        if not target.is_file():
            return False, f"{name} does not exist yet"
        got = target.read_text(encoding="utf-8", errors="replace").strip()
        if got == expected:
            return True, "ok"
        return False, f"{name} contains {got[:200]!r}, expected {expected!r}"
    return check


def _task(task_id, title, files, prompt, check):
    def setup(wd):
        for rel, content in files.items():
            _write(wd, rel, content)
    return {"id": task_id, "title": title, "setup": setup, "prompt": prompt,
            "check": check, "needs_tools": True}


def _task_fizzbuzz():
    return _task(
        "fizzbuzz", "Implement a function from a spec",
        {
            "fb.py": "def fizzbuzz(n):\n    raise NotImplementedError\n",
            "check_fb.py": (
                "from fb import fizzbuzz\n"
                "assert fizzbuzz(1) == '1'\n"
                "assert fizzbuzz(3) == 'Fizz'\n"
                "assert fizzbuzz(10) == 'Buzz'\n"
                "assert fizzbuzz(30) == 'FizzBuzz'\n"
                "assert fizzbuzz(7) == '7'\n"
                "print('ok')\n"
            ),
        },
        "Implement fizzbuzz(n) in fb.py: return 'FizzBuzz' when n is divisible "
        "by 3 and 5, 'Fizz' when divisible by 3, 'Buzz' when divisible by 5, "
        "otherwise str(n). `python3 check_fb.py` checks it. Then call run_checks.",
        _python_check("check_fb.py"),
    )


def _task_off_by_one():
    return _task(
        "off_by_one", "Fix an off-by-one bug",
        {
            "series.py": (
                "def sum_to(n):\n"
                "    \"\"\"Return 1 + 2 + ... + n.\"\"\"\n"
                "    total = 0\n"
                "    for i in range(n):\n"
                "        total += i\n"
                "    return total\n"
            ),
            "check_series.py": (
                "from series import sum_to\n"
                "assert sum_to(1) == 1, sum_to(1)\n"
                "assert sum_to(4) == 10, sum_to(4)\n"
                "assert sum_to(100) == 5050, sum_to(100)\n"
                "print('ok')\n"
            ),
        },
        "sum_to(n) in series.py should return 1 + 2 + ... + n but returns the "
        "wrong value. Fix it so `python3 check_series.py` passes, then call "
        "run_checks.",
        _python_check("check_series.py"),
    )


def _rename_check(wd):
    for rel in ("shapes.py", "main.py"):
        path = Path(wd) / rel
        if not path.is_file():
            return False, f"{rel} is missing"
        if "area_of" in path.read_text(encoding="utf-8", errors="replace"):
            return False, f"{rel} still uses area_of"
    return _run_check_command([sys.executable, "main.py"], wd)


def _task_rename():
    return _task(
        "rename_symbol", "Rename a function across files",
        {
            "shapes.py": "def area_of(w, h):\n    return w * h\n",
            "main.py": (
                "from shapes import rectangle_area\n\n"
                "assert rectangle_area(3, 4) == 12\n"
                "print('ok')\n"
            ),
        },
        "main.py imports rectangle_area from shapes.py, but shapes.py still "
        "calls the function area_of. Rename area_of to rectangle_area in "
        "shapes.py (no area_of left anywhere) so `python3 main.py` runs, then "
        "call run_checks.",
        _rename_check,
    )


def _json_config_check(wd):
    path = Path(wd) / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, f"config.json is not valid JSON: {e}"
    expected = {"name": "demo", "debug": True, "port": 8080, "tags": ["a", "b"]}
    if data == expected:
        return True, "ok"
    return False, f"config.json is {json.dumps(data)[:200]}, expected {json.dumps(expected)}"


def _task_json_edit():
    return _task(
        "json_edit", "Edit a JSON config",
        {"config.json": '{\n  "name": "demo",\n  "debug": false,\n  "tags": ["a", "b"]\n}\n'},
        "Edit config.json: set debug to true and add a numeric key port with "
        "the value 8080. Keep the other keys unchanged and the file valid "
        "JSON. Then call run_checks.",
        _json_config_check,
    )


def _task_count_errors():
    lines = []
    for i in range(1, 31):
        level = "ERROR" if i % 4 == 0 else ("WARN" if i % 5 == 0 else "INFO")
        lines.append(f"2026-01-01 10:{i:02d}:00 {level} worker {i} step")
    lines.append("2026-01-01 11:00:00 INFO summary: no ERROR budget left")
    return _task(
        "count_errors", "Count matching lines in a log",
        {"app.log": "\n".join(lines) + "\n"},
        "app.log has one entry per line, with the level as the third "
        "space-separated field. Count the entries whose level is exactly "
        "ERROR and write just that number to count.txt. Then call run_checks.",
        _file_equals("count.txt", "7"),
    )


def _task_palindrome():
    return _task(
        "palindrome", "Write code to pass given tests",
        {
            "test_pal.py": (
                "import unittest\n\nfrom pal import is_palindrome\n\n\n"
                "class PalTests(unittest.TestCase):\n"
                "    def test_simple(self):\n"
                "        self.assertTrue(is_palindrome('racecar'))\n"
                "        self.assertFalse(is_palindrome('hello'))\n\n"
                "    def test_ignores_case_and_punctuation(self):\n"
                "        self.assertTrue(is_palindrome('A man, a plan, a canal: Panama!'))\n"
                "        self.assertTrue(is_palindrome('No lemon, no melon'))\n\n"
                "    def test_empty(self):\n"
                "        self.assertTrue(is_palindrome(''))\n\n\n"
                "if __name__ == '__main__':\n    unittest.main()\n"
            ),
        },
        "Create pal.py with is_palindrome(text) so `python3 -m unittest "
        "test_pal` passes. Read test_pal.py for the exact rules. Then call "
        "run_checks.",
        lambda wd: _run_check_command([sys.executable, "-m", "unittest", "test_pal"], wd),
    )


def _task_parse_int():
    return _task(
        "handle_bad_input", "Handle bad input without crashing",
        {
            "parse.py": "def parse_int(text):\n    return int(text)\n",
            "check_parse.py": (
                "from parse import parse_int\n"
                "assert parse_int('42') == 42\n"
                "assert parse_int('  -7 ') == -7\n"
                "assert parse_int('abc') is None\n"
                "assert parse_int('') is None\n"
                "assert parse_int(None) is None\n"
                "print('ok')\n"
            ),
        },
        "parse_int(text) in parse.py crashes on bad input. Change it to return "
        "the integer for valid input (surrounding spaces allowed) and None for "
        "anything invalid, including None. `python3 check_parse.py` checks "
        "it. Then call run_checks.",
        _python_check("check_parse.py"),
    )


def _task_slugify():
    return _task(
        "new_module", "Create a module from a spec",
        {
            "check_slug.py": (
                "from slug import slugify\n"
                "assert slugify('Hello World') == 'hello-world'\n"
                "assert slugify('  Free   Models!  ') == 'free-models'\n"
                "assert slugify('C3PO & R2D2') == 'c3po-r2d2'\n"
                "assert slugify('---') == ''\n"
                "print('ok')\n"
            ),
        },
        "Create slug.py with slugify(text): lowercase, replace every run of "
        "characters that are not a-z or 0-9 with a single hyphen, and strip "
        "hyphens from both ends. `python3 check_slug.py` checks it. Then call "
        "run_checks.",
        _python_check("check_slug.py"),
    )


def _task_csv_total():
    rows = [
        ("east", 120), ("west", 75), ("west", 230), ("north", 40),
        ("east", 15), ("west", 5), ("south", 99), ("west", 60),
    ]
    csv = "region,amount\n" + "".join(f"{r},{a}\n" for r, a in rows)
    return _task(
        "csv_total", "Aggregate a CSV column",
        {"sales.csv": csv},
        "sales.csv has columns region and amount. Add up the amount for rows "
        "whose region is west and write just the total to west_total.txt. "
        "Then call run_checks.",
        _file_equals("west_total.txt", "370"),
    )


def _task_dedupe_sort():
    names = ["maya", "Liam", "zoe", "ava", "liam", "Noah", "ava", "Zoe", "eli"]
    return _task(
        "dedupe_sort", "Deduplicate and sort a list",
        {"names.txt": "\n".join(names) + "\n"},
        "names.txt has one name per line with duplicates that differ only in "
        "case. Write unique.txt with each name once, lowercased, sorted "
        "alphabetically, one per line. Then call run_checks.",
        _file_equals("unique.txt", "ava\neli\nliam\nmaya\nnoah\nzoe"),
    )


def _fresh_bytecode(check):
    """Drop stale .pyc first: a same-size edit within one second of the last
    check would otherwise rerun the old code and fail a correct fix."""
    def wrapped(wd):
        for cache in Path(wd).rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
        return check(wd)
    return wrapped


def suite_tasks():
    """Fresh task dicts for one sandbox each (closures are single-use)."""
    tasks = free_eval.eval_tasks() + [
        _task_fizzbuzz(),
        _task_off_by_one(),
        _task_rename(),
        _task_json_edit(),
        _task_count_errors(),
        _task_palindrome(),
        _task_parse_int(),
        _task_slugify(),
        _task_csv_total(),
        _task_dedupe_sort(),
    ]
    for task in tasks:
        task["check"] = _fresh_bytecode(task["check"])
    return tasks


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class RateLimiter:
    """Minimum spacing between requests on one backend (thread-safe)."""

    def __init__(self, min_interval_s):
        self.min_interval_s = float(min_interval_s)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.min_interval_s
        if delay:
            time.sleep(delay)


def _openai_tools():
    return [
        {"type": "function", "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        }}
        for tool in EVAL_TOOLS
    ]


def is_zero_price(pricing):
    """True only when every listed OpenRouter price is exactly zero."""
    if not isinstance(pricing, dict) or not pricing:
        return False
    for key in ("prompt", "completion"):
        if key not in pricing:
            return False
    try:
        return all(float(value or 0) == 0 for value in pricing.values())
    except (TypeError, ValueError):
        return False


def openrouter_free_models(catalog):
    """``:free`` tool-capable rows from OpenRouter's /models with zero price."""
    picked = []
    for row in catalog or []:
        mid = str(row.get("id") or "")
        params = row.get("supported_parameters") or []
        if mid.endswith(":free") and "tools" in params and is_zero_price(row.get("pricing")):
            picked.append(mid)
    return sorted(picked)


def make_backends(names, github_models=GITHUB_DEFAULT_MODELS, log=print):
    """Resolve each requested backend into ``{name, models, chat, cost_probe}``."""
    backends = []
    if "router" in names:
        cfg = free_eval.router_config(probe=True, force=True)
        if cfg:
            rows, err = free_eval.fetch_catalog(cfg)
            models = [r["id"] for r in rows if r.get("supports_tools")]
            if models:
                limiter = RateLimiter(1.0)
                backends.append({
                    "name": "router", "models": models,
                    "chat": lambda model, msgs, cfg=cfg, lim=limiter: _anthropic_chat(cfg, model, msgs, lim),
                    "wire": "anthropic", "cost_probe": None,
                })
            else:
                log(f"router: no tool-capable models ({err or 'empty catalog'}); skipped")
        else:
            log("router: CCC free router not configured on this machine; skipped")
    if "openrouter" in names:
        key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        status, payload, err = _http_json("GET", OPENROUTER_BASE + "/models", timeout=30)
        catalog = payload.get("data") if status == 200 and isinstance(payload, dict) else None
        if not key:
            log("openrouter: OPENROUTER_API_KEY not set; skipped")
        elif catalog is None:
            log(f"openrouter: catalog unavailable ({err}); skipped")
        else:
            limiter = RateLimiter(3.5)  # free tier: 20 requests/minute per account
            headers = {"Authorization": f"Bearer {key}"}
            backends.append({
                "name": "openrouter", "models": openrouter_free_models(catalog),
                "chat": lambda model, msgs, h=headers, lim=limiter: _openai_chat(
                    OPENROUTER_BASE, h, model, msgs, lim,
                    extra={"provider": {"max_price": {"prompt": 0, "completion": 0, "request": 0}}}),
                "wire": "openai",
                "cost_probe": lambda h=headers: _openrouter_usage(h),
            })
    if "github" in names:
        token = (os.environ.get("GITHUB_TOKEN") or "").strip() or _gh_token()
        if not token:
            log("github: no GitHub token (set GITHUB_TOKEN or log in with gh); skipped")
        else:
            limiter = RateLimiter(4.5)  # free tier: 15 requests/minute
            headers = {"Authorization": f"Bearer {token}"}
            backends.append({
                "name": "github", "models": list(github_models),
                "chat": lambda model, msgs, h=headers, lim=limiter: _openai_chat(
                    GITHUB_MODELS_BASE, h, model, msgs, lim),
                "wire": "openai", "cost_probe": None,
            })
    return backends


def _gh_token():
    try:
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _openrouter_usage(headers):
    status, payload, _err = _http_json("GET", OPENROUTER_BASE + "/key", headers=headers, timeout=20)
    if status == 200 and isinstance(payload, dict):
        usage = (payload.get("data") or {}).get("usage")
        if isinstance(usage, (int, float)):
            return float(usage)
    return None


def _with_retries(send, limiter):
    """POST with backoff on 429/5xx. ``(status, payload, err, ms)``."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        limiter.wait()
        started = time.monotonic()
        status, payload, err = send()
        ms = int((time.monotonic() - started) * 1000)
        if status not in (429, 502, 503) or attempt == RATE_LIMIT_RETRIES:
            return status, payload, err, ms
        time.sleep(min(60, 10 * (attempt + 1)))
    return status, payload, err, ms


def _anthropic_chat(cfg, model, messages, limiter):
    body = {"model": model, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT,
            "messages": messages, "tools": EVAL_TOOLS,
            "tool_choice": {"type": "auto"}, "stream": False}
    return _with_retries(lambda: _http_json(
        "POST", cfg["base_url"] + "/v1/messages", body=body,
        headers=free_eval._inference_headers(cfg), timeout=free_eval.REQUEST_TIMEOUT_S,
    ), limiter)


def _openai_chat(base, headers, model, messages, limiter, extra=None):
    body = {"model": model, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "tools": _openai_tools(), "tool_choice": "auto", "stream": False}
    body.update(extra or {})
    return _with_retries(lambda: _http_json(
        "POST", base + "/chat/completions", body=body, headers=headers,
        timeout=free_eval.REQUEST_TIMEOUT_S,
    ), limiter)


# ---------------------------------------------------------------------------
# Agent loop (both wire formats)
# ---------------------------------------------------------------------------

def _usage_tokens(payload, wire):
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    if wire == "anthropic":
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _tool_calls(payload, wire):
    """Normalize to ``(assistant_message, [(call_id, name, input_dict)])``."""
    if wire == "anthropic":
        content = payload.get("content") or []
        calls = [(str(b.get("id") or ""), b.get("name"), b.get("input"))
                 for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        return {"role": "assistant", "content": content}, calls
    choices = payload.get("choices") or []
    message = (choices[0].get("message") if choices and isinstance(choices[0], dict) else None) or {}
    calls = []
    for raw in message.get("tool_calls") or []:
        fn = raw.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        calls.append((str(raw.get("id") or ""), fn.get("name"), args))
    assistant = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        assistant["tool_calls"] = message["tool_calls"]
    return assistant, calls


def run_task(chat, wire, model, task, workdir, deadline=None):
    """Drive one task; returns pass/fail, latency and token counts."""
    task["setup"](workdir)
    listing = ", ".join(_list_workspace(workdir)) or "(empty)"
    messages = [{"role": "user", "content": task["prompt"] + f"\n\nWorkspace files: {listing}"}]
    started = time.monotonic()
    request_ms, input_tokens, output_tokens = [], 0, 0
    tool_calls = 0
    error = None
    for _turn in range(MAX_TOOL_TURNS):
        if deadline and time.monotonic() > deadline:
            error = "time budget reached"
            break
        status, payload, err, ms = chat(model, messages)
        request_ms.append(ms)
        if status != 200 or not isinstance(payload, dict):
            error = (err or f"HTTP {status}")[:200]
            if status == 429:
                error = "rate limited: " + error
            break
        tin, tout = _usage_tokens(payload, wire)
        input_tokens += tin
        output_tokens += tout
        assistant, calls = _tool_calls(payload, wire)
        messages.append(assistant)
        calls = calls[:MAX_TOOL_CALLS_PER_TURN] if wire == "anthropic" else calls
        if not calls:
            break
        outputs = []
        for call_id, name, tool_input in calls:
            tool_calls += 1
            outputs.append((call_id or f"call_{tool_calls}",
                            _exec_tool(task, workdir, name, tool_input)))
        if wire == "anthropic":
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid, "content": out}
                for cid, out in outputs]})
        else:
            messages.extend({"role": "tool", "tool_call_id": cid, "content": out}
                            for cid, out in outputs)
        if any(out.startswith("PASS") for _cid, out in outputs):
            break
    else:
        error = error or "too many tool turns"
    ok, detail = task["check"](workdir)
    return {
        "task": task["id"],
        "passed": bool(ok),
        "detail": "ok" if ok else str(detail)[:200],
        "error": error,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "requests": len(request_ms),
        "median_request_ms": int(statistics.median(request_ms)) if request_ms else None,
        "tool_calls": tool_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def run_model(backend, model, log=print):
    per_task = []
    tasks = suite_tasks()
    deadline = time.monotonic() + len(tasks) * TASK_WALL_BUDGET_S
    for task in tasks:
        with tempfile.TemporaryDirectory(prefix="ccc-leaderboard-") as wd:
            try:
                result = run_task(backend["chat"], backend["wire"], model, task, wd, deadline)
            except Exception as e:  # one task never kills the run
                result = {"task": task["id"], "passed": False, "detail": str(e)[:200],
                          "error": str(e)[:200], "latency_ms": 0, "requests": 0,
                          "median_request_ms": None, "tool_calls": 0,
                          "input_tokens": 0, "output_tokens": 0}
        per_task.append(result)
        log(f"  {backend['name']} {model} {task['id']}: "
            f"{'PASS' if result['passed'] else 'FAIL'} {result['latency_ms'] / 1000:.1f}s"
            + (f" ({result['error']})" if result["error"] else ""))
        if len(per_task) == 2 and all(r["error"] and r["requests"] <= 1 for r in per_task):
            # The model is unreachable (404, auth, no tool support): stop early.
            for rest in tasks[2:]:
                per_task.append({"task": rest["id"], "passed": False, "detail": "skipped",
                                 "error": "skipped after two request failures",
                                 "latency_ms": 0, "requests": 0, "median_request_ms": None,
                                 "tool_calls": 0, "input_tokens": 0, "output_tokens": 0})
            break
    return summarize(backend["name"], model, per_task)


LOOP_ERRORS = ("too many tool turns", "time budget reached")


def is_infra_error(result):
    """A failed request (rate limit, refusal, unreachable), not a model mistake."""
    return bool(result.get("error")) and not result.get("passed") \
        and result["error"] not in LOOP_ERRORS


def summarize(backend_name, model, per_task):
    passed = sum(1 for r in per_task if r["passed"])
    medians = [r["median_request_ms"] for r in per_task if r["median_request_ms"] is not None]
    attempted = [r for r in per_task if r["requests"]]
    infra_errors = sum(1 for r in per_task if is_infra_error(r))
    return {
        "backend": backend_name,
        "model": model,
        "passed": passed,
        "tasks": len(per_task),
        "pass_rate": round(passed / max(1, len(per_task)), 3),
        "median_request_ms": int(statistics.median(medians)) if medians else None,
        "median_task_s": round(statistics.median([r["latency_ms"] for r in attempted]) / 1000, 1)
        if attempted else None,
        "input_tokens": sum(r["input_tokens"] for r in per_task),
        "output_tokens": sum(r["output_tokens"] for r in per_task),
        "infra_errors": infra_errors,
        "per_task": per_task,
    }


def rank(models):
    """Pass rate first, then faster median request, then fewer tokens."""
    return sorted(models, key=lambda m: (
        -m["pass_rate"],
        m["median_request_ms"] if m["median_request_ms"] is not None else 10**9,
        m["input_tokens"] + m["output_tokens"],
        m["model"],
    ))


def render_markdown(results):
    lines = [
        "# Free-model leaderboard",
        "",
        f"Run {results['run_at']} · suite v{results['suite_version']} · "
        f"{len(results['tasks'])} tasks · cost ${results['cost_usd']:.2f}",
        "",
        "Every model here cost $0 to call. Pass rate counts tasks whose fixed "
        "checker passed after the model's last edit. Latency is the median "
        "time per API request. Tokens are totals across all tasks, as "
        "reported by the provider. Infra errors (rate limits, unreachable "
        "models) count as failures and are listed so they are not mistaken "
        "for model mistakes.",
        "",
        "| # | Model | Backend | Pass rate | Passed | Median request | Median task | Input tokens | Output tokens | Infra errors |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    measured = [m for m in results["models"] if m["infra_errors"] < m["tasks"]]
    unmeasured = [m for m in results["models"] if m["infra_errors"] >= m["tasks"]]
    for i, m in enumerate(rank(measured), 1):
        req = f"{m['median_request_ms'] / 1000:.1f}s" if m["median_request_ms"] is not None else "n/a"
        task = f"{m['median_task_s']:.1f}s" if m["median_task_s"] is not None else "n/a"
        lines.append(
            f"| {i} | `{m['model']}` | {m['backend']} | {m['pass_rate'] * 100:.0f}% | "
            f"{m['passed']}/{m['tasks']} | {req} | {task} | {m['input_tokens']:,} | "
            f"{m['output_tokens']:,} | {m['infra_errors']} |"
        )
    if unmeasured:
        lines += ["", "## Not measured", "",
                  "Every request to these models failed, so they have no score:", ""]
        for m in sorted(unmeasured, key=lambda m: m["model"]):
            first = next((r["error"] for r in m["per_task"] if r.get("error")), "")
            lines.append(f"- `{m['model']}` ({m['backend']}): {first[:160]}")
    lines += ["", "## Tasks", ""]
    lines += [f"- `{t['id']}`: {t['title']}" for t in results["tasks"]]
    if results.get("skipped"):
        lines += ["", "## Skipped", ""]
        lines += [f"- {s}" for s in results["skipped"]]
    lines.append("")
    return "\n".join(lines)
