"""`ccc restart` (OPS-150 / CCC-206): agents restart CCC through the
drain-aware restart endpoints, and requests inside the debounce window merge
into one deferred restart instead of each costing a cold warmup.

Runs the real `ccc` script (and its detached --run-pending runner) against a
stub HTTP server standing in for the restart endpoints, with HOME pointed at
a temp dir so the debounce state file is isolated.
"""

import http.server
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

CCC = str(Path(__file__).resolve().parent.parent / "ccc")


def _load_ccc_cli():
    loader = importlib.machinery.SourceFileLoader("ccc_cli_restart_under_test", CCC)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _Stub(http.server.BaseHTTPRequestHandler):
    posts = []
    callers = []
    pid = 1000
    refuse = 0  # answer this many restart POSTs with 409 first

    def log_message(self, *a):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            self._send(200, {"pid": _Stub.pid, "code_rev": "abc"})
        else:
            self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n))
        _Stub.callers.append(payload["audit_caller"]["pid"])
        if _Stub.refuse > 0:
            _Stub.refuse -= 1
            _Stub.posts.append(("409", self.path))
            self._send(409, {"ok": False, "error": "drain in flight"})
            return
        _Stub.posts.append(("200", self.path))
        before = _Stub.pid
        _Stub.pid += 1  # the "restarted" dashboard answers with a new pid
        self._send(200, {"ok": True, "before": {"pid": before}})


class CccRestartCliTests(unittest.TestCase):
    def setUp(self):
        _Stub.posts, _Stub.callers, _Stub.refuse, _Stub.pid = [], [], 0, 1000
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.home = tempfile.mkdtemp()
        self.env = dict(os.environ, HOME=self.home, CCC_RESTART_DEBOUNCE_S="3")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        # A runner may still be waiting for the (now gone) stub to answer.
        state_file = Path(self.home) / ".claude/command-center/restart-debounce.json"
        try:
            pid = json.loads(state_file.read_text()).get("runner_pid")
            if pid:
                os.kill(int(pid), 15)
        except (OSError, ValueError):
            pass

    def ccc(self, *args):
        return subprocess.run([sys.executable, CCC, "--server", self.base, "restart", *args],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def wait_posts(self, n, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline and len(_Stub.posts) < n:
            time.sleep(0.2)
        return list(_Stub.posts)

    def test_first_restart_fires_now_and_waits_for_new_pid(self):
        r = self.ccc()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_Stub.posts, [("200", "/api/restart/all")])
        self.assertIn("dashboard back (pid 1001", r.stdout)

    def test_requests_inside_window_merge_into_one_deferred_restart(self):
        self.assertEqual(self.ccc("worker").returncode, 0)
        r2 = self.ccc("worker")
        r3 = self.ccc("dashboard")
        self.assertIn("queued one worker restart", r2.stdout)
        self.assertIn("queued one all restart", r3.stdout)  # worker + dashboard == all
        posts = self.wait_posts(2)
        time.sleep(1.5)  # nothing else may fire after the merged one
        self.assertEqual(_Stub.posts, [("200", "/api/restart/worker"), ("200", "/api/restart/all")])
        self.assertEqual(posts[:2], _Stub.posts)

    def test_now_skips_the_window(self):
        self.ccc("worker")
        self.ccc("worker", "--now")
        self.assertEqual(_Stub.posts, [("200", "/api/restart/worker")] * 2)

    def test_concurrent_processes_share_one_restart_slot(self):
        self.env.pop("CCC_RESTART_DEBOUNCE_S")
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.ccc("worker"), range(6)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_Stub.posts, [("200", "/api/restart/worker")])
        state = json.loads((Path(self.home) / ".claude/command-center/restart-debounce.json").read_text())
        self.assertEqual(state["pending"]["target"], "worker")
        self.assertGreater(state["pending"]["due"] - time.time(), 1700)

    def test_refused_restart_is_retried_not_dropped(self):
        _Stub.refuse = 1
        r = self.ccc("worker")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not safe yet (drain in flight)", r.stdout)
        # Retry cadence is 60s in production; the runner honours the due time
        # recorded in the state file, so pull it in for the test. Use the
        # CLI's lock: the detached runner also reads and rewrites this file,
        # and an unlocked edit can be lost or expose partially written JSON.
        state_file = Path(self.home) / ".claude/command-center/restart-debounce.json"
        cli = _load_ccc_cli()
        cli.RESTART_STATE_FILE = str(state_file)
        cli._restart_state_update(lambda state: state["pending"].update(due=time.time()))
        self.assertEqual(self.wait_posts(2, timeout=45),
                         [("409", "/api/restart/worker"), ("200", "/api/restart/worker")])

    def test_unreachable_server_fails(self):
        self.base = "http://127.0.0.1:9"  # discard port: connection refused
        r = self.ccc("worker")
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed", r.stderr)

    def test_pending_runner_loads_upgraded_cli_before_dispatch(self):
        self.env["CCC_RESTART_DEBOUNCE_S"] = "1800"
        candidate = Path(self.home) / "ccc"
        original = Path(CCC).read_text()
        candidate.write_text(original)
        command = [sys.executable, str(candidate), "--server", self.base, "restart", "worker"]
        for _ in range(2):
            result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

        cli = _load_ccc_cli()
        cli.RESTART_STATE_FILE = str(Path(self.home) / ".claude/command-center/restart-debounce.json")
        runner = cli._restart_state_update(lambda state: state["runner_pid"])

        upgraded = candidate.with_suffix(".new")
        upgraded.write_text(original.replace('"/api/restart/worker"', '"/api/restart/upgraded-worker"'))
        upgraded.replace(candidate)

        # Advance the test clock past the batching window on disk so the
        # detached process makes its attempt immediately.
        def elapse_window(state):
            state["last_restart"] = time.time() - 1801
            state["pending"]["due"] = time.time()

        cli._restart_state_update(elapse_window)
        self.assertEqual(self.wait_posts(2, timeout=10), [
            ("200", "/api/restart/worker"),
            ("200", "/api/restart/upgraded-worker"),
        ])
        self.assertEqual(_Stub.callers[-1], runner)

    def test_loaded_thread_refusals_outlast_retry_limit_then_restart_once(self):
        # Exercise the actual runner and HTTP requests, with only its wait
        # shortened. A busy fleet must not lose the request at retry eleven.
        cli = _load_ccc_cli()
        cli.RESTART_STATE_FILE = str(Path(self.home) / "restart.json")
        cli.RESTART_DEBOUNCE_S = 0
        cli._RESTART_RETRY_S = 0
        _Stub.refuse = 12
        cli._restart_state_update(lambda state: state.update(
            runner_pid=os.getpid(), pending={"target": "worker", "due": 0}))
        self.assertEqual(cli._run_pending_restart(self.base, 0), 0)
        self.assertEqual(_Stub.posts, [("409", "/api/restart/worker")] * 12
                         + [("200", "/api/restart/worker")])
        state = cli._restart_state_update(lambda state: dict(state))
        self.assertNotIn("pending", state)
        self.assertNotIn("runner_pid", state)


class RestartReservationTests(unittest.TestCase):
    def setUp(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("CCC_RESTART_DEBOUNCE_S", None)
            self.m = _load_ccc_cli()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.m.RESTART_STATE_FILE = str(Path(self.home.name) / "restart.json")
        # Exercise the real file lock and state transitions, without starting
        # a long-lived runner. Model its ownership as this exact process.
        self.m._spawn_restart_runner = lambda *_: os.getpid()
        self.m._get_json = mock.Mock(return_value={})

    def state(self):
        return self.m._restart_state_update(lambda s: dict(s))

    def observation_lease(self):
        lease = Path(self.m.RESTART_STATE_FILE + ".observation")
        lease.write_text(json.dumps({
            "server": "base", "after_started_at": 9000,
            "expires_at": 19000, "minimum_uptime_s": 5400,
        }))
        return lease

    def test_observation_hold_preserves_reserved_boot_and_runner_target(self):
        self.observation_lease()
        self.m._get_json.return_value = {"started_at": 9000}
        with mock.patch("time.time", return_value=10800) as clock:
            self.m._restart_state_update(lambda state: state.update(
                runner_pid=os.getpid(), last_dashboard_back=9000,
                pending={"target": "dashboard", "due": 10800}))
            self.assertEqual(self.m._reserve_restart("base", runner=True)["target"],
                             "dashboard")
            self.m._get_json.return_value = {"started_at": 10810}
            clock.return_value = 10812
            self.m._note_dashboard_back(9000, 10810)
            queued = self.m._reserve_restart("base", "dashboard")["pending"]
            self.assertEqual(queued["due"], 16210)
            self.assertEqual(self.m._reserve_restart("base", "worker")["pending"]["target"],
                             "all")
            clock.return_value = 12613
            self.assertEqual(self.m._reserve_restart("base", runner=True),
                             {"hold_until": 16210})
            clock.return_value = 16210
            self.assertEqual(self.m._reserve_restart("base", runner=True)["target"], "all")

    def test_observation_hold_can_be_overridden_or_removed(self):
        lease = self.observation_lease()
        self.m._get_json.return_value = {"started_at": 13000}
        self.m.RESTART_DEBOUNCE_S = 0
        with mock.patch("time.time", return_value=15000) as clock:
            self.assertEqual(self.m._reserve_restart("base", "worker")["target"], "worker")
            self.assertIn("pending", self.m._reserve_restart("base", "all"))
            self.assertEqual(self.m._reserve_restart("base", "dashboard", force=True)["target"],
                             "dashboard")
            self.assertEqual(self.state()["pending"]["target"], "worker")
            clock.return_value = 15002
            self.assertEqual(self.m._reserve_restart("base", runner=True)["target"], "worker")
            self.assertIn("pending", self.m._reserve_restart("base", "dashboard"))
            lease.unlink()
            clock.return_value = 15004
            self.assertEqual(self.m._reserve_restart("base", runner=True)["target"], "dashboard")

    def _run_replies(self, replies):
        self.m.RESTART_DEBOUNCE_S = 0
        self.m._RESTART_RETRY_S = 0
        self.m._restart_state_update(lambda state: state.update(
            runner_pid=os.getpid(), pending={"target": "worker", "due": 0}))
        with mock.patch.object(self.m, "_post_restart", side_effect=replies) as post:
            self.assertEqual(self.m._run_pending_restart("base", 0), 0)
        return post.call_count

    def test_non_safety_failures_still_have_a_retry_limit(self):
        self.assertEqual(self._run_replies([(503, {"error": "unavailable"})] * 11), 11)
        self.assertNotIn("pending", self.state())
        self.assertNotIn("runner_pid", self.state())

    def test_safety_refusal_resets_consecutive_failure_budget(self):
        self.assertEqual(self._run_replies(
            [(503, {"error": "unavailable"})] * 10
            + [(409, {"error": "Codex threads are loaded"})] * 12
            + [(503, {"error": "unavailable"})] * 10
            + [(200, {"ok": True})]), 33)
        self.assertNotIn("pending", self.state())

    def test_combined_restart_preserves_worker_after_dashboard_recovers(self):
        self.m.RESTART_DEBOUNCE_S = 0
        self.m._RESTART_RETRY_S = 0
        self.m._restart_state_update(lambda state: state.update(
            runner_pid=os.getpid(), pending={"target": "all", "due": 0, "retries": 206}))
        replies = [
            (409, {"code": "codex_state_unavailable", "reason": "threads_loaded"}),
            (200, {"before": {"started_at": 9000, "pid": 7}, "worker_restarted": False}),
            (409, {"code": "codex_turns_running"}),
            (200, {"ok": True}),
        ]
        with mock.patch.object(self.m, "_post_restart", side_effect=replies) as post, \
             mock.patch.object(self.m, "_wait_dashboard_back", return_value={"pid": 7, "started_at": 10000}):
            assert self.m._run_pending_restart("base", 0) == 0
        assert [call.args[1] for call in post.call_args_list] == [
            "all", "dashboard", "worker", "worker",
        ]
        assert all(call.kwargs["routine"] for call in post.call_args_list)
        assert "pending" not in self.state()

    def test_combined_restart_keeps_dashboard_guard_and_explicit_target(self):
        for code, routine in (("codex_state_unavailable", False), ("dashboard_busy", True)):
            with self.subTest(code=code, routine=routine), mock.patch.object(
                self.m, "_post_restart", return_value=(409, {"code": code}),
            ) as post:
                assert self.m._restart_now("base", "all", routine=routine)[0] == 409
                post.assert_called_once_with("base", "all", routine=routine)

    def test_combined_restart_retries_worker_when_final_proof_refuses(self):
        with mock.patch.object(self.m, "_post_restart", return_value=(
            200, {"ok": True, "worker_restarted": False, "worker": {"reason": "codex_turns_running"}},
        )) as post:
            assert self.m._restart_now("base", "all", routine=True, wait=False)[0] == 0
        post.assert_called_once_with("base", "all", routine=True)
        assert self.state()["pending"]["target"] == "worker"

    def test_merging_request_preserves_failure_budget(self):
        with mock.patch("time.time", return_value=10000):
            self.m._reserve_restart("base", "worker")
            self.m._reserve_restart("base", "worker")
            self.m._restart_state_update(lambda state: state["pending"].update(
                retries=25, failure_retries=4))
            self.m._reserve_restart("base", "dashboard")
            self.assertEqual(self.state()["pending"]["target"], "all")
            self.assertEqual(self.state()["pending"]["failure_retries"], 4)
            self.assertEqual(self.state()["pending"]["retries"], 25)

    def test_external_start_moves_an_already_queued_restart(self):
        with mock.patch("time.time", return_value=10000) as clock:
            self.m._reserve_restart("base", "dashboard")
            self.m._reserve_restart("base", "dashboard")
            # An external start occurs midway through the queued window.
            self.m._get_json.return_value = {"started_at": 10900}
            clock.return_value = 11800
            slot = self.m._reserve_restart("base", runner=True)
            self.assertEqual(slot["pending"]["due"], 12700)
            self.assertEqual(self.state()["last_dashboard_back"], 10900)
            # Reobserving the same generation must not postpone forever.
            clock.return_value = 12700
            self.assertIn("reservation", self.m._reserve_restart("base", runner=True))

    def test_fresh_state_respects_running_dashboard_but_now_can_override(self):
        with mock.patch("time.time", return_value=10000):
            self.m._get_json.return_value = {"started_at": 9900}
            slot = self.m._reserve_restart("base", "dashboard")
            self.assertEqual(slot["pending"]["due"], 11700)
            self.assertIn("reservation", self.m._reserve_restart("base", "dashboard", force=True))
            self.assertEqual(self.m._get_json.call_count, 1)

    def test_gc_hint_tracks_the_runner_generation_across_merge_and_reservation(self):
        with mock.patch("time.time", return_value=10000) as clock:
            self.m._get_json.return_value = {"started_at": 9900}
            slot = self.m._reserve_restart("base", "dashboard")
            self.assertEqual(slot["pending"]["dashboard_started_at"], 9900)

            self.m._get_json.return_value = {"started_at": 9800}
            slot = self.m._reserve_restart("peer", "worker")
            self.assertEqual(slot["pending"]["dashboard_started_at"], 9900)

            self.m._get_json.return_value = {"started_at": 9900}
            clock.return_value = 11700
            slot = self.m._reserve_restart("base", runner=True)
            self.assertEqual(slot["target"], "all")
            self.assertEqual(slot["reservation"]["dashboard_started_at"], 9900)
            self.assertNotIn("pending", self.state())

    def test_missing_invalid_or_offline_generation_keeps_local_policy(self):
        for value in (None, True, "9000", -1, float("nan"), float("inf"), 10001):
            with self.subTest(value=value), mock.patch("time.time", return_value=10000):
                self.m._restart_state_update(lambda s: s.clear())
                self.m._get_json.return_value = {"started_at": value}
                self.assertIn("reservation", self.m._reserve_restart("base", "dashboard"))
        self.m._get_json.side_effect = OSError("offline")
        with mock.patch("time.time", return_value=11800):
            self.assertIn("reservation", self.m._reserve_restart("base", "dashboard"))

    def test_busy_hour_keeps_a_fixed_half_hour_window(self):
        with mock.patch("time.time", return_value=10000) as clock:
            starts = []
            for minute in range(60):
                clock.return_value = 10000 + minute * 60
                pending = self.state().get("pending")
                if pending and pending["due"] <= clock.return_value:
                    runner_slot = self.m._reserve_restart("base", runner=True)
                    if runner_slot.get("reservation"):
                        starts.append(clock.return_value)
                        self.m._finish_restart_reservation(runner_slot["reservation"], 200)
                slot = self.m._reserve_restart("base", "dashboard")
                if slot.get("reservation"):
                    starts.append(clock.return_value)
                    self.m._finish_restart_reservation(slot["reservation"], 200)
            self.assertEqual(starts, [10000, 11800])
            self.assertEqual(self.state()["pending"]["due"], 13600)

    def test_two_callers_at_expiry_cannot_both_reserve(self):
        with mock.patch("time.time", return_value=10000):
            barrier = threading.Barrier(8)

            def reserve(_):
                barrier.wait()
                return self.m._reserve_restart("base", "dashboard")

            with ThreadPoolExecutor(max_workers=8) as pool:
                slots = list(pool.map(reserve, range(8)))
            self.assertEqual(sum("reservation" in slot for slot in slots), 1)
            self.assertEqual(self.state()["pending"]["due"], 11800)

    def test_refusal_releases_slot_but_lost_reply_preserves_it(self):
        with mock.patch("time.time", return_value=10000):
            first = self.m._reserve_restart("base", "worker")
            self.m._reserve_restart("base", "worker")
            self.m._finish_restart_reservation(first["reservation"], 409)
            self.assertEqual(self.state()["last_restart"], 0)
            self.assertEqual(self.state()["pending"]["due"], 10060)
            self.m._restart_state_update(lambda s: s.pop("pending"))
            second = self.m._reserve_restart("base", "worker")
            self.m._finish_restart_reservation(second["reservation"], -1)
            self.assertEqual(self.state()["last_restart"], 10000)

    def test_now_fulfills_matching_pending_work_once(self):
        with mock.patch("time.time", return_value=10000):
            self.m._reserve_restart("base", "dashboard")
            self.m._reserve_restart("base", "dashboard")
            immediate = self.m._reserve_restart("base", "dashboard", force=True)
            self.assertEqual(immediate["target"], "dashboard")
            self.assertNotIn("pending", self.state())
            self.assertTrue(self.m._reserve_restart("base", runner=True)["stop"])

    def test_now_preserves_uncovered_target_and_runner_rechecks_window(self):
        with mock.patch("time.time", return_value=10000) as clock:
            self.m._reserve_restart("base", "dashboard")
            self.m._reserve_restart("base", "all")
            clock.return_value = 11799
            immediate = self.m._reserve_restart("base", "dashboard", force=True)
            self.m._finish_restart_reservation(immediate["reservation"], 200)
            self.assertEqual(immediate["target"], "dashboard")
            self.assertEqual(self.state()["pending"]["target"], "worker")
            clock.return_value = 11800
            slot = self.m._reserve_restart("base", runner=True)
            self.assertNotIn("reservation", slot)
            self.assertEqual(slot["pending"]["due"], 13599)

    def test_request_arriving_as_runner_exits_keeps_an_owner(self):
        with mock.patch("time.time", return_value=10000):
            self.m._reserve_restart("base", "dashboard")
            self.m._reserve_restart("base", "worker")
            before = self.state()
            self.assertFalse(self.m._retire_restart_runner("base"))
            self.assertEqual(self.state()["pending"], before["pending"])
            self.assertEqual(self.state()["runner_pid"], os.getpid())
            self.m._restart_state_update(lambda s: s.pop("pending"))
            self.assertTrue(self.m._retire_restart_runner("base"))
            self.assertNotIn("runner_pid", self.state())

    def test_delayed_dashboard_back_moves_window_and_already_pending_restart(self):
        with mock.patch('time.time', return_value=10000) as clock:
            slot = self.m._reserve_restart('base', 'dashboard')
            def post(*args, **kwargs):
                clock.return_value = 10001
                return 200, {'ok': True, 'before': {'pid': 7, 'started_at': 9000}}
            def back(*args, **kwargs):
                clock.return_value = 10010
                self.m._reserve_restart('base', 'dashboard')
                clock.return_value = 10045
                return {'pid': 7, 'started_at': 10044}
            with mock.patch.object(self.m, '_post_restart', side_effect=post), \
                 mock.patch.object(self.m, '_wait_dashboard_back', side_effect=back):
                status, _ = self.m._restart_now('base', 'dashboard', reservation=slot['reservation'])
            self.assertEqual(status, 0)
            self.assertEqual(self.state()['last_restart'], 10045)
            self.assertEqual(self.state()['pending']['due'], 11845)
            clock.return_value = 11801
            self.assertIn('pending', self.m._reserve_restart('base', runner=True))

    def test_dashboard_back_does_not_overwrite_a_newer_human_reservation(self):
        with mock.patch('time.time', return_value=10000) as clock:
            slot = self.m._reserve_restart('base', 'dashboard')
            def back(*args, **kwargs):
                clock.return_value = 10010
                self.m._reserve_restart('base', 'worker', force=True)
                clock.return_value = 10012
                return {'pid': 8, 'started_at': 10011}
            with mock.patch.object(self.m, '_post_restart', return_value=(200, {'ok': True, 'before': {'pid': 7}})), \
                 mock.patch.object(self.m, '_wait_dashboard_back', side_effect=back):
                self.m._restart_now('base', 'dashboard', reservation=slot['reservation'])
            self.assertEqual(self.state()['last_target'], 'worker')
            self.assertEqual(self.state()['reservation']['at'], 10010)
            self.assertEqual(self.state()['last_restart'], 10012)

    def test_no_observed_new_generation_keeps_uncertain_window(self):
        with mock.patch('time.time', return_value=10000):
            slot = self.m._reserve_restart('base', 'dashboard')
            with mock.patch.object(self.m, '_post_restart', return_value=(200, {'ok': True})), \
                 mock.patch.object(self.m, '_wait_dashboard_back', return_value=None):
                self.m._restart_now('base', 'dashboard', reservation=slot['reservation'])
            self.assertEqual(self.state()['last_restart'], 10000)

    def test_refusal_cannot_roll_back_a_dashboard_observed_during_request(self):
        with mock.patch('time.time', return_value=10000) as clock:
            slot = self.m._reserve_restart('base', 'worker')
            clock.return_value = 10010
            self.m._note_dashboard_back()
            clock.return_value = 10011
            self.m._finish_restart_reservation(slot['reservation'], 409)
            self.assertEqual(self.state()['last_restart'], 10010)
            self.assertIn('pending', self.m._reserve_restart('base', 'dashboard'))

    def test_variable_start_delays_cannot_fit_three_generations_in_an_hour(self):
        with mock.patch('time.time', return_value=10000) as clock:
            starts = []
            for number in range(3):
                if number:
                    clock.return_value = self.state()['last_restart'] + self.m.RESTART_DEBOUNCE_S
                slot = self.m._reserve_restart('base', 'dashboard')
                at = clock.return_value
                def post(*args, **kwargs):
                    clock.return_value = at + 1
                    return 200, {'ok': True, 'before': {'pid': number + 1}}
                def back(*args, **kwargs):
                    actual = at + (40 if number == 0 else 2)
                    starts.append(actual)
                    clock.return_value = actual + 1
                    return {'pid': number + 2, 'started_at': actual}
                with mock.patch.object(self.m, '_post_restart', side_effect=post), \
                     mock.patch.object(self.m, '_wait_dashboard_back', side_effect=back):
                    self.m._restart_now('base', 'dashboard', reservation=slot['reservation'])
            self.assertGreaterEqual(starts[2] - starts[0], 3600)


class WaitDashboardBackTests(unittest.TestCase):
    """_wait_dashboard_back identifies a new process generation; execvp keeps the pid."""

    def setUp(self):
        self.m = _load_ccc_cli()
        import time as _t
        self._sleep = _t.sleep
        _t.sleep = lambda s: None

    def tearDown(self):
        import time as _t
        _t.sleep = self._sleep

    def _feed(self, answers):
        it = iter(answers)

        def fake(base, path, timeout=5):
            a = next(it)
            if isinstance(a, Exception):
                raise a
            return a
        self.m._get_json = fake

    def test_same_pid_exec_detected_by_started_at(self):
        self._feed([{"pid": 7, "started_at": 2.0}])
        v = self.m._wait_dashboard_back("b", 7, before_started_at=1.0)
        self.assertEqual(v["started_at"], 2.0)

    def test_delayed_startup_waits_through_old_and_down(self):
        self._feed([{"pid": 7, "started_at": 1.0}, OSError("down"),
                    {"pid": 7, "started_at": 3.0}])
        v = self.m._wait_dashboard_back("b", 7, before_started_at=1.0)
        self.assertEqual(v["started_at"], 3.0)

    def test_failed_restart_times_out(self):
        self._feed([{"pid": 7, "started_at": 1.0}] * 10**6)
        self.assertIsNone(self.m._wait_dashboard_back("b", 7, timeout=0.01, before_started_at=1.0))

    def test_pid_change_still_works_without_started_at(self):
        self._feed([{"pid": 8}])
        self.assertEqual(self.m._wait_dashboard_back("b", 7)["pid"], 8)


if __name__ == "__main__":
    unittest.main()


def test_ccc_sends_routine_flag_only_when_not_forced(monkeypatch):
    """Keep CLI caller attribution independently of speech generation."""
    cli = _load_ccc_cli()
    sent = []
    monkeypatch.setattr(cli, "_post_json", lambda base, path, payload, timeout=60:
                        sent.append(payload) or (200, {"ok": True}))
    cli._post_restart("http://example.test", "dashboard", routine=True)
    cli._post_restart("http://example.test", "dashboard")
    assert [payload.get("routine", False) for payload in sent] == [True, False]
    assert all(payload["audit_caller"]["source"] == "ccc-cli"
               and payload["audit_caller"]["pid"] > 0 for payload in sent)
