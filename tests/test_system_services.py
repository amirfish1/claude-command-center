"""Contract for GET /api/system/services.

One endpoint backs both the SERVER STATUS chip (20s) and the open System
status panel (5s), and the frontend reads the field names literally: `state`,
`started_at`, `busy_count`, `restart_endpoint`, `restart_blocking`. Renaming
any of them silently blanks a row rather than raising, so they are pinned here.

The `state` vocabulary is shared by all four rows on purpose -- one mapping in
the frontend, not four. "idle" belongs to the Codex app-server alone: it is a
lazily started subprocess, and reporting "offline" for "nobody has asked for a
Codex turn yet" would paint the header yellow on a perfectly healthy machine.
"""

import importlib
import unittest
from unittest import mock


SERVICE_STATES = {"online", "degraded", "offline", "idle", "unknown"}

WT_STOPPED = {
    "ok": True, "installed": True, "running": False, "pid": None,
    "command_verified": False, "pid_reused": False, "api_ok": False,
    "state": "stopped", "host": "127.0.0.1", "port": 8787,
    "url": "http://127.0.0.1:8787", "started_at": None,
    "started_at_approx": False, "uptime_s": None,
    "queues_total": 0, "open_total": 0, "stuck_total": 0, "workers_live": 0,
}

WORKER_HEALTHY = {
    "ok": True, "available": True, "active": 2, "queued": 1, "uncertain": 4,
    "drain": {"enabled": False},
    "worker": {
        "pid": 4242, "started_at": 1785262538.385,
        "capabilities": ["engine-execution-v1"],
    },
}


class SystemServicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def setUp(self):
        self.server._system_services_cache = {"ts": 0.0, "payload": None}

    def _build(self, *, worker=None, watchtower=None, app_server=None, busy=None):
        server = self.server
        worker = dict(WORKER_HEALTHY if worker is None else worker)
        worker.setdefault("worker", {}).setdefault(
            "server_version", server.__version__
        )
        wt = dict(WT_STOPPED if watchtower is None else watchtower)
        app = app_server if app_server is not None else {
            "live": False, "consecutive_liveness_misses": 0, "miss_threshold": 3,
        }
        with mock.patch.object(server, "_control_plane_request",
                               side_effect=lambda *a, **k: worker), \
             mock.patch.object(server, "_watchtower_service_status",
                               side_effect=lambda **k: wt), \
             mock.patch.object(server, "_app_server_status_preferring_worker",
                               return_value=app), \
             mock.patch.object(server, "_dashboard_owned_active_executions",
                               return_value=list(busy or [])):
            return server.build_system_services(force=True)

    def _row(self, payload, service_id):
        for row in payload["services"]:
            if row["id"] == service_id:
                return row
        self.fail(f"no {service_id} row in /api/system/services")

    # ── shape ────────────────────────────────────────────────────────────

    def test_returns_all_four_services_with_a_shared_state_vocabulary(self):
        payload = self._build()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dashboard_version"], self.server.__version__)
        self.assertEqual(
            [row["id"] for row in payload["services"]],
            ["dashboard", "worker", "watchtower", "app_server"],
        )
        for row in payload["services"]:
            self.assertIn(row["state"], SERVICE_STATES, row)
            for field in ("label", "started_at", "started_at_approx", "uptime_s",
                          "busy_count", "restart_endpoint", "restart_body",
                          "restart_blocking"):
                self.assertIn(field, row, f"{row['id']} lost `{field}`")

    def test_restart_endpoints_match_the_routes_that_actually_exist(self):
        """No new POSTs: the three existing endpoints already cover this."""
        payload = self._build()
        self.assertEqual(self._row(payload, "dashboard")["restart_endpoint"],
                         "/api/restart")
        self.assertEqual(self._row(payload, "worker")["restart_endpoint"],
                         "/api/restart/worker")
        wt = self._row(payload, "watchtower")
        self.assertEqual(wt["restart_endpoint"], "/api/watchtower/service")
        self.assertEqual(wt["restart_body"], {"action": "start"})
        # A worker subprocess, not a service: restarting it means restarting
        # the worker, which the row above already offers.
        self.assertIsNone(self._row(payload, "app_server")["restart_endpoint"])

    # ── dashboard ────────────────────────────────────────────────────────

    def test_dashboard_is_online_and_reports_its_own_start_time(self):
        payload = self._build()
        dash = self._row(payload, "dashboard")
        self.assertEqual(dash["state"], "online")
        self.assertEqual(dash["started_at"], self.server._SERVER_START_TS)
        self.assertFalse(dash["started_at_approx"])
        self.assertEqual(dash["version"], self.server.__version__)

    def test_a_dashboard_owned_kimi_turn_marks_the_restart_blocking(self):
        """Mirrors the real 409 in the restart route, so the UI can say why."""
        payload = self._build(busy=[{"engine": "kimi", "session_id": "s1"}])
        dash = self._row(payload, "dashboard")
        self.assertTrue(dash["restart_blocking"])
        self.assertEqual(dash["busy_count"], 1)

    def test_a_claude_spawn_is_busy_but_does_not_block_a_restart(self):
        payload = self._build(busy=[{"engine": "claude", "session_id": "s1", "pid": 9}])
        dash = self._row(payload, "dashboard")
        self.assertEqual(dash["busy_count"], 1)
        self.assertFalse(dash["restart_blocking"])

    # ── worker ───────────────────────────────────────────────────────────

    def test_worker_carries_the_counts_the_busy_line_needs(self):
        worker = self._row(self._build(), "worker")
        self.assertEqual(worker["state"], "online")
        self.assertEqual(worker["active"], 2)
        self.assertEqual(worker["queued"], 1)
        self.assertEqual(worker["uncertain"], 4)
        self.assertFalse(worker["drain_enabled"])
        self.assertEqual(worker["started_at"], 1785262538.385)

    def test_socket_answered_but_health_did_not_is_degraded_not_offline(self):
        """The wedged shape _retire_wedged_control_plane_worker exists for."""
        payload = self._build(worker={"ok": False, "available": True, "worker": {}})
        self.assertEqual(self._row(payload, "worker")["state"], "degraded")

    def test_no_worker_socket_is_offline(self):
        payload = self._build(worker={"ok": False, "available": False, "worker": {}})
        self.assertEqual(self._row(payload, "worker")["state"], "offline")

    def test_a_worker_on_older_server_py_is_flagged_stale(self):
        """The classic "the fix did not work": worker still runs old code."""
        payload = self._build(worker={
            "ok": True, "available": True, "active": 0, "queued": 0,
            "uncertain": 0, "drain": {"enabled": False},
            "worker": {"pid": 1, "started_at": 1.0, "server_version": "0.0.1"},
        })
        self.assertTrue(self._row(payload, "worker")["version_stale"])

    def test_a_worker_that_never_imported_server_is_not_stale(self):
        """server_version None means "no stale code to restart away"."""
        payload = self._build(worker={
            "ok": True, "available": True, "active": 0, "queued": 0,
            "uncertain": 0, "drain": {"enabled": False},
            "worker": {"pid": 1, "started_at": 1.0, "server_version": None},
        })
        self.assertFalse(self._row(payload, "worker")["version_stale"])

    # ── watchtower ───────────────────────────────────────────────────────

    def test_watchtower_stopped_maps_to_the_shared_offline_word(self):
        wt = self._row(self._build(), "watchtower")
        self.assertEqual(wt["state"], "offline")

    def test_watchtower_start_time_is_reported_as_approximate(self):
        """It comes from the pidfile mtime, so the UI says "about"."""
        payload = self._build(watchtower=dict(
            WT_STOPPED, running=True, api_ok=True, state="online", pid=99,
            command_verified=True, started_at=1785215178.0,
            started_at_approx=True, uptime_s=48234,
            queues_total=3, open_total=12, stuck_total=1, workers_live=2,
        ))
        wt = self._row(payload, "watchtower")
        self.assertEqual(wt["state"], "online")
        self.assertTrue(wt["started_at_approx"])
        self.assertEqual(wt["stuck_total"], 1)
        self.assertEqual(wt["workers_live"], 2)
        self.assertEqual(wt["restart_body"], {"action": "restart"})

    def test_unverified_process_identity_blocks_the_restart_button(self):
        payload = self._build(watchtower=dict(
            WT_STOPPED, running=True, state="degraded", pid=99,
            pid_reused=True, command_verified=False,
        ))
        self.assertTrue(self._row(payload, "watchtower")["restart_blocking"])

    # ── codex app-server ─────────────────────────────────────────────────

    def test_a_not_yet_started_app_server_is_idle_not_offline(self):
        payload = self._build()
        app = self._row(payload, "app_server")
        self.assertEqual(app["state"], "idle")

    def test_app_server_is_offline_only_when_the_worker_is_gone(self):
        payload = self._build(worker={"ok": False, "available": False, "worker": {}})
        self.assertEqual(self._row(payload, "app_server")["state"], "offline")

    def test_a_missed_liveness_probe_is_degraded(self):
        payload = self._build(app_server={
            "live": True, "pid": 7, "age_s": 431,
            "consecutive_liveness_misses": 1, "miss_threshold": 3,
        })
        app = self._row(payload, "app_server")
        self.assertEqual(app["state"], "degraded")
        self.assertEqual(app["uptime_s"], 431)

    # ── caching ──────────────────────────────────────────────────────────

    def test_the_snapshot_is_coalesced_within_the_ttl(self):
        server = self.server
        with mock.patch.object(server, "_build_system_services_uncached",
                               return_value={"ok": True, "services": []}) as build:
            server.build_system_services(force=True)
            server.build_system_services()
            server.build_system_services()
        self.assertEqual(build.call_count, 1)


class AppServerRoutingTests(unittest.TestCase):
    """The transport lives in the worker, not the dashboard.

    _control_plane_routes_engines() defaults on, so Codex runs through the
    worker's lazily-imported copy of `server`. Reading the dashboard's own
    module global returned live:false on a healthy system and the panel said
    "idle" mid-Codex-session.
    """

    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_prefers_the_workers_answer(self):
        server = self.server
        remote = {"ok": True, "status": {"live": True, "pid": 61204}}
        with mock.patch.object(server, "_control_plane_request", return_value=remote), \
             mock.patch.object(server, "_system_app_server_status") as local:
            out = server._app_server_status_preferring_worker()
        self.assertEqual(out, {"live": True, "pid": 61204})
        local.assert_not_called()

    def test_falls_back_locally_when_no_worker_answers(self):
        """Compatibility mode: the dashboard really does own the transport."""
        server = self.server
        local_status = {"live": False, "kind": None}
        with mock.patch.object(server, "_control_plane_request",
                               return_value={"ok": False, "available": False}), \
             mock.patch.object(server, "_system_app_server_status",
                               return_value=local_status):
            out = server._app_server_status_preferring_worker()
        self.assertEqual(out, local_status)


class WorkerVerbTests(unittest.TestCase):
    def test_worker_exposes_system_app_server(self):
        import sys
        ccc_worker = importlib.import_module("ccc_worker")
        runtime = ccc_worker.WorkerRuntime.__new__(ccc_worker.WorkerRuntime)
        sentinel = {"live": True, "pid": 1234}
        fake = mock.Mock()
        fake._system_app_server_status.return_value = sentinel
        with mock.patch.dict(sys.modules, {"server": fake}):
            out = runtime.dispatch("system.app_server", {})
        self.assertEqual(out, {"ok": True, "status": sentinel})

    def test_reports_not_loaded_when_the_worker_never_imported_server(self):
        import sys
        ccc_worker = importlib.import_module("ccc_worker")
        runtime = ccc_worker.WorkerRuntime.__new__(ccc_worker.WorkerRuntime)
        modules = dict(sys.modules)
        modules.pop("server", None)
        with mock.patch.dict(sys.modules, modules, clear=True):
            out = runtime.dispatch("system.app_server", {})
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "not_loaded")


if __name__ == "__main__":
    unittest.main()
