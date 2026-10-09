"""scripts/port_preflight.py: free / ccc / busy, and --pick."""
import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "port_preflight.py"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=30)


def _serve(body):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_free_port_reports_free():
    port = _free_port()
    assert _run(port).stdout.strip() == "free"
    assert _run(port, "--pick").stdout.strip() == str(port)


def test_other_program_is_busy_and_pick_moves_on():
    server = _serve({"hello": "not ccc"})
    try:
        port = server.server_address[1]
        assert _run(port).stdout.strip() == "busy"
        picked = int(_run(port, "--pick").stdout.strip())
        assert picked > port
    finally:
        server.shutdown()


def test_running_ccc_is_recognised_and_kept():
    server = _serve({"version": "5.37.0", "code_rev": "abc"})
    try:
        port = server.server_address[1]
        assert _run(port).stdout.strip() == "ccc"
        assert _run(port, "--pick").stdout.strip() == str(port)
    finally:
        server.shutdown()


def test_bad_usage():
    assert _run("nope").returncode == 2
