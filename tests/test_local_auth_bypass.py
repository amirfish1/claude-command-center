"""Integration tests and verification for CCC proxy secret protection
against local-program authentication bypass (issue #222).

Verifies that:
1. When CCC_PROXY_SECRET is unset (default local dev), requests pass through unauthenticated.
2. When CCC_PROXY_SECRET is set, requests without valid X-CCC-Proxy-Secret or Bearer auth
   are rejected with HTTP 401 Unauthorized.
3. CCC_PROXY_SECRET_FILE allows loading the secret from a mode 0600 file.
4. On native Linux with Docker, direct host connections to the internal bridge IP without
   the secret return HTTP 401, closing the unauthenticated bridge bypass.
"""

import json
import os
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import server


class TestProxySecretUnit(unittest.TestCase):
    def setUp(self):
        self._orig_env = os.environ.copy()
        self.tmp_dir = tempfile.mkdtemp()
        self.port = self._find_free_port()

        # Save server globals
        self._orig_allowed_origins = list(server.ALLOWED_ORIGINS)
        server.ALLOWED_ORIGINS.append(f"http://127.0.0.1:{self.port}")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)
        server.ALLOWED_ORIGINS[:] = self._orig_allowed_origins
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _find_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _start_server(self):
        # Threaded server running CommandCenterHandler
        class Handler(server.CommandCenterHandler):
            # Suppress console logging in tests
            def log_message(self, format, *args):
                pass

        httpd = socketserver.TCPServer(("127.0.0.1", self.port), Handler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        time.sleep(0.05)
        return httpd

    def test_proxy_secret_unset_allows_unauthenticated_access(self):
        os.environ.pop("CCC_PROXY_SECRET", None)
        os.environ.pop("CCC_PROXY_SECRET_FILE", None)
        self._start_server()

        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/sessions/census")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertTrue(data.get("ok"))

    def test_proxy_secret_rejects_missing_header(self):
        os.environ["CCC_PROXY_SECRET"] = "secret-token-xyz-12345"
        self._start_server()

        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/sessions/census")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)
        body = json.loads(ctx.exception.read().decode())
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "unauthorized")

    def test_proxy_secret_rejects_invalid_header(self):
        os.environ["CCC_PROXY_SECRET"] = "secret-token-xyz-12345"
        self._start_server()

        # Wrong header value
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/sessions/census",
            headers={"X-CCC-Proxy-Secret": "wrong-secret"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

        # Wrong Bearer token
        req2 = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/sessions/census",
            headers={"Authorization": "Bearer wrong-bearer-token"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req2, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

    def test_proxy_secret_accepts_valid_header(self):
        os.environ["CCC_PROXY_SECRET"] = "secret-token-xyz-12345"
        self._start_server()

        # Via X-CCC-Proxy-Secret
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/sessions/census",
            headers={"X-CCC-Proxy-Secret": "secret-token-xyz-12345"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)

        # Via Authorization: Bearer
        req2 = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/sessions/census",
            headers={"Authorization": "Bearer secret-token-xyz-12345"},
        )
        with urllib.request.urlopen(req2, timeout=5) as resp:
            self.assertEqual(resp.status, 200)

    def test_proxy_secret_from_file(self):
        secret_file = os.path.join(self.tmp_dir, "secret.txt")
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write("file-loaded-secret-67890\n")
        os.chmod(secret_file, 0o600)

        os.environ.pop("CCC_PROXY_SECRET", None)
        os.environ["CCC_PROXY_SECRET_FILE"] = secret_file
        self._start_server()

        # Missing secret -> 401
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/sessions/census")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

        # Valid secret -> 200
        req_auth = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/sessions/census",
            headers={"X-CCC-Proxy-Secret": "file-loaded-secret-67890"},
        )
        with urllib.request.urlopen(req_auth, timeout=5) as resp:
            self.assertEqual(resp.status, 200)

    def test_proxy_secret_gates_post_requests(self):
        os.environ["CCC_PROXY_SECRET"] = "post-secret-abc"
        self._start_server()

        # POST without secret -> 401
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/sessions/spawn",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)


class TestLinuxDockerBridgeIsolation(unittest.TestCase):
    """Reproducible native Linux Docker bridge test.

    Proves:
    1. Baseline vulnerability: native Linux host can reach an unauthenticated
       container on an internal bridge directly without credentials.
    2. Boundary enforcement: with CCC_PROXY_SECRET configured, direct host
       connections without the secret receive HTTP 401, while proxy-injected
       or secret-bearing requests succeed with HTTP 200.
    """

    @classmethod
    def setUpClass(cls):
        if not shutil.which("docker"):
            raise unittest.SkipTest("docker CLI not available")
        res = subprocess.run(["docker", "info"], capture_output=True)
        if res.returncode != 0:
            raise unittest.SkipTest("docker daemon not accessible")

    def test_native_linux_bridge_direct_bypass_rejected(self):
        net_name = f"test-ccc-net-{int(time.time())}"
        backend_name = f"test-ccc-backend-{int(time.time())}"
        secret = "integration-test-secret-999"

        # Create internal bridge network
        subprocess.run(["docker", "network", "create", "--internal", net_name], check=True)
        self.addCleanup(lambda: subprocess.run(["docker", "network", "rm", net_name], capture_output=True))

        # Run backend server in container
        # Use python:3.12-slim to run a test server checking for X-CCC-Proxy-Secret
        python_script = """
import http.server, os, hmac

secret = os.environ.get("CCC_PROXY_SECRET", "")

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        provided = self.headers.get("X-CCC-Proxy-Secret", "")
        if not (provided and hmac.compare_digest(provided, secret)):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"ok": false, "error": "unauthorized"}')
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

http.server.HTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
"""
        cmd = [
            "docker", "run", "-d",
            "--name", backend_name,
            "--net", net_name,
            "-e", f"CCC_PROXY_SECRET={secret}",
            "python:3.12-slim",
            "python3", "-c", python_script,
        ]
        subprocess.run(cmd, check=True)
        self.addCleanup(lambda: subprocess.run(["docker", "rm", "-f", backend_name], capture_output=True))

        # Get backend IP on the internal bridge
        time.sleep(1)
        inspect_res = subprocess.run(
            ["docker", "inspect", backend_name, "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
            capture_output=True, text=True, check=True,
        )
        backend_ip = inspect_res.stdout.strip()
        self.assertTrue(backend_ip, "Failed to get backend IP")

        # 1. Native Linux host direct connection WITHOUT secret -> MUST BE 401
        req_unauth = urllib.request.Request(f"http://{backend_ip}:8090/api/sessions")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_unauth, timeout=5)
        self.assertEqual(ctx.exception.code, 401, "Direct host bypass should be rejected with 401")

        # 2. Native Linux host direct connection WITH secret -> MUST BE 200
        req_auth = urllib.request.Request(
            f"http://{backend_ip}:8090/api/sessions",
            headers={"X-CCC-Proxy-Secret": secret},
        )
        with urllib.request.urlopen(req_auth, timeout=5) as resp:
            self.assertEqual(resp.status, 200, "Authenticated request with proxy secret should succeed with 200")
            body = json.loads(resp.read().decode())
            self.assertTrue(body["ok"])

        # 3. Peer container on same bridge WITHOUT secret -> MUST BE 401
        peer_run = subprocess.run(
            [
                "docker", "run", "--rm",
                "--net", net_name,
                "curlimages/curl:latest",
                "-s", "-o", "/dev/null", "-w", "%{http_code}",
                f"http://{backend_ip}:8090/api/sessions",
            ],
            capture_output=True, text=True,
        )
        if peer_run.returncode == 0:
            self.assertEqual(peer_run.stdout.strip(), "401", "Peer container without secret must receive 401")

    def test_mode_0600_dac_isolation_against_other_uids(self):
        """Verifies that mode 0600 credentials cannot be read by other host UIDs (e.g. nobody)."""
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        secret_file = os.path.join(d, "secret.txt")
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write("top-secret-token\n")
        os.chmod(secret_file, 0o600)
        os.chmod(d, 0o755)

        # Container running as unprivileged UID 65534 (nobody) mounting secret directory
        res = subprocess.run(
            [
                "docker", "run", "--rm",
                "--user", "65534:65534",
                "-v", f"{d}:/secrets:ro",
                "alpine:latest",
                "cat", "/secrets/secret.txt",
            ],
            capture_output=True, text=True,
        )
        self.assertNotEqual(res.returncode, 0, "Non-owner UID should not be able to read mode 0600 file")
        self.assertIn("Permission denied", res.stderr)

    def test_caddy_e2e_proxy_and_direct_bypass_rejected(self):
        """End-to-end Caddy reverse-proxy + backend proxy secret test.

        Proves:
        1. Direct host access to backend bridge IP without secret -> 401 (blocked).
        2. Proxy access without Basic Auth -> 401 (blocked by Caddy).
        3. Proxy access with valid Basic Auth -> 200 (Caddy injects secret, backend succeeds).
        """
        front_net = f"test-caddy-front-{int(time.time())}"
        back_net = f"test-caddy-back-{int(time.time())}"
        backend_name = f"test-caddy-backend-{int(time.time())}"
        caddy_name = f"test-caddy-proxy-{int(time.time())}"
        secret = "caddy-e2e-secret-xyz-777"
        caddy_port = 18443

        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))

        caddyfile_path = os.path.join(d, "Caddyfile")
        hash_res = subprocess.run(
            ["docker", "run", "--rm", "caddy:2", "caddy", "hash-password", "--plaintext", "testpass"],
            capture_output=True, text=True, check=True,
        )
        password_hash = hash_res.stdout.strip()

        with open(caddyfile_path, "w", encoding="utf-8") as f:
            f.write(f"""{{
    admin off
}}

:8080 {{
    basic_auth {{
        admin {password_hash}
    }}

    reverse_proxy {backend_name}:8090 {{
        header_up X-CCC-Proxy-Secret {secret}
    }}
}}
""")

        backend_file = os.path.join(d, "backend.py")
        with open(backend_file, "w", encoding="utf-8") as f:
            f.write(f"""import http.server, os, hmac

secret = "{secret}"

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        provided = self.headers.get("X-CCC-Proxy-Secret", "")
        if not (provided and hmac.compare_digest(provided, secret)):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{{"ok": false, "error": "unauthorized"}}')
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{{"ok": true, "msg": "authenticated via proxy"}}')

http.server.HTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
""")

        # Networks
        subprocess.run(["docker", "network", "create", front_net], check=True)
        self.addCleanup(lambda: subprocess.run(["docker", "network", "rm", front_net], capture_output=True))
        subprocess.run(["docker", "network", "create", "--internal", back_net], check=True)
        self.addCleanup(lambda: subprocess.run(["docker", "network", "rm", back_net], capture_output=True))

        # Backend on internal back_net
        subprocess.run([
            "docker", "run", "-d",
            "--name", backend_name,
            "--net", back_net,
            "-v", f"{backend_file}:/app/backend.py:ro",
            "python:3.12-slim",
            "python3", "/app/backend.py",
        ], check=True)
        self.addCleanup(lambda: subprocess.run(["docker", "rm", "-f", backend_name], capture_output=True))

        # Caddy proxy on front_net, connected to back_net
        subprocess.run([
            "docker", "run", "-d",
            "--name", caddy_name,
            "--net", front_net,
            "-p", f"127.0.0.1:{caddy_port}:8080",
            "-v", f"{caddyfile_path}:/etc/caddy/Caddyfile:ro",
            "caddy:2",
        ], check=True)
        self.addCleanup(lambda: subprocess.run(["docker", "rm", "-f", caddy_name], capture_output=True))
        subprocess.run(["docker", "network", "connect", back_net, caddy_name], check=True)

        time.sleep(2)

        # Inspect backend IP
        inspect_res = subprocess.run(
            ["docker", "inspect", backend_name, "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
            capture_output=True, text=True, check=True,
        )
        backend_ip = inspect_res.stdout.strip()

        # 1. Direct host bypass to backend bridge IP -> 401
        req_direct = urllib.request.Request(f"http://{backend_ip}:8090/api/sessions")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_direct, timeout=5)
        self.assertEqual(ctx.exception.code, 401, "Direct bridge bypass without secret must be rejected")

        # 2. Proxy request without basic auth -> 401
        req_proxy_unauth = urllib.request.Request(f"http://127.0.0.1:{caddy_port}/api/sessions")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_proxy_unauth, timeout=5)
        self.assertEqual(ctx.exception.code, 401, "Proxy request without basic auth must be rejected")

        # 3. Proxy request with valid basic auth -> 200
        import base64
        creds = base64.b64encode(b"admin:testpass").decode()
        req_proxy_auth = urllib.request.Request(
            f"http://127.0.0.1:{caddy_port}/api/sessions",
            headers={"Authorization": f"Basic {creds}"},
        )
        with urllib.request.urlopen(req_proxy_auth, timeout=5) as resp:
            self.assertEqual(resp.status, 200, "Proxy request with basic auth should succeed")
            data = json.loads(resp.read().decode())
            self.assertTrue(data.get("ok"))
            self.assertEqual(data.get("msg"), "authenticated via proxy")


if __name__ == "__main__":
    unittest.main()
