"""Wire-level proof for the paid BYOK presets (Settings > Free models > Use
your own paid key).

Each preset's spawn env is built the way a real spawn builds it
(``server._spawn_env`` on a parent shell polluted with Anthropic credentials,
then ``domestic_providers.apply_env``), and the real ``claude`` CLI is run
against a local HTTP recorder that stands in for the vendor. Only the host of
``ANTHROPIC_BASE_URL`` is swapped to the recorder; the vendor's path prefix is
kept, so the recorded path proves the request would hit
``<preset base_url>/v1/messages``.

Asserted per preset: ``Authorization: Bearer <saved key>`` (the envelope the
vendors document for Claude Code via ``ANTHROPIC_AUTH_TOKEN``), no
``x-api-key``, the vendor model id in the body, and no inherited Anthropic
key, OAuth token or ``ANTHROPIC_CUSTOM_HEADERS`` value anywhere in the
request. The recorder answers every call itself: nothing leaves 127.0.0.1 and
no paid provider is contacted.

Skipped when the ``claude`` CLI is not installed (CI has none)."""

import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import server
from ccc_server import byok, domestic_providers as dp
from ccc_server.domestic_presets import DOMESTIC_PRESETS

CLAUDE = os.environ.get("CCC_TEST_CLAUDE_BIN") or shutil.which("claude")
pytestmark = pytest.mark.skipif(not CLAUDE, reason="claude CLI not installed")

PARENT_SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-test-XXXX-parent-api-key",
    "ANTHROPIC_AUTH_TOKEN": "sk-ant-test-XXXX-parent-auth-token",
    "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test-XXXX-parent-oauth",
    "CLAUDE_CODE_SESSION_KEY": "sk-ant-sid-test-XXXX-parent-session",
    "ANTHROPIC_CUSTOM_HEADERS": "X-Gateway-Token: gateway-test-XXXX-parent",
}
PARENT_ROUTING = {
    "ANTHROPIC_BASE_URL": "https://gateway.invalid/anthropic",
    "ANTHROPIC_MODEL": "claude-opus-5",
    "CLAUDE_CODE_USE_BEDROCK": "1",
}


def _sse(model):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_recorder", "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "ok"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


class _Recorder(http.server.BaseHTTPRequestHandler):
    calls = []

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        type(self).calls.append({"method": self.command, "path": self.path,
                                 "headers": {k.lower(): v for k, v in self.headers.items()},
                                 "raw": raw.decode("utf-8", "replace"), "body": body})
        return body

    def do_POST(self):
        body = self._record()
        if urlsplit(self.path).path.endswith("/v1/messages") and body.get("stream"):
            payload = _sse(body.get("model") or "")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            payload = b'{"type":"error","error":{"type":"not_found_error","message":"recorder"}}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def recorder():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(byok, "_state_dir", lambda: tmp_path / "byok")
    monkeypatch.setattr(byok, "_keychain_available", lambda: False)
    monkeypatch.setattr(server, "_MODEL_CATALOG_CACHE", {"ts": 1.0, "data": {}})
    for name, value in {**PARENT_SECRETS, **PARENT_ROUTING}.items():
        monkeypatch.setenv(name, value)
    return home, repo


@pytest.mark.parametrize("preset", DOMESTIC_PRESETS, ids=[p["id"] for p in DOMESTIC_PRESETS])
def test_preset_request_reaches_vendor_path_with_bearer_only(preset, recorder, isolated):
    home, repo = isolated
    key = f"{preset['id']}-test-XXXX-0123456789abcdef"
    assert dp.save_key(preset["id"], key)["ok"]
    model = preset["models"][0]
    spawn = dp.resolve_spawn(f"byok/{preset['id']}/{model}", str(repo))
    child_env = dp.apply_env(server._spawn_env(), spawn["env"])

    vendor = urlsplit(preset["base_url"])
    assert child_env["ANTHROPIC_BASE_URL"] == preset["base_url"]
    child_env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{recorder.server_port}{vendor.path}"
    child_env["HOME"] = str(home)
    child_env.pop("NODE_OPTIONS", None)

    _Recorder.calls.clear()
    proc = subprocess.run([CLAUDE, "-p", "Reply with ok.", "--max-turns", "1"],
                          cwd=repo, env=child_env, capture_output=True, text=True, timeout=90)
    calls = list(_Recorder.calls)
    messages = [c for c in calls if urlsplit(c["path"]).path.endswith("/v1/messages")]
    assert messages, f"claude made no /v1/messages call (rc={proc.returncode}): {proc.stderr[-500:]}"

    leaked = [v for v in PARENT_SECRETS.values()] + ["gateway-test-XXXX", "gateway.invalid"]
    for call in calls:
        assert urlsplit(call["path"]).path.startswith(vendor.path + "/"), call["path"]
        headers = call["headers"]
        assert headers.get("authorization") == f"Bearer {key}"
        assert "x-api-key" not in headers
        assert "x-gateway-token" not in headers
        dump = json.dumps(headers) + call["raw"]
        for secret in leaked:
            assert secret not in dump, f"inherited value reached the vendor on {call['path']}"
    for call in messages:
        assert urlsplit(call["path"]).path == vendor.path + "/v1/messages"
        assert call["body"].get("model") == model
