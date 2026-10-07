"""Tests for ccc_server.setup_jobs + ccc_server.setup_steps (first-run setup).

Hermetic: node tarball resolution and sha checks run against injected fakes,
and installs are exercised only through the no-op `python` step or external
steps. No test invokes a real installer or touches the network.
"""

import hashlib
import json
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

from ccc_server import setup_jobs, setup_steps


# ------------------------------------------------------------------ helpers


def _wait_done(job_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = setup_jobs.describe_job(job_id)
        if payload and payload["status"] != "running":
            return payload
        time.sleep(0.02)
    return setup_jobs.describe_job(job_id)


class FakeHandler:
    """Minimal stand-in for the HTTP handler so route helpers can be tested
    without booting a server."""

    def __init__(self, path, body=None):
        self.path = path
        self.responses = []
        self.html = None
        self.headers = {}
        self._body = body
        if body is not None:
            raw = json.dumps(body).encode()
            self.headers["Content-Length"] = str(len(raw))
            import io

            self.rfile = io.BytesIO(raw)

    def send_json(self, data, status=200, **_kw):
        self.responses.append((status, data))

    def send_html(self, content):
        self.html = content

    @property
    def last(self):
        return self.responses[-1] if self.responses else (None, None)


# --------------------------------------------------------------- job runner


def test_job_runs_steps_to_done():
    seen = []

    def step_a(job):
        job.emit("hello from a")
        seen.append("a")

    def step_b(job):
        job.emit("hello from b")
        job.set_step_progress(0.5)
        seen.append("b")

    job_id = setup_jobs.start_job(
        "test", [("a", "Step A", step_a), ("b", "Step B", step_b)]
    )
    payload = _wait_done(job_id)
    assert payload["status"] == "done"
    assert payload["progress"] == 1.0
    assert seen == ["a", "b"]
    assert "hello from a" in payload["lines"]
    assert "hello from b" in payload["lines"]
    states = {s["id"]: s["state"] for s in payload["steps"]}
    assert states == {"a": "done", "b": "done"}
    assert payload["finished_ts"]


def test_job_step_failure_marks_error():
    def boom(job):
        job.emit("about to fail")
        raise RuntimeError("kaboom")

    job_id = setup_jobs.start_job("test", [("x", "Step X", boom)])
    payload = _wait_done(job_id)
    assert payload["status"] == "error"
    assert payload["error"] == "kaboom"
    assert payload["steps"][0]["state"] == "error"
    assert not payload["cancelled"]


def test_job_cancel_stops_work():
    started = threading.Event()

    def slow(job):
        started.set()
        while True:
            job.check_cancelled()
            time.sleep(0.02)

    job_id = setup_jobs.start_job("test", [("s", "Slow", slow)])
    assert started.wait(5)
    job = setup_jobs.cancel_job(job_id)
    assert job is not None
    payload = _wait_done(job_id)
    assert payload["status"] == "error"
    assert payload["cancelled"] is True
    assert payload["error"] == "Cancelled"


def test_job_cancel_kills_child_process():
    started = threading.Event()

    def sleeper(job):
        started.set()
        job.run_cmd([sys.executable, "-c", "import time; time.sleep(60)"])

    job_id = setup_jobs.start_job("test", [("s", "Sleep", sleeper)])
    assert started.wait(5)
    time.sleep(0.3)  # let the child spawn
    setup_jobs.cancel_job(job_id)
    payload = _wait_done(job_id)
    assert payload["cancelled"] is True


def test_run_cmd_streams_output():
    def step(job):
        rc = job.run_cmd([sys.executable, "-c", "print('alpha'); print('beta')"])
        assert rc == 0

    job_id = setup_jobs.start_job("test", [("c", "Cmd", step)])
    payload = _wait_done(job_id)
    assert payload["status"] == "done"
    assert "alpha" in payload["lines"]
    assert "beta" in payload["lines"]


def test_run_cmd_missing_command_returns_127():
    def step(job):
        rc = job.run_cmd(["definitely-not-a-real-binary-xyz"])
        assert rc == 127

    job_id = setup_jobs.start_job("test", [("c", "Cmd", step)])
    payload = _wait_done(job_id)
    assert payload["status"] == "done"
    assert any("Command not found" in line for line in payload["lines"])


def test_lines_capped_at_200():
    def step(job):
        for i in range(300):
            job.emit(f"line {i}")

    job_id = setup_jobs.start_job("test", [("c", "Chatty", step)])
    payload = _wait_done(job_id)
    assert len(payload["lines"]) == setup_jobs.MAX_JOB_LINES
    assert payload["lines"][-1] == "line 299"
    assert payload["lines"][0] == "line 100"


def test_emit_strips_ansi_and_cr():
    def step(job):
        job.emit("\x1b[32mgreen\x1b[0m text")
        job.emit("carriage\rreturn")

    job_id = setup_jobs.start_job("test", [("c", "Ansi", step)])
    payload = _wait_done(job_id)
    assert "green text" in payload["lines"]
    assert "carriage" in payload["lines"]
    assert "return" in payload["lines"]
    assert not any("\x1b" in line for line in payload["lines"])


def test_progress_line_rewrites():
    def step(job):
        job.progress_line("Downloading 10%")
        job.progress_line("Downloading 80%")
        job.emit("done")

    job_id = setup_jobs.start_job("test", [("d", "Dl", step)])
    payload = _wait_done(job_id)
    assert "Downloading 80%" in payload["lines"]
    assert "Downloading 10%" not in payload["lines"]


def test_describe_unknown_job_returns_none():
    assert setup_jobs.describe_job("nope-not-a-job") is None


def test_cancel_unknown_job_returns_none():
    assert setup_jobs.cancel_job("nope-not-a-job") is None


# ------------------------------------------------------------- run_setup()


def test_run_setup_rejects_unknown_steps():
    job_id, error, status, already = setup_jobs.run_setup(["bogus_step"])
    assert job_id is None
    assert status == 400
    assert "bogus_step" in error["unknown"]


def test_run_setup_rejects_empty():
    job_id, error, status, already = setup_jobs.run_setup([])
    assert job_id is None
    assert status == 400


def test_run_setup_python_step_succeeds():
    job_id, error, status, already = setup_jobs.run_setup(["python"])
    assert error is None and not already
    payload = _wait_done(job_id)
    assert payload["status"] == "done"
    assert payload["steps"][0]["state"] == "done"
    assert any("Python" in line for line in payload["lines"])


def test_run_setup_external_step_skips():
    job_id, error, status, already = setup_jobs.run_setup(["first_task"])
    assert error is None
    payload = _wait_done(job_id)
    assert payload["status"] == "done"
    assert payload["steps"][0]["state"] == "skipped"
    assert any("own" in line.lower() or "screen" in line.lower()
               for line in payload["lines"])


def test_run_setup_dedupes_running_setup_job():
    release = threading.Event()
    orig = setup_steps.STEPS
    try:
        slow_step = {
            "id": "python",
            "label": "Python",
            "install": lambda job: release.wait(10) or True,
        }
        monkey = dict(orig[2])  # python entry
        monkey.update(slow_step)
        setup_steps.STEPS = [monkey if s["id"] == "python" else s for s in orig]
        first, err1, _s1, already1 = setup_jobs.run_setup(["python"])
        second, err2, _s2, already2 = setup_jobs.run_setup(["python"])
        assert err1 is None and err2 is None
        assert first == second
        assert already1 is False and already2 is True
    finally:
        release.set()
        setup_steps.STEPS = orig
        _wait_done(first)


# ------------------------------------------------------------------- plan


def test_plan_shape_matches_contract():
    plan = setup_steps.build_plan(force=True)
    ids = [s["id"] for s in plan["steps"]]
    assert ids == [
        "clt", "git", "python", "node", "claude_cli", "gh",
        "free_router", "free_key", "first_task",
    ]
    for step in plan["steps"]:
        assert step["status"] in ("ok", "missing", "outdated", "error")
        assert isinstance(step["label"], str) and step["label"]
        assert isinstance(step["detail"], str)
        assert isinstance(step["needs_consent"], bool)
        assert isinstance(step["est_seconds"], int)
    assert plan["summary"]["total"] == len(ids)


def test_plan_caches_and_refreshes():
    first = setup_steps.build_plan(force=True)
    second = setup_steps.build_plan()
    assert second is not first or second == first  # cached payload
    third = setup_steps.build_plan(force=True)
    assert third["steps"][0]["id"] == "clt"


def test_empty_playground_is_not_a_completed_task(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "CCC-Playground").mkdir()
    monkeypatch.setenv("CCC_FIRST_TASK_STATE", str(tmp_path / "first-task.json"))
    assert setup_steps._detect_first_task()[0] == "missing"
    (tmp_path / "first-task.json").write_text(json.dumps({"tasks_done": ["hello-3-langs"]}))
    assert setup_steps._detect_first_task()[0] == "ok"


def test_stopped_router_needs_a_start_step(monkeypatch):
    monkeypatch.setattr(setup_steps, "_free_router_status", lambda: {
        "installed": True, "running": False, "healthy": False})
    assert setup_steps._detect_free_router()[0] == "missing"


def test_python_step_always_ok():
    status, detail = setup_steps._detect_python()
    assert status == "ok"
    assert "Python" in detail or "v" in detail


# --------------------------------------------------------------- pure bits


def test_parse_version():
    assert setup_steps._parse_version("v22.11.0") == (22, 11, 0)
    assert setup_steps._parse_version("git version 2.39.5 (Apple Git-154)") == (2, 39, 5)
    assert setup_steps._parse_version("no version") is None


def test_node_supported_bounds():
    assert setup_steps._node_supported((22, 11, 0))
    assert setup_steps._node_supported((20, 18, 0))
    assert not setup_steps._node_supported((20, 17, 9))
    assert not setup_steps._node_supported((25, 0, 0))
    assert not setup_steps._node_supported((19, 9, 9))


def test_resolve_node_tarball_from_sums():
    fake_sums = "\n".join(
        [
            "aaaa  node-v22.11.0-darwin-arm64.tar.gz",
            "bbbb  node-v22.11.0-darwin-x64.tar.gz",
            "cccc  node-v22.11.0-linux-x64.tar.gz",
            "dddd  node-v22.11.0-linux-arm64.tar.gz",
            "eeee  node-v22.11.0-win-x64.zip",
            "ffff  node-v21.9.9-darwin-arm64.tar.gz",
        ]
    )
    ver, name, sha = setup_steps._resolve_node_tarball(
        "darwin-arm64", fetch=lambda url: fake_sums
    )
    assert ver == "22.11.0"
    assert name == "node-v22.11.0-darwin-arm64.tar.gz"
    assert sha == "aaaa"
    ver2, name2, _ = setup_steps._resolve_node_tarball(
        "linux-x64", fetch=lambda url: fake_sums
    )
    assert name2 == "node-v22.11.0-linux-x64.tar.gz"
    with pytest.raises(RuntimeError):
        setup_steps._resolve_node_tarball("win-x64", fetch=lambda url: fake_sums)


def test_resolve_node_tarball_fetch_url():
    seen = []

    def fetch(url):
        seen.append(url)
        return "aaaa  node-v22.11.0-darwin-arm64.tar.gz"

    setup_steps._resolve_node_tarball("darwin-arm64", fetch=fetch)
    assert seen[0].endswith("/latest-v22.x/SHASUMS256.txt")


def test_verify_sha256(tmp_path):
    content = b"hello node tarball"
    f = tmp_path / "n.tar.gz"
    f.write_bytes(content)
    good = hashlib.sha256(content).hexdigest()
    assert setup_steps._verify_sha256(f, good)
    assert not setup_steps._verify_sha256(f, "0" * 64)


def test_node_platform_token():
    plat = setup_steps._node_platform()
    assert plat is None or plat in (
        "darwin-arm64", "darwin-x64", "linux-x64", "linux-arm64"
    )


# ------------------------------------------------------------------ routes


def test_handle_get_plan():
    handler = FakeHandler("/api/setup/plan")
    parsed = urllib.parse.urlparse("/api/setup/plan")
    setup_jobs.handle_get(handler, parsed)
    status, data = handler.last
    assert status == 200
    assert "steps" in data


def test_handle_get_unknown_job_404():
    handler = FakeHandler("/api/setup/jobs/deadbeef")
    parsed = urllib.parse.urlparse("/api/setup/jobs/deadbeef")
    setup_jobs.handle_get(handler, parsed)
    status, data = handler.last
    assert status == 404


def test_handle_get_setup_page():
    handler = FakeHandler("/setup")
    parsed = urllib.parse.urlparse("/setup")
    setup_jobs.handle_get(handler, parsed)
    assert handler.html and "cccSetup" in handler.html


def test_handle_post_run_and_poll():
    handler = FakeHandler("/api/setup/run", body={"steps": ["python"]})
    setup_jobs.handle_post(handler)
    status, data = handler.last
    assert status == 200
    assert data["job_id"]
    payload = _wait_done(data["job_id"])
    assert payload["status"] == "done"


def test_handle_post_run_bad_body():
    import io

    handler = FakeHandler("/api/setup/run")
    handler.headers["Content-Length"] = "4"
    handler.rfile = io.BytesIO(b"nope")
    setup_jobs.handle_post(handler)
    status, _data = handler.last
    assert status == 400


def test_handle_post_cancel_unknown_404():
    handler = FakeHandler("/api/setup/jobs/deadbeef/cancel", body={})
    setup_jobs.handle_post(handler)
    status, _data = handler.last
    assert status == 404
