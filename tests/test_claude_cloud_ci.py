"""The cloud bridge must fail closed on missing or unrelated results."""

import base64
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "cloud_ci_bridge", Path(__file__).parents[1] / "scripts" / "claude-cloud-ci.py"
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


@pytest.fixture
def config():
    return {
        "fire_url": "https://api.anthropic.com/v1/claude_code/routines/trig_test/fire",
        "fire_token": "test-task-token",
        "github_token": "test-github-token",
        "repository": "example/demo",
        "sha": "a" * 40,
        "ref": "main",
        "run_id": "123",
        "attempt": "2",
        "timeout": 40,
    }


def report_response(config, **updates):
    report = {"repository": config["repository"], "sha": config["sha"],
              "status_context": "claude-cloud-ci/123/2",
              "checks": {name: "success" for name in bridge.CHECKS}}
    report.update(updates)
    return {"encoding": "base64", "content": base64.b64encode(json.dumps(report).encode()).decode()}


class Transport:
    def __init__(self, reports, session_id="session_test"):
        self.reports = iter(reports)
        self.calls = []
        self.session_id = session_id

    def __call__(self, url, token, body=None, headers=None):
        self.calls.append((url, token, body, headers))
        if body is not None:
            return {
                "claude_code_session_id": self.session_id,
                "claude_code_session_url": "https://claude.ai/code/" + self.session_id,
            }
        result = next(self.reports)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize("session_id", ["session_test", "cse_test"])
def test_waits_for_this_run_and_attempt(config, session_id):
    remote = Transport([
        RuntimeError("Remote API returned HTTP 404"), report_response(config),
    ], session_id)
    assert bridge.dispatch_and_wait(config, remote, lambda: 0, lambda _: None) == 0
    assert len(remote.calls) == 3
    assert all("claude%2Fcloud-ci-results%2F123%2F2" in call[0] for call in remote.calls[1:])
    assert remote.calls[0][1] == config["fire_token"]
    assert all(call[1] == config["github_token"] for call in remote.calls[1:])


@pytest.mark.parametrize("state", ["failure", "error"])
def test_failed_or_incomplete_checks_fail_workflow(config, state):
    checks = {name: "success" for name in bridge.CHECKS}
    checks["unittest"] = state
    remote = Transport([report_response(config, checks=checks)])
    assert bridge.dispatch_and_wait(config, remote, lambda: 0, lambda _: None) == 1


@pytest.mark.parametrize("updates", [
    {"sha": "b" * 40}, {"status_context": "claude-cloud-ci/123/1"},
    {"repository": "other/repo"}, {"checks": {"unittest": "success"}},
    {"checks": {name: "pending" for name in bridge.CHECKS}},
])
def test_unrelated_or_incomplete_report_cannot_pass(config, updates):
    remote = Transport([report_response(config, **updates)])
    with pytest.raises(RuntimeError):
        bridge.dispatch_and_wait(config, remote, lambda: 0, lambda _: None)


def test_api_permission_error_is_not_hidden_as_missing_report(config):
    remote = Transport([RuntimeError("Remote API returned HTTP 403")])
    with pytest.raises(RuntimeError, match="403"):
        bridge.dispatch_and_wait(config, remote, lambda: 0, lambda _: None)


def test_timeout_does_not_redispatch(config):
    remote = Transport([RuntimeError("Remote API returned HTTP 404")])
    times = iter([0, 0, 1, 41])
    with pytest.raises(RuntimeError, match="Timed out"):
        bridge.dispatch_and_wait(config, remote, lambda: next(times), lambda _: None)
    assert len([call for call in remote.calls if call[2] is not None]) == 1


def test_ambiguous_dispatch_failure_is_not_retried(config):
    calls = []

    def ambiguous(*args):
        calls.append(args)
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        bridge.dispatch_and_wait(config, ambiguous)
    assert len(calls) == 1


@pytest.mark.parametrize("url", [
    "https://attacker.example/fire",
    "https://api.anthropic.com.attacker.example/v1/claude_code/routines/trig_test/fire",
    "https://api.anthropic.com/v1/claude_code/routines/trig_test/fire?token=test",
])
def test_fire_token_only_goes_to_official_endpoint(config, url):
    config["fire_url"] = url
    with pytest.raises(ValueError, match="official"):
        bridge.dispatch_and_wait(config)


def test_auth_is_not_forwarded_on_redirect():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/credential-trap")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with pytest.raises(RuntimeError, match="HTTP 302"):
            bridge.request_json("http://127.0.0.1:%s/start" % server.server_port, "test-token")
        assert calls == [("/start", "Bearer test-token")]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
