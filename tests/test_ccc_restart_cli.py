"""`ccc restart` (OPS-150 / CCC-206): agents restart CCC through the
drain-aware restart endpoints, and requests inside the debounce window merge
into one deferred restart instead of each costing a cold warmup.

Runs the real `ccc` script (and its detached --run-pending runner) against a
stub HTTP server standing in for the restart endpoints, with HOME pointed at
a temp dir so the debounce state file is isolated.
"""

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

CCC = str(Path(__file__).resolve().parent.parent / "ccc")


class _Stub(http.server.BaseHTTPRequestHandler):
    posts = []
    pid = 1000
    refuse = 0  # answer this many restart POSTs with 409 first

    def log_message(self, *a):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            self._send(200, {"pid": _Stub.pid, "code_rev": "abc"})
        else:
            self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if _Stub.refuse > 0:
            _Stub.refuse -= 1
            _Stub.posts.append(("409", self.path))
            self._send(409, {"ok": False, "error": "drain in flight"})
            return
        _Stub.posts.append(("200", self.path))
        before = _Stub.pid
        _Stub.pid += 1  # the "restarted" dashboard answers with a new pid
        self._send(200, {"ok": True, "before": {"pid": before}})


class CccRestartCliTests(unittest.TestCase):
    def setUp(self):
        _Stub.posts, _Stub.refuse = [], 0
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.home = tempfile.mkdtemp()
        self.env = dict(os.environ, HOME=self.home, CCC_RESTART_DEBOUNCE_S="3")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        # A runner may still be waiting for the (now gone) stub to answer.
        state_file = Path(self.home) / ".claude/command-center/restart-debounce.json"
        try:
            pid = json.loads(state_file.read_text()).get("runner_pid")
            if pid:
                os.kill(int(pid), 15)
        except (OSError, ValueError):
            pass

    def ccc(self, *args):
        return subprocess.run([sys.executable, CCC, "--server", self.base, "restart", *args],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def wait_posts(self, n, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline and len(_Stub.posts) < n:
            time.sleep(0.2)
        return list(_Stub.posts)

    def test_first_restart_fires_now_and_waits_for_new_pid(self):
        r = self.ccc()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_Stub.posts, [("200", "/api/restart/all")])
        self.assertIn("dashboard back (pid 1001", r.stdout)

    def test_requests_inside_window_merge_into_one_deferred_restart(self):
        self.assertEqual(self.ccc("worker").returncode, 0)
        r2 = self.ccc("worker")
        r3 = self.ccc("dashboard")
        self.assertIn("queued one worker restart", r2.stdout)
        self.assertIn("queued one all restart", r3.stdout)  # worker + dashboard == all
        posts = self.wait_posts(2)
        time.sleep(1.5)  # nothing else may fire after the merged one
        self.assertEqual(_Stub.posts, [("200", "/api/restart/worker"), ("200", "/api/restart/all")])
        self.assertEqual(posts[:2], _Stub.posts)

    def test_now_skips_the_window(self):
        self.ccc("worker")
        self.ccc("worker", "--now")
        self.assertEqual(_Stub.posts, [("200", "/api/restart/worker")] * 2)

    def test_refused_restart_is_retried_not_dropped(self):
        _Stub.refuse = 1
        r = self.ccc("worker")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not safe yet (drain in flight)", r.stdout)
        # Retry cadence is 60s in production; the runner honours the due time
        # recorded in the state file, so pull it in for the test.
        state_file = Path(self.home) / ".claude/command-center/restart-debounce.json"
        state = json.loads(state_file.read_text())
        state["pending"]["due"] = time.time()
        state_file.write_text(json.dumps(state))
        self.assertEqual(self.wait_posts(2, timeout=45),
                         [("409", "/api/restart/worker"), ("200", "/api/restart/worker")])

    def test_unreachable_server_fails(self):
        self.base = "http://127.0.0.1:9"  # discard port: connection refused
        r = self.ccc("worker")
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed", r.stderr)


if __name__ == "__main__":
    unittest.main()
