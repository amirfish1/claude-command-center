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


def test_running_ccc_is_recognised_but_never_reused_by_pick():
    # That CCC may serve another repo; the installer must not bind on top.
    server = _serve({"version": "5.37.0", "code_rev": "abc"})
    try:
        port = server.server_address[1]
        assert _run(port).stdout.strip() == "ccc"
        assert int(_run(port, "--pick").stdout.strip()) > port
    finally:
        server.shutdown()


def test_explicit_bind_host_is_probed():
    port = _free_port()
    assert _run(port, "--host", "").stdout.strip() == "free"
    assert _run(port, "--host", "127.0.0.1", "--pick").stdout.strip() == str(port)


def test_unassigned_host_treated_as_free():
    port = _free_port()
    # RFC 5737 test address not assigned locally raises EADDRNOTAVAIL;
    # should be treated as free (e.g. Tailscale interface still coming up at boot).
    assert _run(port, "--host", "198.51.100.254").stdout.strip() == "free"


def test_bad_usage():
    assert _run("nope").returncode == 2


def test_run_sh_stops_at_a_terminal_when_ccc_already_runs():
    run_sh = (SCRIPT.parent.parent / "run.sh").read_text()
    guard = run_sh.index('[ "$port_state" = "ccc" ] && [ -t 1 ]')
    assert "CCC is already running at http://localhost:$PORT" in run_sh
    # Before the worker block, so a second launch starts no duplicate worker.
    assert guard < run_sh.index('ccc_worker.py" --health')


def test_port_in_use_is_one_line_not_a_traceback(capsys):
    import errno
    import pytest
    import server

    with pytest.raises(SystemExit) as stop:
        server._exit_if_port_in_use(OSError(errno.EADDRINUSE, "in use"), 8090)
    assert stop.value.code == 1
    err = capsys.readouterr().err
    assert "port 8090 is already in use" in err and "PORT=8091" in err
    # Any other bind failure stays the caller's to raise.
    assert server._exit_if_port_in_use(OSError(errno.EACCES, "denied"), 8090) is None
