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


class LaunchdJobPidTests(unittest.TestCase):
    """`launchctl list <label>` parsing: the plist-style dump only carries a
    "PID" key while the job is actively running."""

    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_parses_pid_from_launchctl_list_output(self):
        server = self.server
        stdout = (
            '{\n\t"Label" = "com.github.claude-command-center";\n'
            '\t"PID" = 36700;\n\t"LastExitStatus" = 0;\n}\n'
        )
        with mock.patch.object(server.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=stdout, stderr="")
            self.assertEqual(
                server._launchd_job_pid("com.github.claude-command-center"), 36700,
            )

    def test_none_when_the_job_is_not_loaded(self):
        server = self.server
        with mock.patch.object(server.subprocess, "run") as run:
            run.return_value = mock.Mock(
                returncode=113, stdout="", stderr="Could not find service",
            )
            self.assertIsNone(server._launchd_job_pid("com.github.claude-command-center"))

    def test_none_when_the_job_is_loaded_but_stopped(self):
        """No PID key at all -- loaded, but not currently running."""
        server = self.server
        stdout = '{\n\t"Label" = "com.github.claude-command-center";\n\t"LastExitStatus" = 0;\n}\n'
        with mock.patch.object(server.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=stdout, stderr="")
            self.assertIsNone(server._launchd_job_pid("com.github.claude-command-center"))

    def test_none_on_timeout(self):
        server = self.server
        with mock.patch.object(
            server.subprocess, "run",
            side_effect=subprocess.TimeoutExpired("launchctl", 3),
        ):
            self.assertIsNone(server._launchd_job_pid("com.github.claude-command-center"))

    def test_launchctl_print_pid_parser(self):
        server = self.server
        stdout = "state = running\npid = 36700\nprogram = /usr/bin/python3\n"
        with mock.patch.object(server.platform, "system", return_value="Darwin"), \
             mock.patch.object(server.os, "getuid", return_value=501), \
             mock.patch.object(server.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=stdout, stderr="")
            self.assertEqual(
                server._launchd_print_job_pid("com.github.claude-command-center"),
                36700,
            )
        self.assertEqual(run.call_args[0][0], [
            "launchctl",
            "print",
            "gui/501/com.github.claude-command-center",
        ])


class LaunchdRestartTargetsPidTests(unittest.TestCase):
    """The decision logic that fixes the actual bug: a plist existing on
    disk only means the label is installed, not that it's the process
    currently serving traffic. This is what a manually started dev/
    duplicate instance (CCC_ALLOW_DUPLICATE_REPO=1 on another port) or a
    stray leftover process exposes -- kickstarting the label in that case
    restarts a process nobody is talking to, `returncode == 0` still reads
    as success, and the live process nobody touched keeps serving stale
    code."""

    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_safe_when_nothing_is_running_under_the_label(self):
        """Job not loaded / stopped -- kickstart will start it fresh, no
        other live copy to conflict with."""
        server = self.server
        with mock.patch.object(server, "_launchd_job_pid", return_value=None):
            self.assertTrue(server._launchd_restart_targets_pid("label", 555))
            self.assertTrue(server._launchd_restart_targets_pid("label", None))

    def test_safe_when_the_tracked_pid_matches(self):
        server = self.server
        with mock.patch.object(server, "_launchd_job_pid", return_value=36700):
            self.assertTrue(server._launchd_restart_targets_pid("label", 36700))

    def test_unsafe_when_a_different_process_owns_the_label(self):
        """The exact bug: a dev/duplicate instance (PID 90210) asks to
        restart, but launchd's own bookkeeping for the label points at the
        real dashboard (PID 36700) -- kickstarting it would restart the
        wrong process and leave the caller's stale code running."""
        server = self.server
        with mock.patch.object(server, "_launchd_job_pid", return_value=36700):
            self.assertFalse(server._launchd_restart_targets_pid("label", 90210))

    def test_unsafe_when_something_is_running_but_expected_pid_is_unknown(self):
        server = self.server
        with mock.patch.object(server, "_launchd_job_pid", return_value=36700):
            self.assertFalse(server._launchd_restart_targets_pid("label", None))


class RestartWorkerProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_skips_kickstart_when_a_different_pid_owns_the_worker_label(self):
        """A duplicate/dev worker (pid 4242) must not trust a kickstart that
        would actually target the real service's worker (pid 9999) -- it
        should fall straight to killing its own known pid and respawning,
        never touching the unrelated launchd-owned process."""
        server = self.server
        with mock.patch.object(server, "_launchd_job_pid", return_value=9999), \
             mock.patch.object(server.subprocess, "run") as run, \
             mock.patch.object(server.subprocess, "Popen") as popen, \
             mock.patch.object(server.os, "kill") as kill, \
             mock.patch("builtins.open", mock.mock_open()):
            out = server._restart_worker_process({"pid": 4242})
        run.assert_not_called()
        self.assertEqual(out["via"], "respawn")
        kill.assert_any_call(4242, server.signal.SIGTERM)
        popen.assert_called_once()

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


class ScheduleRestartTests(unittest.TestCase):
    """The dashboard's own in-place restart: kickstart via launchd when
    that's safe to trust, os.execvp self otherwise. Fakes threading.Timer
    to run synchronously so the decision is observable without the real
    0.5s delay."""

    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def _run_schedule_restart(self):
        server = self.server

        class ImmediateTimer:
            def __init__(self, delay, fn):
                self.fn = fn
                self.daemon = False

            def start(self):
                self.fn()

        with mock.patch.object(server.threading, "Timer", ImmediateTimer):
            server._schedule_restart()

    def test_kickstarts_self_when_launchd_owns_this_pid(self):
        server = self.server
        with mock.patch.object(server.sys, "platform", "darwin"), \
             mock.patch.object(server.Path, "is_file", return_value=True), \
             mock.patch.object(server, "_launchd_restart_targets_pid", return_value=True), \
             mock.patch.object(server.subprocess, "run") as run, \
             mock.patch.object(server.os, "execvp") as execvp:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            self._run_schedule_restart()
        run.assert_called_once()
        argv = run.call_args[0][0]
        self.assertEqual(argv[:3], ["launchctl", "kickstart", "-k"])
        execvp.assert_not_called()

    def test_execs_self_when_a_different_pid_owns_the_label(self):
        """A dev/duplicate dashboard instance (CCC_ALLOW_DUPLICATE_REPO=1 on
        another port) must restart ITSELF, not kickstart the real service
        it happens to share a plist label with -- the launchctl call it
        would otherwise trust as "restart done" targets a process nobody
        here is actually watching."""
        server = self.server
        with mock.patch.object(server.sys, "platform", "darwin"), \
             mock.patch.object(server.Path, "is_file", return_value=True), \
             mock.patch.object(server, "_launchd_restart_targets_pid", return_value=False), \
             mock.patch.object(server.subprocess, "run") as run, \
             mock.patch.object(server.os, "execvp") as execvp:
            self._run_schedule_restart()
        run.assert_not_called()
        execvp.assert_called_once()


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
