"""CCC-282: an in-place restart must not surface as HTTP 502s.

The restart drains in-flight requests and hands the listening socket to the
exec'd successor, so connections made during the restart wait in the
kernel backlog instead of being refused (a refusal is a 502 behind
`tailscale serve`).
"""
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class _FakeHandler:
    def __init__(self, path):
        self.path = path


class InflightDrainTests(unittest.TestCase):
    def tearDown(self):
        with server._INFLIGHT_LOCK:
            server._INFLIGHT_REQUESTS.clear()

    def test_drain_waits_for_inflight_request_to_finish(self):
        h = _FakeHandler("/api/inject-input")
        server._inflight_begin(h)
        threading.Timer(0.2, server._inflight_end, args=(h,)).start()
        t0 = time.monotonic()
        left = server._drain_inflight_requests(timeout=5)
        self.assertEqual(left, [])
        self.assertGreaterEqual(time.monotonic() - t0, 0.15)

    def test_drain_is_bounded_and_reports_stragglers(self):
        h = _FakeHandler("/api/savings")
        server._inflight_begin(h)
        left = server._drain_inflight_requests(timeout=0.1)
        self.assertEqual(left, ["/api/savings"])

    def test_event_stream_response_is_not_counted(self):
        # send_header('Content-Type', 'text/event-stream') ends the count.
        h = server.CommandCenterHandler.__new__(server.CommandCenterHandler)
        h.path = "/api/events"
        h.request_version = "HTTP/1.0"
        h._headers_buffer = []
        server._inflight_begin(h)
        h.send_header("Content-Type", "text/event-stream")
        self.assertEqual(server._inflight_snapshot(), [])


class ListenSocketHandoffTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop(server._LISTEN_FD_ENV, None)

    def tearDown(self):
        os.environ.pop(server._LISTEN_FD_ENV, None)

    def _listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(16)
        self.addCleanup(s.close)
        return s

    def test_take_adopts_matching_listener_and_consumes_env(self):
        s = self._listener()
        port = s.getsockname()[1]
        dup = os.dup(s.fileno())  # stands in for the fd that survives exec
        os.environ[server._LISTEN_FD_ENV] = str(dup)
        got = server._take_inherited_listen_socket("127.0.0.1", port)
        self.assertIsNotNone(got)
        self.addCleanup(got.close)
        self.assertNotIn(server._LISTEN_FD_ENV, os.environ)
        self.assertFalse(got.get_inheritable())
        self.assertEqual(got.getsockname()[1], port)

    def test_take_rejects_wrong_port(self):
        s = self._listener()
        os.environ[server._LISTEN_FD_ENV] = str(os.dup(s.fileno()))
        self.assertIsNone(server._take_inherited_listen_socket("127.0.0.1", 1))
        self.assertNotIn(server._LISTEN_FD_ENV, os.environ)

    def test_take_ignores_garbage_env(self):
        os.environ[server._LISTEN_FD_ENV] = "not-a-fd"
        self.assertIsNone(server._take_inherited_listen_socket("127.0.0.1", 8091))

    def test_connection_made_during_exec_is_served_by_successor(self):
        # Real exec: the predecessor publishes its listener and execs; a
        # client that connected before the exec is answered afterwards.
        script = textwrap.dedent(f"""
            import os, socket, sys, time
            sys.path.insert(0, {str(ROOT)!r})
            if os.environ.get("STAGE") == "after":
                import server
                s = server._take_inherited_listen_socket("127.0.0.1", int(os.environ["PORT_"]))
                assert s is not None, "no inherited socket"
                c, _ = s.accept()
                c.sendall(b"served-by-successor")
                c.close()
                sys.exit(0)
            import server
            s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(8)
            port = s.getsockname()[1]
            print(port, flush=True)
            sys.stdin.readline()   # client has connected (queued, not accepted)
            assert server._prepare_listen_socket_handoff(s)
            os.environ["STAGE"] = "after"; os.environ["PORT_"] = str(port)
            os.execv(sys.executable, [sys.executable, __file__])
        """)
        path = Path(self._tmp_script())
        path.write_text(script)
        proc = subprocess.Popen(
            [sys.executable, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            env={k: v for k, v in os.environ.items() if k != server._LISTEN_FD_ENV},
        )
        self.addCleanup(proc.kill)
        port = int(proc.stdout.readline())
        client = socket.create_connection(("127.0.0.1", port), timeout=60)
        self.addCleanup(client.close)
        proc.stdin.write(b"go\n")
        proc.stdin.flush()
        data = client.recv(64)
        self.assertEqual(data, b"served-by-successor")
        self.assertEqual(proc.wait(timeout=60), 0)

    def _tmp_script(self):
        import tempfile
        fd, p = tempfile.mkstemp(suffix=".py")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(p) and os.unlink(p))
        return p


if __name__ == "__main__":
    unittest.main()
