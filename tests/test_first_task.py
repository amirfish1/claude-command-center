"""First magic task: playground scaffold, headless-run jobs, verification.

Covers ccc_server/first_task.py end to end with a fake `claude` CLI (selected
via CCC_CLAUDE_BIN) that speaks just enough one-shot stream-json to exercise
the runner: an init event, a tool_use line, a real file edit in the
playground, and a result event carrying token usage. No real agent is
launched and nothing touches the real ~/CCC-Playground or
~/.claude/command-center state.
"""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ccc_server import first_task


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys, time

argv = sys.argv[1:]
prompt = ""
if "-p" in argv:
    try:
        prompt = argv[argv.index("-p") + 1]
    except IndexError:
        prompt = ""

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()

emit({"type": "system", "subtype": "init", "session_id": "fake-ft-1",
      "model": "fake-free-model", "cwd": os.getcwd()})

sleep_s = float(os.environ.get("FAKE_CLAUDE_SLEEP") or 0)
if sleep_s:
    time.sleep(sleep_s)

emit({"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "tool_use", "name": "Edit",
     "input": {"file_path": os.path.join(os.getcwd(), "index.html")}}]}})

# Do the tiny real edit the task asked for, so post-run checks can pass.
idx_path = os.path.join(os.getcwd(), "index.html")
try:
    html = open(idx_path).read()
except OSError:
    html = ""
if "three languages" in prompt:
    html = html.replace("Hello, world!", "Hello, world! Hola Bonjour")
    open(idx_path, "w").write(html)
elif "test_page.py" in prompt:
    html = html.replace('<div class="card">', '<main>\\n<div class="card">')
    html = html.replace("</div>\\n<script", "</div>\\n</main>\\n<script")
    open(idx_path, "w").write(html)
elif "dark mode" in prompt:
    css_path = os.path.join(os.getcwd(), "style.css")
    open(css_path, "a").write("\\nbody.dark { background: #111; }\\n")

emit({"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "text", "text": "DONE: updated the page"}]}})
emit({"type": "result", "subtype": "success", "session_id": "fake-ft-1",
      "result": "DONE: updated the page", "is_error": False,
      "duration_ms": 1234, "num_turns": 2, "total_cost_usd": 0.0,
      "usage": {"input_tokens": 6, "output_tokens": 30},
      "modelUsage": {"fake-free-model": {
          "inputTokens": 2000, "outputTokens": 100,
          "cacheCreationInputTokens": 500,
          "cacheReadInputTokens": 10000}}})
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Redirect the playground + state file + claude bin into tmp."""
    pg = tmp_path / "CCC-Playground"
    state = tmp_path / "first-task.json"
    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("CCC_PLAYGROUND_DIR", str(pg))
    monkeypatch.setenv("CCC_FIRST_TASK_STATE", str(state))
    monkeypatch.setenv("CCC_CLAUDE_BIN", str(fake))
    # Isolate from a real free router on this machine (L01 may be installed).
    monkeypatch.setattr(first_task, "_free_router_env", lambda: {})
    first_task._free_ready_cache.update({"t": 0.0, "ready": False, "base_url": None})
    first_task._JOBS.clear()
    yield {"playground": pg, "state": state, "fake": fake}
    first_task._JOBS.clear()


def _wait_job(job_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = first_task.get_job(job_id)
        if job and job["status"] != "running":
            return job
        time.sleep(0.05)
    return first_task.get_job(job_id)


class TestPlayground:
    def test_creates_files_and_git(self, env):
        res = first_task.ensure_playground()
        assert res["ok"] is True
        pg = env["playground"]
        for name in ("index.html", "style.css", "script.js", "test_page.py", "README.md"):
            assert (pg / name).is_file(), name
        if (pg / ".git").is_dir():
            head = subprocess.run(
                ["git", "log", "--oneline"], cwd=str(pg),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            assert "First playground" in head.stdout

    def test_idempotent_keeps_user_edits(self, env):
        first_task.ensure_playground()
        idx = env["playground"] / "index.html"
        idx.write_text("<html>user edited</html>")
        res = first_task.ensure_playground()
        assert res["ok"] is True
        assert idx.read_text() == "<html>user edited</html>"
        assert res["files_written"] == []

    def test_shipped_test_fails_on_main_landmark(self, env):
        """The 'fix the failing test' task premise must actually fail."""
        first_task.ensure_playground()
        proc = subprocess.run(
            [sys.executable, "test_page.py"], cwd=str(env["playground"]),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
        assert proc.returncode != 0
        assert "<main>" in proc.stdout


class TestRegistry:
    def test_three_tasks_unique_ids(self):
        assert len(first_task.TASKS) == 3
        ids = [t["id"] for t in first_task.TASKS]
        assert len(set(ids)) == 3
        for t in first_task.TASKS:
            for key in ("title", "blurb", "prompt", "est_seconds", "open_file", "checks"):
                assert t.get(key), (t["id"], key)
            assert "DONE:" in t["prompt"]

    def test_tasks_public_has_no_prompts(self, env):
        public = first_task.tasks_public()
        assert {t["id"] for t in public} == {"hello-3-langs", "fix-failing-test", "dark-mode"}
        for t in public:
            assert "prompt" not in t
            assert t["done"] is False


class TestJobLifecycle:
    def test_hello_task_runs_and_verifies(self, env):
        job, err = first_task.start_task("hello-3-langs")
        assert err is None
        assert job["status"] == "running"
        assert job["runtime"] == "standard"  # no free router in this checkout
        done = _wait_job(job["job_id"])
        assert done["status"] == "done"
        assert done["session_id"] == "fake-ft-1"
        assert done["model"] == "fake-free-model"
        kinds = {l["kind"] for l in done["lines"]}
        assert "tool" in kinds and "info" in kinds
        # The assistant DONE line must not be duplicated by the result event.
        assert [l["text"] for l in done["lines"]].count("DONE: updated the page") == 1
        usage = done["usage"]
        # modelUsage merges over flat usage: 2000 in * $3/M + 100 out * $15/M
        # + 500 cw * $3.75/M + 10000 cr * $0.30/M = $0.0124
        assert usage["input_tokens"] == 2000
        assert usage["api_value_usd"] == pytest.approx(0.0124, abs=0.001)
        assert done["result"]["verified"] is True
        assert "Hola" in (env["playground"] / "index.html").read_text()
        # State file remembers the completed task.
        state = json.loads(env["state"].read_text())
        assert "hello-3-langs" in state["tasks_done"]
        assert state["runs"][0]["runtime"] == "standard"

    def test_fix_test_task_passes_check(self, env):
        job, err = first_task.start_task("fix-failing-test")
        assert err is None
        done = _wait_job(job["job_id"])
        assert done["status"] == "done"
        check = done["result"]["checks"][0]
        assert check["ok"] is True
        # The fake really wrapped content in <main>: the test passes now.
        proc = subprocess.run(
            [sys.executable, "test_page.py"], cwd=str(env["playground"]),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
        assert proc.returncode == 0

    def test_unknown_task_rejected(self, env):
        job, err = first_task.start_task("not-a-task")
        assert job is None
        assert err["ok"] is False
        assert "hello-3-langs" in err["tasks"]

    def test_busy_conflict_then_cancel(self, env):
        job, err = first_task.start_task(
            "hello-3-langs", extra_env={"FAKE_CLAUDE_SLEEP": "30"})
        assert err is None
        try:
            again, err2 = first_task.start_task("dark-mode")
            assert again is None
            assert err2["code"] == "busy"
            assert err2["job_id"] == job["job_id"]
        finally:
            res = first_task.cancel_job(job["job_id"])
        assert res["ok"] is True
        done = _wait_job(job["job_id"])
        assert done["status"] == "cancelled"

    def test_get_job_unknown(self):
        assert first_task.get_job("nope") is None

    def test_requested_free_task_refuses_paid_fallback(self, env):
        job, err = first_task.start_task("hello-3-langs", require_free=True)
        assert job is None
        assert err["code"] == "free_unavailable"
        assert first_task._JOBS == {}
        assert not env["playground"].exists()

    def test_free_child_scrubs_paid_and_nested_session_env(self, env, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-XXXX")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-oauth-XXXX")
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-parent")
        monkeypatch.setattr(first_task, "_free_router_env", lambda: {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:3017",
            "ANTHROPIC_AUTH_TOKEN": "test-router-XXXX",
        })
        captured = {}
        monkeypatch.setattr(first_task, "_run_job", lambda job, prompt, cmd_env, pg: captured.update(cmd_env))
        job, err = first_task.start_task("hello-3-langs", require_free=True, extra_env={
            "ANTHROPIC_API_KEY": "sk-ant-test-XXXX",
            "CLAUDE_CODE_SESSION_KEY": "test-session-XXXX",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_SESSION_ID": "test-parent",
            "CLAUDE_CODE_MESSAGING_SOCKET": "test-socket",
        })
        assert err is None
        first_task._JOBS[job["job_id"]]["thread"].join(timeout=3)
        assert job["runtime"] == "free"
        assert captured["CCC_SESSION_RUNTIME"] == "free"
        assert captured["ANTHROPIC_AUTH_TOKEN"] == "test-router-XXXX"
        assert all(k not in captured for k in (
            "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_SESSION_KEY",
            "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET"))

    def test_failed_checks_do_not_mark_task_complete(self, env, monkeypatch):
        first_task.ensure_playground()
        job = {"job_id": "test-unverified", "task_id": "hello-3-langs",
               "playground": str(env["playground"]), "runtime": "free"}
        first_task._finish_job(job, status="done")
        assert job["status"] == "error"
        assert job["result"]["verified"] is False
        assert "hello-3-langs" not in first_task._load_state().get("tasks_done", [])


class TestOpenResult:
    def test_opens_page_inside_playground(self, env):
        first_task.ensure_playground()
        opened = []
        res = first_task.open_result(opener=lambda p: opened.append(str(p)))
        assert res["ok"] is True
        assert opened and opened[0].endswith("index.html")
        assert str(env["playground"]) in opened[0]

    def test_missing_page_errors(self, tmp_path, monkeypatch):
        empty = tmp_path / "Empty-Playground"
        empty.mkdir()
        monkeypatch.setenv("CCC_PLAYGROUND_DIR", str(empty))
        res = first_task.open_result(opener=lambda p: None)
        assert res["ok"] is False


class TestHelpers:
    def test_api_value_pricing(self):
        assert first_task.api_value_usd({}) == 0.0
        v = first_task.api_value_usd({"input_tokens": 1_000_000, "output_tokens": 0})
        assert v == pytest.approx(3.0)
        # camelCase (modelUsage shape) works too.
        v2 = first_task.api_value_usd({"inputTokens": 500_000, "outputTokens": 100_000})
        assert v2 == pytest.approx(1.5 + 1.5)

    def test_friendly_tool_lines(self):
        assert "index.html" in first_task._friendly_tool_line(
            {"name": "Edit", "input": {"file_path": "/x/CCC-Playground/index.html"}})
        assert "python3 test_page.py" in first_task._friendly_tool_line(
            {"name": "Bash", "input": {"command": "python3 test_page.py"}})
        assert first_task._friendly_tool_line({"name": "Read", "input": {}}) == "Reading…"

    def test_status_shape(self, env):
        st = first_task.status()
        assert st["ok"] is True
        assert st["playground"]["path"].endswith("CCC-Playground")
        assert len(st["tasks"]) == 3
        assert st["runtime"]["free_ready"] is False
        assert st["any_done"] is False
        first_task.ensure_playground()
        st = first_task.status()
        assert st["playground"]["exists"] is True
        assert "index.html" in st["playground"]["files"]
