"""Real sockets + OS signals: service stops must finish an accepted write."""
import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import signal
import site
import socket
import subprocess
import sys
import time

import pytest
import server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def running_server(tmp_path):
    script = tmp_path / 'serve.py'
    script.write_text('''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import server
from ccc_server import restart_recovery
state = Path(sys.argv[2])
server._unregister_self = lambda: None
server.voice_shutdown = lambda: None
server._ccc_peer_server_stop = lambda: None
restart_recovery.record_clean_stop = lambda: None
quiesce = server._quiesce_http_for_stop
def stopping(*args, **kwargs):
    (state / 'stopping').touch()
    return quiesce(*args, **kwargs)
server._quiesce_http_for_stop = stopping
class Handler(server.CommandCenterHandler):
    def do_POST(self):
        (state / 'executed').write_text(self.path)
        time.sleep(1.2)
        self.send_json({'saved': True})
    def do_GET(self):
        self.send_json({'ok': True})
    def log_message(self, *args): pass
httpd = server.ThreadedHTTPServer(('127.0.0.1', 0), Handler)
# _serve_http installs the production SIGTERM handler before accepting.
print(httpd.server_port, flush=True)
server._serve_http(httpd)
''')
    # Keep installed dependencies visible when the fixture isolates HOME.
    env = dict(os.environ, PYTHONUSERBASE=site.getuserbase(),
               HOME=str(tmp_path), CCC_EPHEMERAL='1',
               CCC_ONCALL_STATE_DIR=str(tmp_path / 'oncall'),
               CCC_SHIP_GRAPH_DB=str(tmp_path / 'ship.sqlite'),
               CCC_CONV_META_CACHE_FILE=str(tmp_path / 'meta.json'))
    proc = subprocess.Popen([sys.executable, str(script), str(ROOT), str(tmp_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    try:
        # Imports may write diagnostics first.
        for line in proc.stdout:
            if line.strip().isdigit():
                port = int(line)
                break
        else:
            pytest.fail(proc.stderr.read())
        yield proc, port, tmp_path
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()


def wait_file(filename):
    deadline = time.monotonic() + 10
    while not filename.exists():
        assert time.monotonic() < deadline, str(filename)
        time.sleep(.01)


def post(port):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        conn.request('POST', '/save', body=b'{}')
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


@pytest.mark.parametrize('stop_signal', [signal.SIGTERM, signal.SIGINT])
def test_signal_finishes_inflight_write(running_server, stop_signal):
    proc, port, state = running_server
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = pool.submit(post, port)
        wait_file(state / 'executed')
        proc.send_signal(stop_signal)
        wait_file(state / 'stopping')
        assert proc.poll() is None, 'exited while save was still running'
        assert result.result(timeout=10) == (200, {'saved': True})
    assert proc.wait(timeout=10) == 0


def test_late_headers_refused_without_executing_write(running_server):
    proc, port, state = running_server
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = pool.submit(post, port)
        wait_file(state / 'executed')
        # Hold a second accepted connection in header parsing.
        client = socket.create_connection(('127.0.0.1', port), timeout=10)
        try:
            client.sendall(b'POST /must-not-run HTTP/1.1\r\nHost: localhost\r\n')
            time.sleep(.1)
            proc.terminate()
            wait_file(state / 'stopping')
            client.sendall(b'Content-Length: 0\r\n\r\n')
            response = http.client.HTTPResponse(client)
            response.begin()
            assert response.status == 503
            assert response.getheader('X-CCC-Restarting') == '1'
            assert json.loads(response.read())['restarting'] is True
        finally:
            client.close()
        assert result.result(timeout=10) == (200, {'saved': True})
    assert (state / 'executed').read_text() == '/save'
    assert proc.wait(timeout=10) == 0


def test_busy_exec_restart_is_cancelled(monkeypatch):
    monkeypatch.setattr(server, '_HTTPD_SERVER', object())
    monkeypatch.setattr(server, '_drain_inflight_requests', lambda timeout: ['/save'])
    try:
        assert server._quiesce_http_for_restart(.01) is False
        assert not server._ACCEPT_PAUSED.is_set()
    finally:
        server._ACCEPT_PAUSED.clear()
