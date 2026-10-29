"""A Codex app-server that dies at startup must fail fast, not time out.

Failure being guarded: the shared stdio app-server child exited ~40ms after
spawn (a truncated codex install), but the `initialize` waiter still sat out
its full 10s and logged "app-server TIMEOUT ... watching for late arrival",
hiding the crash. The reader's EOF now fails the pending request at once and
logs `app-server EXITED` with the child's stderr tail.
"""
import importlib
import subprocess
import sys
import threading
import time
from unittest import mock

server = importlib.import_module("server")
from ccc_server import codex  # noqa: E402


def test_child_exit_fails_pending_request_immediately(tmp_path):
    log_file = tmp_path / "activity.log"
    stderr_path = tmp_path / "codex-app-server-stderr.log"
    with open(stderr_path, "w") as stderr_log:
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; time.sleep(0.3); "
             "sys.stderr.write('Error: spawn failed errno -88\\n')"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr_log,
            text=True, bufsize=1,
        )
    transport = codex._CodexAppServerTransport("stdio", proc=proc)
    logged = []
    with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_file), \
         mock.patch.object(server, "_log_activity",
                           lambda *a, **k: logged.append(a)):
        reader = threading.Thread(
            target=codex._codex_app_server_reader, args=(transport,), daemon=True,
        )
        reader.start()
        started = time.time()
        result = codex._codex_app_server_request_to_transport(
            transport, "initialize", {}, timeout=10,
        )
        elapsed = time.time() - started
        reader.join(timeout=2)
    assert elapsed < 3, f"waited {elapsed:.1f}s for a dead child"
    assert result["ok"] is False and result.get("exited") is True
    assert result["fallback"] == "queue"
    verbs = [(a[0], a[1]) for a in logged]
    assert ("app-server", "EXITED") in verbs
    assert ("app-server", "TIMEOUT") not in verbs
    detail = next(a[2] for a in logged if a[1] == "EXITED")
    assert "method=initialize" in detail
    assert "stderr=Error: spawn failed errno -88" in detail
    assert not codex._CODEX_APP_SERVER_ORPHANED_WAITERS.get(result.get("id"))


def test_mock_transport_without_flag_still_waits_for_reply():
    transport = mock.Mock()  # Mock attributes are truthy; must not look exited
    result = codex._codex_app_server_request_to_transport(
        transport, "thread/list", {}, timeout=0.2, count_as_inflight=False,
    )
    assert result.get("exited") is None
    assert "timed out" in result["error"]
