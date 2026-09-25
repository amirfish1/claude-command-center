"""Browser sidekick: dev-URL detection and the frame-friendly loopback proxy.

The proxy tests run a real throwaway HTTP server on 127.0.0.1 so the header
rewriting, redirects and the WebSocket tunnel are exercised end to end.
"""
import http.client
import os
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile

from ccc_server import browser_sidekick as bs


class ExtractUrlsTest(unittest.TestCase):
    def test_finds_loopback_urls_most_recent_first(self):
        text = (
            '{"text":"  VITE ready\\n  Local:   http://localhost:5173/\\n"}\n'
            '{"text":"Server running at http://0.0.0.0:8000/api?x=1."}\n'
            '{"text":"curl (http://127.0.0.1:3000)."}\n'
        )
        self.assertEqual(
            bs.extract_urls(text),
            ["http://127.0.0.1:3000/", "http://localhost:8000/api?x=1", "http://localhost:5173/"],
        )

    def test_ignores_non_loopback_and_portless(self):
        self.assertEqual(bs.extract_urls("https://example.com:8080/ http://localhost/ http://10.0.0.2:80"), [])

    def test_stops_at_json_escapes_and_ansi(self):
        self.assertEqual(bs.extract_urls('"http://localhost:4000/app\\n\\u001b[0m"'), ["http://localhost:4000/app"])
        self.assertEqual(bs.extract_urls("http://localhost:4000/x\x1b[39m"), ["http://localhost:4000/x"])

    def test_repeated_url_moves_to_front(self):
        self.assertEqual(
            bs.extract_urls("http://localhost:1111 http://localhost:2222 http://localhost:1111"),
            ["http://localhost:1111/", "http://localhost:2222/"],
        )


class ScanTranscriptTest(unittest.TestCase):
    def setUp(self):
        bs._scan_cache.clear()
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "s.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_incremental_scan_reads_only_appended_bytes(self):
        self.path.write_text('{"t":"http://localhost:3000/"}\n')
        self.assertEqual(bs.scan_transcript(self.path), ["http://localhost:3000/"])
        size_before = bs._scan_cache[str(self.path)]["size"]
        with open(self.path, "a") as fh:
            fh.write('{"t":"now on http://localhost:5173/"}\n')
        os.utime(self.path, (time.time() + 5, time.time() + 5))
        self.assertEqual(bs.scan_transcript(self.path), ["http://localhost:5173/", "http://localhost:3000/"])
        self.assertGreater(bs._scan_cache[str(self.path)]["size"], size_before)

    def test_unchanged_file_is_not_reread(self):
        self.path.write_text('{"t":"http://localhost:3000/"}\n')
        bs.scan_transcript(self.path)
        real_open = open
        opened = []

        def spy(*a, **k):
            opened.append(a[0])
            return real_open(*a, **k)

        import builtins
        builtins.open = spy
        try:
            bs.scan_transcript(self.path)
        finally:
            builtins.open = real_open
        self.assertEqual(opened, [])

    def test_binary_store_is_skipped(self):
        db = Path(self.dir.name) / "store.db"
        db.write_bytes(b"http://localhost:3000/")
        self.assertEqual(bs.scan_transcript(db), [])


class DetectTest(unittest.TestCase):
    def setUp(self):
        bs._scan_cache.clear()
        self.dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.dir.name) / "repo"
        self.repo.mkdir()
        self.transcript = Path(self.dir.name) / "s.jsonl"
        self.transcript.write_text(
            '{"t":"Local: http://localhost:5173/app/"}\n'
            '{"t":"old server http://localhost:4000/"}\n'
            '{"t":"CCC http://127.0.0.1:8090/"}\n'
        )

    def tearDown(self):
        self.dir.cleanup()

    def detect(self, **kw):
        # pid 100 = session; 101 its child (vite on 5173); 200 an orphaned
        # `npm run dev &` running inside the repo (port 3000); 300 unrelated.
        listen = {101: {5173}, 200: {3000}, 300: {9999}}
        ppid = {100: 1, 101: 100, 200: 1, 300: 1}
        cwds = {200: str(self.repo / "web"), 300: "/somewhere/else"}
        return bs.detect_dev_urls(
            transcript_path=self.transcript,
            snapshots=lambda: (listen, ppid),
            pid_cwds=lambda pids: {p: cwds[p] for p in pids if p in cwds},
            is_open=lambda port: False,
            **kw,
        )

    def test_merges_process_cwd_and_transcript_signals(self):
        items = self.detect(pid=100, cwd=str(self.repo), exclude_ports=(8090,))
        self.assertEqual(
            [(i["port"], i["source"], i["live"]) for i in items],
            [(5173, "process", True), (3000, "cwd", True), (4000, "transcript", False)],
        )
        # The transcript's path is kept for a port found via the process tree.
        self.assertEqual(items[0]["url"], "http://localhost:5173/app/")
        self.assertEqual(items[1]["url"], "http://localhost:3000/")

    def test_excludes_ccc_port_and_unrelated_processes(self):
        ports = [i["port"] for i in self.detect(pid=100, cwd=str(self.repo), exclude_ports=(8090,))]
        self.assertNotIn(8090, ports)
        self.assertNotIn(9999, ports)

    def test_home_directory_cwd_does_not_claim_everything(self):
        items = bs.detect_dev_urls(
            cwd=str(Path.home()),
            snapshots=lambda: ({200: {3000}}, {200: 1}),
            pid_cwds=lambda pids: {200: str(Path.home() / "anything")},
            is_open=lambda port: False,
        )
        self.assertEqual(items, [])

    def test_transcript_only_when_session_not_live(self):
        called = []
        items = bs.detect_dev_urls(
            transcript_path=self.transcript,
            snapshots=lambda: called.append(1) or ({}, {}),
            is_open=lambda port: False,
            exclude_ports=(8090,),
        )
        self.assertEqual(called, [], "no pid/cwd means no lsof/ps at all")
        self.assertEqual([i["port"] for i in items], [4000, 5173])


class SnapshotCacheTest(unittest.TestCase):
    """Perf gate: one lsof + one ps per TTL window, shared by every session."""

    def test_snapshots_fork_once_per_ttl(self):
        calls = []
        orig = bs._run
        bs._run = lambda argv, timeout=4.0: calls.append(argv[0]) or ""
        bs._snap.update(ts=0.0, listen={}, ppid={})
        try:
            bs._snapshots(now=1000.0)
            per_window = len(calls)
            self.assertLessEqual(per_window, 3)  # netstat (+lsof fallback) + ps
            for _ in range(20):
                bs._snapshots(now=1000.0 + 1)
            self.assertEqual(len(calls), per_window, "no forks inside the window")
            bs._snapshots(now=1000.0 + bs._SNAPSHOT_TTL_S + 0.1)
            self.assertEqual(len(calls), 2 * per_window)
        finally:
            bs._run = orig
            bs._snap.update(ts=0.0, listen={}, ppid={})

    def test_netstat_parser(self):
        out = (
            "Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)          rxbytes      txbytes  rhiwat  shiwat          process:pid    state  options\n"
            "tcp4       0      0  127.0.0.1.52982        *.*                    LISTEN                 0            0  131072  131072 Google Chrome fo:91452  00100 00000006 0000000000194288\n"
            "tcp46      0      0  *.3000                 *.*                    LISTEN                 0            0  131072  131072             node:4242  00000 00000006 00000000001937d8\n"
            "tcp6       0      0  ::1.5173               *.*                    LISTEN                 0            0  131072  131072             node:4242  00000 00000006 00000000001937d8\n"
            "tcp4       0      0  127.0.0.1.50000        127.0.0.1.3000         ESTABLISHED            0            0  131072  131072           Python:1  00000 00000006 00000000001937d8\n"
        )
        self.assertEqual(bs.parse_netstat_listen(out), {91452: {52982}, 4242: {3000, 5173}})

    def test_idle_session_skips_pid_lookup(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "s.jsonl"
            t.write_text("{}\n")
            old = time.time() - bs._LIVE_CANDIDATE_WINDOW_S - 60
            os.utime(t, (old, old))
            calls = []

            class Core:
                def session_live_status(self, sid, cwd):
                    calls.append(sid)
                    return {"live": True, "pid": 7}

            bs._pid_cache.clear()
            self.assertIsNone(bs._session_pid(Core(), "s1", None, str(t)))
            os.utime(t, None)
            self.assertEqual(bs._session_pid(Core(), "s1", None, str(t)), 7)
            self.assertEqual(bs._session_pid(Core(), "s1", None, str(t)), 7)
            self.assertEqual(calls, ["s1"], "idle skipped, then one lookup cached")

    def test_parsers(self):
        self.assertEqual(
            bs.parse_lsof_listen("p10\nf5\nn*:3000\nn[::1]:3000\np11\nf7\nn127.0.0.1:5173\n"),
            {10: {3000}, 11: {5173}},
        )
        self.assertEqual(bs.parse_ps_ppid("  1 0\n 10 1\nbad\n"), {1: 0, 10: 1})
        self.assertEqual(bs.descendants(1, {1: 0, 2: 1, 3: 2, 4: 9}), {1, 2, 3})


class FrameHeadersTest(unittest.TestCase):
    def headers(self, pairs):
        msg = http.client.HTTPMessage()
        for k, v in pairs:
            msg[k] = v
        return msg

    def test_frame_blocked(self):
        self.assertIn("DENY", bs.frame_blocked(self.headers([("X-Frame-Options", "DENY")])))
        self.assertIn("frame-ancestors", bs.frame_blocked(self.headers(
            [("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")])))
        self.assertEqual(bs.frame_blocked(self.headers([("Content-Security-Policy", "default-src 'self'")])), "")
        self.assertEqual(bs.frame_blocked(self.headers([("Content-Security-Policy", "frame-ancestors *")])), "")

    def test_rewrite_response_headers(self):
        out = dict(bs.rewrite_response_headers(
            [("X-Frame-Options", "SAMEORIGIN"),
             ("Content-Security-Policy", "script-src 'self'; frame-ancestors 'none'"),
             ("Location", "http://127.0.0.1:3000/login?next=/"),
             ("Transfer-Encoding", "chunked"),
             ("Set-Cookie", "a=1")],
            ("http://localhost:3000", "http://127.0.0.1:3000"),
            "http://127.0.0.1:55555",
        ))
        self.assertNotIn("X-Frame-Options", out)
        self.assertNotIn("Transfer-Encoding", out)
        self.assertIn("script-src 'self'", out["Content-Security-Policy"])
        self.assertNotIn("'none'", out["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'self' http://localhost:*", out["Content-Security-Policy"])
        self.assertEqual(out["Location"], "http://127.0.0.1:55555/login?next=/")
        self.assertEqual(out["Set-Cookie"], "a=1")


class _DevServer(BaseHTTPRequestHandler):
    """A dev server that refuses framing, redirects, and speaks a toy upgrade."""
    protocol_version = "HTTP/1.1"
    seen_hosts = []

    def log_message(self, *_a):
        pass

    def do_GET(self):
        _DevServer.seen_hosts.append(self.headers.get("Host"))
        if self.headers.get("Upgrade") == "websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.flush()
            data = self.connection.recv(1024)
            self.connection.sendall(b"echo:" + data)
            self.close_connection = True
            return
        if self.path == "/old":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_address[1]}/new")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"<h1>hello from dev</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = b"got:" + self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProxyEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dev = ThreadingHTTPServer(("127.0.0.1", 0), _DevServer)
        cls.dev.daemon_threads = True
        cls.port = cls.dev.server_address[1]
        threading.Thread(target=cls.dev.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        bs.shutdown_proxies()
        cls.dev.shutdown()
        cls.dev.server_close()

    def proxy(self, path="/"):
        res = bs.ensure_proxy(f"http://localhost:{self.port}{path}")
        self.assertTrue(res["ok"], res)
        return res["proxy_url"]

    def get(self, url, method="GET", body=None):
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
        conn.request(method, parts.path + (("?" + parts.query) if parts.query else ""), body=body)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_probe_reports_frame_blocking(self):
        res = bs.probe(f"http://localhost:{self.port}/")
        self.assertTrue(res["reachable"])
        self.assertTrue(res["frame_blocked"])
        self.assertIn("DENY", res["reason"])

    def test_probe_refuses_non_loopback(self):
        self.assertFalse(bs.probe("https://example.com/")["checked"])

    def test_proxy_strips_frame_blocking_and_keeps_host(self):
        resp, data = self.get(self.proxy("/"))
        self.assertEqual(resp.status, 200)
        self.assertEqual(data, b"<h1>hello from dev</h1>")
        self.assertIsNone(resp.getheader("X-Frame-Options"))
        self.assertIn("frame-ancestors 'self' http://localhost:*", resp.getheader("Content-Security-Policy"))
        self.assertEqual(_DevServer.seen_hosts[-1], f"localhost:{self.port}")

    def test_proxy_rewrites_redirects_and_forwards_bodies(self):
        base = self.proxy("/")
        resp, _ = self.get(base.rstrip("/") + "/old")
        self.assertEqual(resp.status, 302)
        self.assertTrue(resp.getheader("Location").startswith(base.rstrip("/")), resp.getheader("Location"))
        resp, data = self.get(base, method="POST", body=b"x=1")
        self.assertEqual(data, b"got:x=1")

    def test_proxy_is_reused_per_origin_and_excluded_from_detection(self):
        a = self.proxy("/a")
        b = self.proxy("/b?q=1")
        self.assertEqual(a.rsplit("/", 1)[0], b.rsplit("/", 1)[0])
        self.assertTrue(b.endswith("/b?q=1"))
        from urllib.parse import urlsplit
        self.assertIn(urlsplit(a).port, bs.proxy_ports())

    def test_proxy_tunnels_websocket_upgrade(self):
        from urllib.parse import urlsplit
        p = urlsplit(self.proxy("/"))
        s = socket.create_connection((p.hostname, p.port), timeout=5)
        s.sendall(b"GET /hmr HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = s.recv(1024)
            if not chunk:
                break
            head += chunk
        self.assertIn(b" 101 ", head.split(b"\r\n", 1)[0] + b" ")
        s.sendall(b"ping")
        rest = head.split(b"\r\n\r\n", 1)[1]
        deadline = time.time() + 5
        while b"echo:ping" not in rest and time.time() < deadline:
            chunk = s.recv(1024)
            if not chunk:
                break
            rest += chunk
        s.close()
        self.assertIn(b"echo:ping", rest)

    def test_proxy_refuses_non_loopback_target(self):
        self.assertFalse(bs.ensure_proxy("http://example.com:80/")["ok"])
        self.assertFalse(bs.ensure_proxy("file:///etc/passwd")["ok"])

    def test_unreachable_upstream_is_a_readable_502(self):
        with socket.socket() as tmp:
            tmp.bind(("127.0.0.1", 0))
            dead = tmp.getsockname()[1]
        res = bs.ensure_proxy(f"http://localhost:{dead}/")
        resp, data = self.get(res["proxy_url"])
        self.assertEqual(resp.status, 502)
        self.assertIn(b"not answering", data)


if __name__ == "__main__":
    unittest.main()
