import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY = "sk-test-XXXX-domestic-XXXX"
MODEL = "byok/kimi-intl/kimi-k3"
REQUESTS = []


class Vendor(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        route = self.path.split("?", 1)[0]
        if route.endswith("/count_tokens"):
            data = json.dumps({"input_tokens": 10}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
            return
        assert route.endswith("/v1/messages"), self.path
        assert self.headers.get("Authorization") == "Bearer " + KEY
        assert self.headers.get("x-api-key") != "sk-ant-test-XXXX"
        assert body["model"] == "kimi-k3", body["model"]
        REQUESTS.append({"path": route, "model": body["model"], "auth": "test-provider-key"})
        message = {"id": "msg_test_XXXX", "type": "message", "role": "assistant", "model": "kimi-k3",
                   "content": [{"type": "text", "text": "domestic-test-OK"}], "stop_reason": "end_turn",
                   "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 3}}
        self.send_response(200)
        if not body.get("stream"):
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(message).encode())
            return
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = [
            {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "domestic-test-OK"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 3}},
            {"type": "message_stop"},
        ]
        for event in events:
            self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
        self.wfile.flush()


def wait_result(log_path, proc):
    deadline = time.monotonic() + 80
    while time.monotonic() < deadline:
        data = Path(log_path).read_text(errors="replace")
        for line in data.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                assert not event.get("is_error"), event
                assert "domestic-test-OK" in event.get("result", ""), event
                return event
        if proc.poll() is not None:
            raise AssertionError("Claude exited before a result: " + data[-2000:])
        time.sleep(0.2)
    raise AssertionError("Claude did not finish before the test deadline: " + data[-2000:])


def main():
    if not shutil.which("claude"):
        raise SystemExit("Install Claude Code before running this local transport test.")
    listener = ThreadingHTTPServer(("127.0.0.1", 0), Vendor)
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    entries = []
    try:
        with tempfile.TemporaryDirectory(prefix="ccc-domestic-e2e-") as home:
            os.environ["HOME"] = home
            os.environ["CCC_CONTROL_PLANE_ENGINES"] = "0"
            os.environ.pop("CCC_SSH_HOST", None)
            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-XXXX"
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "oauth-test-XXXX"
            os.environ["CCC_SESSION_RUNTIME"] = "free"
            sys.path.insert(0, str(ROOT))
            import server
            from ccc_server import byok, domestic_providers as dp
            byok._keychain_available = lambda: False
            project = Path(home) / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            dp._PRESETS["kimi-intl"] = {**dp._PRESETS["kimi-intl"], "base_url": f"http://127.0.0.1:{listener.server_port}/anthropic"}
            assert dp.save_key("kimi-intl", KEY)["ok"]
            first = server.spawn_session("Say hello.", name="domestic-test", cwd=str(project), repo_path=str(project), model=MODEL)
            assert first.get("ok"), first
            entry = next(e for e in server._spawned_sessions if e["pid"] == first["pid"])
            entries.append(entry)
            done = wait_result(first["log"], entry["proc"])
            # The dashboard's poller backfills the native session id into the
            # spawn registry; do the same here so resume can find the preset.
            sid = server._spawn_session_id_from_entry(entry) or done.get("session_id")
            assert sid, first
            assert dp.session_model(sid) == MODEL
            assert entry["model"] == MODEL
            assert entry["command"][entry["command"].index("--model") + 1] == "kimi-k3"
            assert not first.get("prewarmed") and not first.get("runtime")
            entry["proc"].terminate()
            entry["proc"].wait(timeout=10)
            second = server.resume_session_headless(sid, "Say hello again.", cwd=str(project))
            assert second.get("ok"), second
            resumed = next(e for e in server._spawned_sessions if e["pid"] == second["pid"])
            entries.append(resumed)
            wait_result(second["log"], resumed["proc"])
            assert len(REQUESTS) >= 2
            assert all(r["model"] == "kimi-k3" for r in REQUESTS)
            for e in entries:
                e["proc"].terminate()
                e["proc"].wait(timeout=10)
                if e.get("log_fh"):
                    e["log_fh"].close()
                if e.get("stdin_fd") is not None:
                    server._close_fd_quiet(e["stdin_fd"])
            entries.clear()
            print(json.dumps({"status": "VERIFIED", "real_cli": "Claude Code", "vendor": "controlled loopback fixture, no paid traffic",
                              "spawn": "raw vendor model, no paid prewarm or free badge", "cold_resume": "same provider and model",
                              "requests": REQUESTS}, indent=2))
    finally:
        for e in entries:
            if e["proc"].poll() is None:
                e["proc"].terminate()
                e["proc"].wait(timeout=10)
            if e.get("log_fh"):
                e["log_fh"].close()
        listener.shutdown()
        listener.server_close()


if __name__ == "__main__":
    main()
