import json
import os
import pathlib
import socket
import tempfile
import threading
import time
import urllib.request
import unittest

from ccc_worker import WorkerRuntime, WorkerServer
from control_plane import ControlPlaneClient, WorkLedger


class TestWorkLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.ledger = WorkLedger(self.root / "control-plane.sqlite3")

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


if __name__ == "__main__":
    unittest.main()
