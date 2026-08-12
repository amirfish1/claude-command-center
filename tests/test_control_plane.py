import ast
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import unittest
from unittest import mock

from ccc_worker import WorkerRuntime, WorkerServer, _release_stale_restart_drain
from control_plane import ControlPlaneClient, WorkLedger
from worker_engines import EngineHost


class TestWorkLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.ledger = WorkLedger(self.root / "control-plane.sqlite3")

    @unittest.skipUnless(
        os.path.isdir("/dev/fd"), "needs /dev/fd to count descriptors"
    )
    def test_ledger_operations_do_not_leak_descriptors(self):
        """Every ledger call must close its connection.

        `with sqlite3.connect(...)` only ends the transaction. Leaking handles
        starved the worker of file descriptors under launchd's 256 limit, after
        which it accepted connections and closed them unread.
        """
        def open_fds():
            return len(os.listdir("/dev/fd"))

        for index in range(20):
            self.ledger.submit(
                engine="claude",
                idempotency_key=f"warmup-{index}",
                session_id="session-fd",
                payload={"prompt": "warmup"},
            )
        baseline = open_fds()
        for index in range(60):
            self.ledger.submit(
                engine="claude",
                idempotency_key=f"leak-{index}",
                session_id="session-fd",
                payload={"prompt": "leak check"},
            )
            self.ledger.summary()
            self.ledger.drain_state()
        self.assertLessEqual(
            open_fds() - baseline, 4,
            "ledger connections are not being closed",
        )

    def test_idempotent_submission_and_parent_child_graph(self):
        parent, created = self.ledger.submit(
            engine="claude",
            idempotency_key="parent-dispatch",
            session_id="session-parent",
            payload={"prompt": "Coordinate the work"},
        )
        self.assertTrue(created)
        duplicate, created = self.ledger.submit(
            engine="claude",
            idempotency_key="parent-dispatch",
            session_id="session-parent",
            payload={"prompt": "This must not replace the first payload"},
        )
        self.assertFalse(created)
        self.assertEqual(duplicate["id"], parent["id"])
        self.assertEqual(duplicate["payload"]["prompt"], "Coordinate the work")
        self.assertEqual(len(parent["prompt_hash"]), 64)

        child, created = self.ledger.submit(
            engine="codex",
            idempotency_key="child-dispatch",
            parent_id=parent["id"],
            session_id="session-child",
            payload={"prompt": "Implement one bounded part"},
        )
        self.assertTrue(created)
        graph = self.ledger.graph(parent["id"])
        self.assertEqual(
            [(item["id"], item["depth"]) for item in graph["items"]],
            [(parent["id"], 0), (child["id"], 1)],
        )
        self.assertEqual(graph["edges"][0]["parent_id"], parent["id"])
        self.assertEqual(graph["edges"][0]["child_id"], child["id"])

    def test_transition_lease_and_worker_recovery_are_conservative(self):
        item, _ = self.ledger.submit(
            engine="kimi",
            idempotency_key="turn-1",
            payload={"prompt": "Perform an external action"},
        )
        dispatching = self.ledger.transition(
            item["id"], "dispatching",
            owner_epoch="old-worker",
            lease_seconds=30,
        )
        self.assertEqual(dispatching["attempt"], 1)
        running = self.ledger.transition(
            item["id"], "running",
            owner_epoch="old-worker",
            lease_seconds=30,
        )
        self.assertGreater(running["lease_expires_at"], time.time())

        recovered = self.ledger.recover_orphaned_running("new-worker")
        self.assertEqual(recovered, [item["id"]])
        uncertain = self.ledger.get(item["id"])
        self.assertEqual(uncertain["state"], "uncertain")
        self.assertIn("worker restarted", uncertain["last_error"])
        with self.assertRaises(ValueError):
            self.ledger.transition(item["id"], "running")

    def test_drain_is_durable(self):
        enabled = self.ledger.set_drain(True, "dashboard upgrade")
        self.assertTrue(enabled["enabled"])
        reopened = WorkLedger(self.root / "control-plane.sqlite3")
        self.assertEqual(reopened.drain_state()["reason"], "dashboard upgrade")
        self.assertEqual(
            (self.root / "control-plane.sqlite3").stat().st_mode & 0o777,
            0o600,
        )


class TestWorkerIPC(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.socket_path = self.root / "worker.sock"
        self.token_path = self.root / "worker.token"
        self.token_path.write_text("a" * 64 + "\n")
        os.chmod(self.token_path, 0o600)
        runtime = WorkerRuntime(
            ledger=WorkLedger(self.root / "ledger.sqlite3"),
            token="a" * 64,
        )
        self.server = WorkerServer(self.socket_path, runtime)
        os.chmod(self.socket_path, 0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = ControlPlaneClient(
            path=self.socket_path,
            token_file=self.token_path,
        )

    def test_health_submit_drain_and_transition(self):
        health = self.client.request("health")
        self.assertTrue(health["ok"])
        self.assertEqual(health["active"], 0)
        self.assertIsInstance(health["worker"]["started_at"], float)
        self.assertEqual(
            self.client.request("health")["worker"]["started_at"],
            health["worker"]["started_at"],
        )
        self.assertIn(
            "engine-execution-v1",
            health["worker"]["capabilities"],
        )
        # run.sh's ensure_worker_current gate keys off this field: present and
        # null when the worker never imported server (nothing stale), or the
        # loaded module's __version__ once it has. Test ordering may import
        # server into this process, so mirror the same sys.modules lookup.
        self.assertIn("server_version", health["worker"])
        import sys as _sys
        expected_version = getattr(_sys.modules.get("server"), "__version__", None)
        self.assertEqual(health["worker"]["server_version"], expected_version)

        drained = self.client.request("drain.set", {
            "enabled": True,
            "reason": "test restart",
        })
        self.assertTrue(drained["drain"]["enabled"])
        submitted = self.client.request("work.submit", {
            "engine": "claude",
            "idempotency_key": "ipc-turn",
            "payload": {"prompt": "Wait safely"},
        })
        self.assertTrue(submitted["ok"])
        self.assertTrue(submitted["deferred"])
        self.assertEqual(submitted["work"]["state"], "queued")

        self.client.request("drain.set", {"enabled": False})
        work_id = submitted["work"]["id"]
        running = self.client.request("work.transition", {
            "work_id": work_id,
            "new_state": "dispatching",
            "owner_epoch": health["worker"]["epoch"],
            "lease_seconds": 30,
        })
        self.assertTrue(running["ok"])
        self.assertEqual(running["work"]["attempt"], 1)

    def test_bad_token_is_rejected(self):
        self.token_path.write_text("b" * 64 + "\n")
        response = self.client.request("health")
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "unauthorized")

    def test_malformed_reply_after_send_is_ambiguous(self):
        malformed_path = self.root / "malformed.sock"
        raw_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw_server.bind(str(malformed_path))
        raw_server.listen(1)
        self.addCleanup(raw_server.close)

        def respond():
            connection, _ = raw_server.accept()
            with connection:
                connection.recv(65536)
                connection.sendall(b"not-json\\n")

        threading.Thread(target=respond, daemon=True).start()
        response = ControlPlaneClient(
            path=malformed_path,
            token_file=self.token_path,
        ).request("engine.execute", {"idempotency_key": "one-action"})
        self.assertFalse(response["ok"])
        self.assertTrue(response["available"])
        self.assertTrue(response["ambiguous"])

    def test_dashboard_http_proxy_preserves_json_contract(self):
        import server

        old_client = server._CONTROL_PLANE_CLIENT
        server._CONTROL_PLANE_CLIENT = self.client
        self.addCleanup(setattr, server, "_CONTROL_PLANE_CLIENT", old_client)
        httpd = server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), server.CommandCenterHandler
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = "http://127.0.0.1:%d" % httpd.server_address[1]

        with urllib.request.urlopen(base + "/api/control-plane/status") as response:
            health = json.load(response)
        self.assertTrue(health["ok"])
        request = urllib.request.Request(
            base + "/api/control-plane/drain",
            data=json.dumps({
                "enabled": True, "reason": "HTTP compatibility test",
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            drained = json.load(response)
        self.assertTrue(drained["drain"]["enabled"])


class TestWorkerServiceDefinition(unittest.TestCase):
    def test_dashboard_and_worker_are_separate_service_units(self):
        source = pathlib.Path("run.sh").read_text(encoding="utf-8")
        self.assertIn('WORKER_SYSTEMD_UNIT_NAME="ccc-worker.service"', source)
        self.assertIn("ExecStart=/usr/bin/env python3 $HERE/ccc_worker.py", source)
        self.assertIn("Wants=network-online.target $WORKER_SYSTEMD_UNIT_NAME", source)
        self.assertIn('WORKER_PLIST_LABEL="com.github.claude-command-center.worker"', source)
        # Dashboard upgrades must not kickstart an already-running worker.
        self.assertIn(
            'if launchctl print "$(worker_service_target)" >/dev/null 2>&1; then',
            source,
        )
        self.assertIn('"engine-execution-v1" in capabilities', source)
        self.assertIn('kill "$existing_worker_pid"', source)
        self.assertIn("Never roll an older worker with unresolved work.", source)

    def test_restart_handler_has_no_function_local_uuid_shadow(self):
        source = pathlib.Path("server.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        handler = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "do_POST"
        )
        local_uuid_imports = [
            node for node in ast.walk(handler)
            if isinstance(node, ast.Import)
            and any(
                alias.name == "uuid" and alias.asname in (None, "uuid")
                for alias in node.names
            )
        ]
        self.assertEqual(local_uuid_imports, [])
        self.assertIn('restart_id = str(uuid.uuid4())', source)
        self.assertIn('"engine.adopt", {}, engine_timeout=True', source)
        self.assertIn("if active_kimi:", source)
        self.assertIn('"engine-execution-v1" in worker_capabilities', source)

    def test_restart_safety_detects_dashboard_owned_work(self):
        import server

        old_state = server._ACP_SESSION_STATE
        old_spawned = server._spawned_sessions
        old_poll = server._poll_spawn_entry
        self.addCleanup(setattr, server, "_ACP_SESSION_STATE", old_state)
        self.addCleanup(setattr, server, "_spawned_sessions", old_spawned)
        self.addCleanup(setattr, server, "_poll_spawn_entry", old_poll)
        server._ACP_SESSION_STATE = {
            "kimi": {
                "kimi-active": {"status": "active"},
                "kimi-idle": {"status": "idle"},
            },
        }
        server._spawned_sessions = [{
            "engine": "claude",
            "session_id": "claude-active",
            "pid": 123,
        }]
        server._poll_spawn_entry = lambda _entry: None

        active = server._dashboard_owned_active_executions()

        self.assertEqual(
            {(item["engine"], item["session_id"]) for item in active},
            {("kimi", "kimi-active"), ("claude", "claude-active")},
        )

    def test_older_worker_rejection_uses_safe_legacy_fallback(self):
        import server

        class OlderWorker:
            @staticmethod
            def request(_method, _params):
                return {
                    "ok": False,
                    "available": True,
                    "code": "method_not_found",
                }

        old_client = server._CONTROL_PLANE_ENGINE_CLIENT
        self.addCleanup(
            setattr, server, "_CONTROL_PLANE_ENGINE_CLIENT", old_client
        )
        server._CONTROL_PLANE_ENGINE_CLIENT = OlderWorker()

        self.assertIsNone(server._control_plane_engine_call(
            "claude",
            "inject",
            {"session_id": "legacy-session", "text": "continue"},
            idempotency_key="legacy-action",
        ))

    def test_header_and_maintenance_expose_both_service_states(self):
        html = pathlib.Path("static/index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path("static/app.js").read_text(encoding="utf-8")

        self.assertIn('id="cccWorkerBadge"', html)
        self.assertIn('id="watchtowerServiceStatus"', html)
        self.assertIn('id="watchtowerServiceActionBtn"', html)
        self.assertIn("openMaintenanceSettings", app_js)
        self.assertIn("'/api/control-plane/start'", app_js)
        self.assertIn("'/api/watchtower/service/status'", app_js)

    def test_offline_worker_can_be_started_from_maintenance(self):
        import server

        unavailable = {"ok": False, "available": False}
        healthy = {
            "ok": True,
            "worker": {
                "pid": 77,
                "capabilities": ["engine-execution-v1"],
            },
        }

        class FakeProcess:
            pid = 77

            @staticmethod
            def poll():
                return None

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            server, "COMMAND_CENTER_STATE_DIR", pathlib.Path(temp_dir)
        ), mock.patch.object(
            server, "_control_plane_request",
            side_effect=[unavailable, healthy],
        ), mock.patch.object(
            server.subprocess, "Popen", return_value=FakeProcess()
        ) as popen:
            result = server._start_control_plane_worker()

        self.assertTrue(result["ok"])
        self.assertTrue(result["started"])
        popen.assert_called_once()

    def test_wedged_worker_is_retired_so_a_new_one_can_bind(self):
        """A reachable-but-unhealthy worker must be stopped, not worked around.

        Without this the replacement worker exits with "already running" and
        Maintenance reports "did not become ready" on every attempt.
        """
        import server

        wedged = {"ok": False, "available": True, "ambiguous": True}
        healthy = {"ok": True, "worker": {"pid": 78}}

        class FakeProcess:
            pid = 78

            @staticmethod
            def poll():
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            pidfile = root / "worker.pid"
            pidfile.write_text("4242\n", encoding="utf-8")
            sock = root / "worker.sock"
            sock.write_text("", encoding="utf-8")
            killed = []

            def fake_kill(pid, sig):
                killed.append((pid, sig))
                raise ProcessLookupError  # already gone after SIGTERM

            with mock.patch.object(
                server, "COMMAND_CENTER_STATE_DIR", root
            ), mock.patch.object(
                server, "worker_pid_path", return_value=pidfile
            ), mock.patch.object(
                server, "socket_path", return_value=sock
            ), mock.patch.object(
                server, "_control_plane_request",
                side_effect=[wedged, healthy],
            ), mock.patch.object(
                server.os, "kill", side_effect=fake_kill
            ), mock.patch.object(
                server.subprocess, "Popen", return_value=FakeProcess()
            ):
                result = server._start_control_plane_worker()

            self.assertTrue(result["ok"])
            self.assertEqual(killed[0], (4242, signal.SIGTERM))
            self.assertFalse(sock.exists(), "stale socket must be cleared")


class TestWatchTowerServiceControl(unittest.TestCase):
    def test_status_verifies_daemon_and_discovers_custom_api_port(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            pid_file = pathlib.Path(temp_dir, "daemon.pid")
            pid_file.write_text(str(os.getpid()), encoding="utf-8")
            argv = [
                sys.executable, "-m", "watchtower.cli", "start",
                "--foreground", "--host", "127.0.0.1", "--port", "8788",
            ]
            with mock.patch.object(
                server, "_watchtower_daemon_pid_path", return_value=pid_file
            ), mock.patch.object(
                server, "_watchtower_process_argv", return_value=argv
            ), mock.patch.object(
                server, "_watchtower_api_up", return_value=True
            ), mock.patch.object(
                server.shutil, "which", return_value="/test/bin/wt"
            ):
                status = server._watchtower_service_status()

        self.assertTrue(status["running"])
        self.assertTrue(status["command_verified"])
        self.assertTrue(status["api_ok"])
        self.assertEqual(status["port"], 8788)
        self.assertEqual(status["url"], "http://127.0.0.1:8788")

    def test_status_rejects_reused_pid(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            pid_file = pathlib.Path(temp_dir, "daemon.pid")
            pid_file.write_text(str(os.getpid()), encoding="utf-8")
            with mock.patch.object(
                server, "_watchtower_daemon_pid_path", return_value=pid_file
            ), mock.patch.object(
                server, "_watchtower_process_argv",
                return_value=["/usr/bin/sleep", "100"],
            ):
                status = server._watchtower_service_status()

        self.assertFalse(status["running"])
        self.assertTrue(status["pid_reused"])

    def test_restart_preserves_manual_daemon_options(self):
        import server

        before = {
            "ok": True,
            "installed": True,
            "running": True,
            "pid": 42,
            "command_verified": True,
        }
        stopped = {"ok": True, "running": False}
        online = {
            "ok": True,
            "installed": True,
            "running": True,
            "pid": 43,
            "command_verified": True,
            "api_ok": True,
        }
        argv = [
            sys.executable, "-m", "watchtower.cli", "start", "--foreground",
            "--interval", "30", "--engine", "claude", "--auto-spawn",
            "--host", "127.0.0.1", "--port", "8788",
        ]
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            server, "_watchtower_service_status",
            side_effect=[before, stopped, stopped, online, online],
        ), mock.patch.object(
            server, "_watchtower_process_argv", return_value=argv
        ), mock.patch.object(
            server.shutil, "which", return_value="/test/bin/wt"
        ), mock.patch.object(
            server.platform, "system", return_value="Linux"
        ), mock.patch.object(
            server.subprocess, "run", return_value=completed
        ) as run:
            result = server._watchtower_service_action("restart")

        self.assertTrue(result["ok"])
        self.assertEqual(run.call_args_list[0].args[0], ["/test/bin/wt", "stop"])
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/test/bin/wt", "start",
                "--interval", "30",
                "--engine", "claude",
                "--auto-spawn",
                "--host", "127.0.0.1",
                "--port", "8788",
            ],
        )


class TestReleaseStaleRestartDrain(unittest.TestCase):
    """Worker boot lifts "worker-restart:" drains the dashboard leaked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ledger = WorkLedger(pathlib.Path(self.tmp.name, "ledger.sqlite3"))
        self.runtime = WorkerRuntime(ledger=ledger, token="b" * 64)

    def test_lifts_worker_restart_drain(self):
        self.runtime.ledger.set_drain(True, "worker-restart:some-uuid")
        self.assertTrue(_release_stale_restart_drain(self.runtime))
        self.assertFalse(self.runtime.ledger.drain_state().get("enabled"))

    def test_leaves_other_drains_alone(self):
        for reason in ("dashboard-restart:some-uuid", "dashboard upgrade", ""):
            self.runtime.ledger.set_drain(True, reason)
            self.assertFalse(_release_stale_restart_drain(self.runtime))
            self.assertTrue(self.runtime.ledger.drain_state().get("enabled"))
            self.runtime.ledger.set_drain(False, "test reset")

    def test_noop_when_not_draining(self):
        self.assertFalse(_release_stale_restart_drain(self.runtime))


class TestEngineHost(unittest.TestCase):
    class FakeLegacy:
        def __init__(self):
            self.config_calls = 0
            self._spawned_sessions = []
            self.reattach_calls = 0
            self.kimi_attach_calls = []
            self.inject_calls = []

        def _acp_set_config(self, harness, sid, config_id, value):
            self.config_calls += 1
            return {
                "ok": True,
                "harness": harness,
                "session_id": sid,
                "config_id": config_id,
                "value": value,
            }

        def _inject_text_into_session(self, session_id, text, **kwargs):
            self.inject_calls.append((session_id, text, kwargs))
            return {
                "ok": True,
                "queued": True,
                "via": "durable-test-queue",
                "session_id": session_id,
                "text": text,
            }

        @staticmethod
        def _headless_log_result_count(_entry):
            return 0

        def _reattach_spawned_orphans(self, **_kwargs):
            self.reattach_calls += 1
            self._spawned_sessions.append({"pid": 42, "engine": "claude"})

        @staticmethod
        def _load_spawn_registry():
            return [{"engine": "kimi", "session_id": "kimi-session"}]

        def _acp_maybe_attach_on_view(self, harness, sid):
            self.kimi_attach_calls.append((harness, sid))
            return None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ledger = WorkLedger(pathlib.Path(self.tmp.name, "ledger.sqlite3"))
        self.runtime = WorkerRuntime(ledger=ledger, token="a" * 64)
        self.host = EngineHost(self.runtime)
        self.runtime._engine_host = self.host
        self.fake = self.FakeLegacy()
        self.host._module = self.fake

    def test_mutating_engine_rpc_is_idempotent(self):
        params = {
            "engine": "kimi",
            "operation": "config",
            "idempotency_key": "same-browser-action",
            "args": {
                "session_id": "session-1",
                "config_id": "model",
                "value": "k3",
            },
        }
        first = self.host.execute(params)
        second = self.host.execute(params)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(first["work_id"], second["work_id"])
        self.assertEqual(self.fake.config_calls, 1)
        self.assertEqual(second["work"]["state"], "completed")

    def test_drain_records_but_does_not_dispatch_work(self):
        self.runtime.ledger.set_drain(True, "dashboard restart")
        response = self.host.execute({
            "engine": "kimi",
            "operation": "config",
            "idempotency_key": "queued-during-drain",
            "args": {
                "session_id": "session-1",
                "config_id": "model",
                "value": "k3",
            },
        })
        self.assertTrue(response["queued"])
        self.assertTrue(response["deferred"])
        self.assertEqual(response["work"]["state"], "queued")
        self.assertEqual(self.fake.config_calls, 0)
        resumed = self.runtime.dispatch("drain.set", {"enabled": False})
        self.assertEqual(resumed["replayed"], 1)
        self.assertEqual(self.fake.config_calls, 1)
        self.assertEqual(
            self.runtime.ledger.get(response["work_id"])["state"],
            "completed",
        )

    def test_claude_queued_inject_is_durable_and_idempotent(self):
        request = {
            "engine": "claude",
            "operation": "inject",
            "idempotency_key": "claude-browser-action",
            "args": {"session_id": "session-claude", "text": "continue"},
        }
        first = self.host.execute(request)
        duplicate = self.host.execute(request)
        self.assertTrue(first["ok"])
        self.assertTrue(first["queued"])
        self.assertEqual(first["work"]["state"], "completed")
        self.assertTrue(duplicate["deduplicated"])
        self.assertEqual(first["work_id"], duplicate["work_id"])

    def test_claude_inject_forwards_explicit_queue_flag(self):
        response = self.host.execute({
            "engine": "claude",
            "operation": "inject",
            "idempotency_key": "claude-explicit-queue",
            "args": {
                "session_id": "session-claude",
                "text": "wait until next turn",
                "force_queue": True,
            },
        })

        self.assertTrue(response["ok"])
        self.assertEqual(len(self.fake.inject_calls), 1)
        self.assertTrue(self.fake.inject_calls[0][2]["force_queue"])

    def test_parent_session_creates_durable_child_edge(self):
        parent, _ = self.runtime.ledger.submit(
            engine="claude",
            idempotency_key="parent-action",
            session_id="parent-session",
            kind="spawn",
            payload={"operation": "spawn", "args": {}},
        )
        child = self.host.execute({
            "engine": "kimi",
            "operation": "config",
            "idempotency_key": "child-action",
            "args": {
                "session_id": "child-session",
                "parent_session_id": "parent-session",
                "config_id": "model",
                "value": "k3",
            },
        })
        graph = self.runtime.ledger.graph(parent["id"])
        self.assertTrue(child["ok"])
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["child_id"], child["work_id"])

    def test_adopt_registry_takes_over_legacy_transports(self):
        adopted = self.host.adopt_registry()

        self.assertTrue(adopted["ok"])
        self.assertEqual(adopted["adopted"], 1)
        self.assertEqual(adopted["tracked"], 1)
        self.assertEqual(adopted["kimi_attached"], 1)
        self.assertEqual(self.fake.reattach_calls, 1)
        self.assertEqual(
            self.fake.kimi_attach_calls,
            [("kimi", "kimi-session")],
        )


@unittest.skipIf(os.name != "posix", "Unix socket and FIFO integration")
class TestPersistentWorkerIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root / "state"
        self.socket = self.state / "worker.sock"
        self.token = self.state / "worker.token"
        self.fake_claude = self.root / "fake-claude"
        self.fake_claude.write_text(
            """#!/usr/bin/env python3
import json
import sys
import uuid
if "--version" in sys.argv:
    print("fake-claude 1.0")
    raise SystemExit(0)
sid = str(uuid.uuid4())
print(json.dumps({"type":"system","subtype":"init","session_id":sid}), flush=True)
for line in sys.stdin:
    try:
        event = json.loads(line)
    except Exception:
        continue
    text = str(((event.get("message") or {}).get("content") or ""))
    print(json.dumps({
        "type":"assistant",
        "session_id":sid,
        "message":{"content":[{"type":"text","text":"ack:" + text}]},
    }), flush=True)
    print(json.dumps({
        "type":"result",
        "session_id":sid,
        "subtype":"success",
        "result":"done",
    }), flush=True)
""",
            encoding="utf-8",
        )
        self.fake_claude.chmod(0o755)
        self.env = dict(os.environ)
        self.env.update({
            "HOME": str(self.root),
            "CCC_STATE_DIR": str(self.state),
            "CCC_WORKER_SOCKET": str(self.socket),
            "CCC_WORK_LEDGER": str(self.state / "ledger.sqlite3"),
            "CCC_CLAUDE_BIN": str(self.fake_claude),
            "CCC_SKIP_SKILL_INSTALL": "1",
        })
        self.worker = None
        self.child_pid = None
        self._start_worker()
        self.addCleanup(self._cleanup_processes)

    def _start_worker(self):
        self.worker = subprocess.Popen(
            [sys.executable, "ccc_worker.py", "--socket", str(self.socket)],
            cwd=pathlib.Path(__file__).resolve().parents[1],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            if self.socket.exists() and self.token.exists():
                client = ControlPlaneClient(
                    path=self.socket, token_file=self.token, timeout=2
                )
                if client.request("health").get("ok"):
                    self.client = ControlPlaneClient(
                        path=self.socket, token_file=self.token, timeout=15
                    )
                    return
            if self.worker.poll() is not None:
                break
            time.sleep(0.05)
        self.fail("worker did not become healthy")

    def _cleanup_processes(self):
        if self.worker is not None and self.worker.poll() is None:
            self.worker.terminate()
            try:
                self.worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.worker.kill()
        if self.child_pid:
            try:
                os.killpg(self.child_pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

    def _wait_for(self, predicate, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.1)
        self.fail("timed out waiting for worker state")

    def test_claude_turn_survives_dashboard_and_worker_reconnects(self):
        request = {
            "engine": "claude",
            "operation": "spawn",
            "idempotency_key": "integration-spawn",
            "args": {
                "prompt": "first turn",
                "cwd": str(pathlib.Path(__file__).resolve().parents[1]),
            },
        }
        spawned = self.client.request("engine.execute", request)
        self.assertTrue(spawned["ok"], spawned)
        self.child_pid = spawned["pid"]
        duplicate = self.client.request("engine.execute", request)
        self.assertEqual(duplicate["pid"], self.child_pid)
        self.assertTrue(duplicate["deduplicated"])

        def completed_spawn():
            row = self.client.request(
                "work.get", {"work_id": spawned["work_id"]}
            ).get("work")
            return row if row and row["state"] == "completed" else None

        row = self._wait_for(completed_spawn)
        sid = row["session_id"]
        self.assertTrue(sid)

        # A brand-new client stands in for a restarted dashboard process.
        dashboard_after_restart = ControlPlaneClient(
            path=self.socket, token_file=self.token, timeout=3
        )
        injected = dashboard_after_restart.request("engine.execute", {
            "engine": "claude",
            "operation": "inject",
            "idempotency_key": "integration-inject",
            "args": {"session_id": sid, "text": "second turn"},
        })
        self.assertTrue(injected["ok"], injected)

        # Restart the worker itself. Its child remains alive, the FIFO is
        # reopened, and evidence reconciles the in-flight item conservatively.
        self.worker.terminate()
        self.worker.wait(timeout=3)
        self._start_worker()
        health = self.client.request("health")
        self.assertTrue(health["ok"])
        self.assertTrue(os.kill(self.child_pid, 0) is None)

        self.client.request("drain.set", {
            "enabled": True,
            "reason": "integration drain",
        })
        deferred = self.client.request("engine.execute", {
            "engine": "claude",
            "operation": "inject",
            "idempotency_key": "integration-deferred",
            "args": {"session_id": sid, "text": "third turn"},
        })
        self.assertTrue(deferred["deferred"])
        resumed = self.client.request("drain.set", {
            "enabled": False,
            "reason": "integration resume",
        })
        self.assertEqual(resumed["replayed"], 1)


if __name__ == "__main__":
    unittest.main()
