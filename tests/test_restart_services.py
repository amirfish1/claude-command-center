"""Restart controls for the dashboard / worker pair.

A committed fix is not a live fix: Python loads code once at process start,
and worker_engines.py lazily `import server`, so the worker keeps its own copy
of server.py's module state. Restarting only the dashboard after a server.py
change therefore looks exactly like the fix not working. Settings used to
offer a dashboard restart and nothing else, so the service that most often
needed restarting could not be restarted from the UI at all.

These cover the mechanics only; launchctl is always mocked so the suite never
kicks a real service.
"""

import importlib
import subprocess
import unittest
from unittest import mock


class RestartWorkerProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_restart_uses_launchd_when_the_service_exists(self):
        server = self.server
        with mock.patch.object(server.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            out = server._restart_worker_process({})
        self.assertTrue(out["restarted"])
        self.assertEqual(out["via"], "launchd")
        argv = run.call_args[0][0]
        self.assertEqual(argv[:3], ["launchctl", "kickstart", "-k"])
        self.assertIn(server._WORKER_LAUNCHD_LABEL, argv[3])

    def test_falls_back_to_respawn_when_there_is_no_launchd_service(self):
        """Homebrew's single service and the DMG app spawn have no worker job."""
        server = self.server
        with mock.patch.object(server.subprocess, "run") as run, \
             mock.patch.object(server.subprocess, "Popen") as popen, \
             mock.patch.object(server.os, "kill"), \
             mock.patch("builtins.open", mock.mock_open()):
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="no such service")
            out = server._restart_worker_process({"pid": 4242})
        self.assertTrue(out["restarted"])
        self.assertEqual(out["via"], "respawn")
        popen.assert_called_once()
        self.assertIn("ccc_worker.py", " ".join(popen.call_args[0][0]))

    def test_launchctl_timeout_does_not_raise(self):
        server = self.server
        with mock.patch.object(
            server.subprocess, "run",
            side_effect=subprocess.TimeoutExpired("launchctl", 10),
        ), mock.patch.object(server.subprocess, "Popen"), \
             mock.patch.object(server.os, "kill"), \
             mock.patch("builtins.open", mock.mock_open()):
            out = server._restart_worker_process({"pid": 1})
        self.assertEqual(out["via"], "respawn")

    def test_stale_worker_check_still_skips_a_current_worker(self):
        """The update path must not restart a worker that is already current."""
        server = self.server
        with mock.patch.object(
            server, "_control_plane_request",
            return_value={"ok": True, "worker": {"server_version": "9.9.9"}},
        ), mock.patch.object(server, "_repo_version_on_disk", return_value="9.9.9"), \
             mock.patch.object(server, "_restart_worker_process") as restart:
            out = server._restart_stale_worker()
        self.assertFalse(out["restarted"])
        self.assertEqual(out["reason"], "current")
        restart.assert_not_called()

    def test_stale_worker_delegates_to_the_shared_restart(self):
        server = self.server
        with mock.patch.object(
            server, "_control_plane_request",
            return_value={"ok": True, "worker": {"server_version": "1.0.0"}},
        ), mock.patch.object(server, "_repo_version_on_disk", return_value="2.0.0"), \
             mock.patch.object(
                 server, "_restart_worker_process",
                 return_value={"restarted": True, "via": "launchd"},
             ) as restart:
            out = server._restart_stale_worker()
        self.assertTrue(out["restarted"])
        restart.assert_called_once()


class RestartRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_restart_all_is_routed_alongside_plain_restart(self):
        """/api/restart/all must share the dashboard restart's safety handoff."""
        import pathlib
        src = pathlib.Path(self.server.__file__).read_text()
        self.assertIn('if path in ("/api/restart", "/api/restart/all"):', src)
        self.assertIn('if path == "/api/restart/worker":', src)

    def test_worker_is_kicked_after_the_session_handoff_not_before(self):
        """adopt/drain need a live worker; kicking it first strands sessions."""
        import pathlib
        src = pathlib.Path(self.server.__file__).read_text()
        block = src[src.index('if path in ("/api/restart", "/api/restart/all"):'):]
        handoff = block.index("_safe_worker_restart_precheck(")
        kick = block.index("worker_outcome = _restart_worker_process() if restart_all else None")
        self.assertLess(
            handoff, kick,
            "the worker restart must come after the safety precheck (which "
            "runs drain.set before returning ok), or the dashboard hands "
            "its sessions to a worker that is already going down",
        )


class SafeWorkerRestartPrecheckTests(unittest.TestCase):
    """/api/restart/worker used to skip this precheck entirely -- straight
    to launchctl kickstart, no drain, no adopt, no check for a Kimi turn
    this dashboard still owned. That asymmetry with /api/restart/all was
    the actual answer to "when is it safe to restart the worker": the safe
    sequence existed, it just wasn't wired to the worker-only button."""

    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_blocks_on_active_kimi_work_without_touching_the_worker(self):
        server = self.server
        with mock.patch.object(
            server, "_dashboard_owned_active_executions",
            return_value=[{"engine": "kimi", "session_id": "s1"}],
        ), mock.patch.object(server, "_control_plane_request") as cpr:
            ok, err, adopted, protected = server._safe_worker_restart_precheck()
        self.assertFalse(ok)
        self.assertIn("Kimi", err["error"])
        self.assertIsNone(adopted)
        self.assertIsNone(protected)
        cpr.assert_not_called()

    def test_happy_path_adopts_then_drains_in_order(self):
        server = self.server
        calls = []

        def fake_cpr(method, params=None, *, engine_timeout=False):
            calls.append(method)
            if method == "engine.adopt":
                return {"ok": True, "available": True, "adopted": 2}
            if method == "drain.set":
                return {"ok": True, "available": True, "queued": 1}
            raise AssertionError(f"unexpected control-plane method {method!r}")

        with mock.patch.object(
            server, "_dashboard_owned_active_executions", return_value=[],
        ), mock.patch.object(server, "_control_plane_request", side_effect=fake_cpr):
            ok, err, adopted, protected = server._safe_worker_restart_precheck()
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(calls, ["engine.adopt", "drain.set"])
        self.assertEqual(adopted["adopted"], 2)
        self.assertEqual(protected["queued"], 1)

    def test_worker_restart_endpoint_waits_for_health_then_reconciles(self):
        """The endpoint itself: precheck -> kickstart -> wait -> reconcile
        -> lift the precheck drain (a leaked "worker-restart:" drain defers
        every later submit forever; nothing else used to release it)."""
        import pathlib
        src = pathlib.Path(self.server.__file__).read_text()
        block = src[src.index('if path == "/api/restart/worker":'):
                     src.index('if path in ("/api/restart", "/api/restart/all"):')]
        precheck = block.index("_safe_worker_restart_precheck(")
        kick = block.index("_restart_worker_process()")
        wait = block.index("_wait_worker_healthy(")
        reconcile = block.index('"work.reconcile"')
        release = block.index('"drain.set"')
        self.assertLess(precheck, kick)
        self.assertLess(kick, wait)
        self.assertLess(wait, reconcile)
        self.assertLess(reconcile, release)


if __name__ == "__main__":
    unittest.main()
