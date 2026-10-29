"""A live same-repo duplicate must produce a "kill it?" page, not a crash loop.

2026-10-18: a verification server started with CCC_ALLOW_DUPLICATE_REPO=1 on
:18095 registered itself, so ccc.service refused to start and restarted 35
times. Intentional duplicates must not block the primary, and an accidental
one must be killable from the browser the operator is already looking at.
"""
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer
from unittest import mock

import server  # noqa: F401  (initialises ccc_server.core)
from ccc_server import core as _core
from ccc_server import fleet_jobs


def _entry(pid, **extra):
    return {
        "pid": pid,
        "port": 18095,
        "install_path": str(_core.CCC_ROOT),
        "repo_common_dir": fleet_jobs._git_common_dir(_core.CCC_ROOT),
        "started_at": "2026-10-18T16:40:19+00:00",
        **extra,
    }


def _fake_server(name="python3 server.py"):
    return subprocess.Popen(["bash", "-c", f'exec -a "{name}" sleep 60'], cwd=str(_core.CCC_ROOT))


def test_identity_requires_this_repos_server_py():
    ok = _fake_server()
    foreign = _fake_server("python3 other.py")
    try:
        time.sleep(0.2)
        stamp = fleet_jobs._pid_start_stamp(ok.pid)
        assert fleet_jobs._is_this_repos_server(ok.pid, _entry(ok.pid), stamp)
        assert not fleet_jobs._is_this_repos_server(ok.pid, _entry(ok.pid), "Thu Jan  1 00:00:00 1970")
        assert not fleet_jobs._is_this_repos_server(foreign.pid, _entry(foreign.pid))
        assert not fleet_jobs._is_this_repos_server(ok.pid, _entry(ok.pid, repo_common_dir="/elsewhere/.git"))
        assert not fleet_jobs._is_this_repos_server(ok.pid, _entry(ok.pid, install_path="/tmp/not-this-checkout"))
    finally:
        ok.kill()
        foreign.kill()


def test_gate_request_policy():
    allow = fleet_jobs._gate_request_allowed
    assert allow({"Host": "127.0.0.1:8090"})
    assert allow({"Host": "localhost:8090", "Origin": "http://localhost:3000"})
    assert not allow({"Host": "evil.example:8090"})
    assert not allow({"Host": "127.0.0.1:8090", "Origin": "https://evil.example"})
    assert not allow({"Host": "127.0.0.1:8090", "Sec-Fetch-Site": "cross-site"})
    assert not allow({"Host": "127.0.0.1:8090", "X-Forwarded-For": "100.64.0.2"})
    assert not allow({"Host": "node.tail1234.ts.net"})


def test_gate_rejects_foreign_origin_even_with_valid_token():
    other = _fake_server()
    srv = HTTPServer(("127.0.0.1", 0), mock.Mock())
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        time.sleep(0.2)
        with mock.patch.object(fleet_jobs, "_other_instance_responding",
                               side_effect=lambda p: other.poll() is None):
            threading.Thread(
                target=lambda: fleet_jobs._serve_duplicate_gate(srv, _entry(other.pid), object),
                daemon=True).start()
            page = urllib.request.urlopen(url, timeout=5).read().decode()
            token = page.split('name=token value="')[1].split('"')[0]
            body = urllib.parse.urlencode({"token": token}).encode()
            for hdrs in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}):
                req = urllib.request.Request(url + "/kill", body, method="POST", headers=hdrs)
                try:
                    urllib.request.urlopen(req, timeout=5)
                    assert False, "foreign request accepted"
                except urllib.error.HTTPError as e:
                    assert e.code == 403
            assert other.poll() is None
    finally:
        other.kill()
        srv.server_close()


def test_allow_duplicate_entry_never_blocks_primary():
    env = {k: v for k, v in os.environ.items()
           if k not in ("CCC_EPHEMERAL", "CCC_ALLOW_DUPLICATE_REPO")}
    with mock.patch.dict(os.environ, env, clear=True), \
         mock.patch.object(fleet_jobs, "_read_registry_pruned",
                           return_value=[_entry(os.getpid() + 1, allow_duplicate=True)]), \
         mock.patch.object(fleet_jobs, "_pid_command", return_value="python3 server.py"), \
         mock.patch.object(fleet_jobs, "_other_instance_responding", return_value=True):
        fleet_jobs._check_duplicate_repo_instance()  # must not SystemExit


def test_gate_serves_page_and_kills_only_with_token():
    other = _fake_server()
    srv = HTTPServer(("127.0.0.1", 0), mock.Mock())
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    done = threading.Event()
    try:
        time.sleep(0.2)
        with mock.patch.object(fleet_jobs, "_other_instance_responding",
                               side_effect=lambda p: other.poll() is None):
            t = threading.Thread(
                target=lambda: (fleet_jobs._serve_duplicate_gate(srv, _entry(other.pid), object), done.set()),
                daemon=True)
            t.start()
            page = urllib.request.urlopen(url, timeout=5).read().decode()
            assert "second CCC server is up" in page and str(other.pid) in page
            token = page.split('name=token value="')[1].split('"')[0]

            bad = urllib.request.Request(url + "/kill", b"token=nope", method="POST")
            try:
                urllib.request.urlopen(bad, timeout=5)
                assert False, "bad token accepted"
            except urllib.error.HTTPError as e:
                assert e.code == 403
            assert other.poll() is None

            good = urllib.request.Request(
                url + "/kill", urllib.parse.urlencode({"token": token}).encode(), method="POST")
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None
            try:
                urllib.request.build_opener(_NoRedirect).open(good, timeout=10)
            except urllib.error.HTTPError as e:
                assert e.code == 303
            assert done.wait(10), "gate did not release startup after the kill"
            assert other.poll() is not None
    finally:
        other.kill()
        srv.server_close()
