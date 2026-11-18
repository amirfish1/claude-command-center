"""Isolated browser voice harness: real assets/worker/worklet, no dashboard state."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ccc_server import browser_voice


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if os.environ.get("CCC_BROWSER_VOICE_TEST_WORKER") == "1" and self.path == "/api/browser-voice/status":
            self.send_json({"ok": True, "installed": True, "manifest": browser_voice.manifest()})
        elif os.environ.get("CCC_BROWSER_VOICE_TEST_WORKER") == "1" and self.path == "/static/browser-voice-worker.js":
            body = (ROOT / "tests/fixtures/browser_voice_producer.js").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/browser-voice/"):
            browser_voice.handle(self, "GET")
        elif self.path.split("?", 1)[0] == "/":
            body = b'''<!doctype html><meta charset="utf-8"><title>Browser voice test</title>
              <button id="settingsBrowserVoiceToggle" role="switch" aria-checked="false">Browser voice</button>
              <p id="settingsBrowserVoiceStatus" role="status"></p>
              <script src="/static/browser-voice.js"></script>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def do_POST(self):
        browser_voice.handle(self, "POST")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(server.server_port, flush=True)
    server.serve_forever()
