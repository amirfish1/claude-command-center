"""WatchTower error alerts for the Queue panel (ccc_server/wt_alerts.py).

The strip exists so a worker that failed to launch (engine usage limit, auth,
API down), a stopped daemon, or a repeating backend ERROR is impossible to
miss above the queue — and so acking one never hides a NEWER failure.
"""

import importlib
import json
import pathlib
import tempfile
import time
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

wt_alerts = importlib.import_module("ccc_server.wt_alerts")

NOW = 1_788_450_000.0  # 2026-09-03T15:40:00Z


def _stamp(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self._tmp.name) / "wt"
        self.home.mkdir()
        self.acks = pathlib.Path(self._tmp.name) / "state" / "wt-alert-acks.json"
        wt_alerts._log_cache["key"] = None
        wt_alerts._log_cache["alerts"] = []
        wt_alerts._service_down_since = None

    def tearDown(self):
        wt_alerts._service_down_since = None
        self._tmp.cleanup()

    def _write_launch_failures(self, records):
        (self.home / "launch-failures.json").write_text(json.dumps(records))

    def _write_log(self, lines):
        (self.home / "activity.log").write_text("\n".join(lines) + "\n")

    def collect(self, **kw):
        kw.setdefault("wt_home", self.home)
        kw.setdefault("acks_path", self.acks)
        kw.setdefault("service_status", {"ok": True, "installed": True, "state": "online"})
        kw.setdefault("now", NOW)
        return wt_alerts.collect_wt_alerts(**kw)


class TestLaunchFailureAlerts(_Fixture):
    def test_usage_limit_record_becomes_an_error_alert(self):
        self._write_launch_failures({
            "THROUGHPUT:codex": {
                "queue": "THROUGHPUT", "engine": "codex", "model": "gpt-5.6-terra",
                "worker_id": "throughput-afeb77cf", "pid": 47951,
                "log": "/x/throughput-afeb77cf.log",
                "reason": "engine usage limit",
                "cooldown_until": NOW + 3600, "failed_at": NOW - 120,
                "consecutive": 2, "exit_code": 1,
            },
        })
        out = self.collect()
        self.assertEqual(out["total"], 1)
        a = out["alerts"][0]
        self.assertEqual(a["kind"], "launch_failure")
        self.assertEqual(a["severity"], "error")
        self.assertEqual(a["id"], "launch:THROUGHPUT:codex")
        self.assertEqual(a["queue"], "THROUGHPUT")
        self.assertEqual(a["title"], "engine usage limit")
        self.assertIn("throughput-afeb77cf", a["detail"])
        self.assertIn("codex:gpt-5.6-terra", a["detail"])
        self.assertIn("x2", a["detail"])
        self.assertTrue(a["cooldown_active"])
        self.assertEqual(a["age_seconds"], 120)
        self.assertFalse(a["acked"])

    def test_missing_or_corrupt_file_yields_nothing(self):
        self.assertEqual(self.collect()["alerts"], [])
        (self.home / "launch-failures.json").write_text("{not json")
        self.assertEqual(self.collect()["alerts"], [])


class TestActivityErrorAlerts(_Fixture):
    def test_repeated_error_lines_group_per_queue_with_count(self):
        cmd = ("GitHub list failed: gh issue list --repo x/y --state open --json a,b "
               "failed: To get started with GitHub CLI, please run:  gh auth login")
        self._write_log([
            _stamp(NOW - 7200) + " UTC  CCC-GH          ERROR    " + cmd,
            _stamp(NOW - 3600) + " UTC  CCC-GH          ERROR    " + cmd.replace("--state open", "--state closed"),
            _stamp(NOW - 600) + " UTC  STRAMP          ERROR    " + cmd,
            _stamp(NOW - 300) + " UTC  CCC-GH          CLAIM    CCC-1 by ccc-abc — fine",
            # Outside the 48h window: history, not an alert.
            _stamp(NOW - 3 * 86400) + " UTC  OLD             ERROR    something ancient failed: nope",
        ])
        out = self.collect()
        by_q = {a["queue"]: a for a in out["alerts"]}
        self.assertEqual(set(by_q), {"CCC-GH", "STRAMP"})
        ccc = by_q["CCC-GH"]
        self.assertEqual(ccc["kind"], "activity_error")
        self.assertEqual(ccc["severity"], "warning")
        self.assertEqual(ccc["count"], 2)  # open + closed variants collapse
        self.assertEqual(ccc["title"], "GitHub list failed")
        self.assertIn("gh auth login", ccc["detail"])
        self.assertEqual(ccc["age_seconds"], 3600)  # newest occurrence
        self.assertTrue(ccc["id"].startswith("log:CCC-GH:"))

    def test_log_tail_parse_is_cached_by_mtime_and_size(self):
        self._write_log([_stamp(NOW - 60) + " UTC  Q               ERROR    boom failed: x"])
        self.collect()
        key = wt_alerts._log_cache["key"]
        self.assertIsNotNone(key)
        self.collect()
        self.assertEqual(wt_alerts._log_cache["key"], key)


class TestServiceAlerts(_Fixture):
    def test_stopped_daemon_is_critical_and_first(self):
        self._write_launch_failures({
            "CCC:claude": {"queue": "CCC", "engine": "claude", "reason": "engine authentication required",
                           "failed_at": NOW - 10, "cooldown_until": NOW + 300},
        })
        out = self.collect(service_status={"ok": True, "installed": True, "state": "stopped"})
        self.assertEqual(out["alerts"][0]["kind"], "service_down")
        self.assertEqual(out["alerts"][0]["severity"], "critical")
        self.assertEqual(out["alerts"][0]["id"], "service:watchtower")
        self.assertEqual(out["alerts"][1]["kind"], "launch_failure")

    def test_online_or_not_installed_raises_nothing(self):
        self.assertEqual(self.collect()["alerts"], [])
        out = self.collect(service_status={"ok": True, "installed": False, "state": "stopped"})
        self.assertEqual(out["alerts"], [])

    def test_outage_start_is_sticky_until_back_online(self):
        stopped = {"ok": True, "installed": True, "state": "stopped"}
        a1 = self.collect(service_status=stopped, now=NOW)["alerts"][0]
        a2 = self.collect(service_status=stopped, now=NOW + 600)["alerts"][0]
        self.assertEqual(a1["ts"], a2["ts"])
        self.collect(now=NOW + 700)  # online resets
        a3 = self.collect(service_status=stopped, now=NOW + 800)["alerts"][0]
        self.assertEqual(a3["ts"], NOW + 800)


class TestAcks(_Fixture):
    def test_ack_hides_alert_until_a_newer_occurrence(self):
        self._write_launch_failures({
            "OPS:kimi": {"queue": "OPS", "engine": "kimi", "reason": "engine api unavailable",
                         "failed_at": NOW - 100, "cooldown_until": NOW + 200},
        })
        self.assertEqual(self.collect()["total"], 1)
        wt_alerts.ack_wt_alerts(["launch:OPS:kimi"], path=self.acks, now=NOW)
        out = self.collect(now=NOW + 1)
        self.assertEqual(out["alerts"], [])
        self.assertEqual(out["acked"], 1)
        self.assertEqual(out["total"], 0)
        # Same key fails again, later than the ack: it must re-surface.
        self._write_launch_failures({
            "OPS:kimi": {"queue": "OPS", "engine": "kimi", "reason": "engine api unavailable",
                         "failed_at": NOW + 50, "cooldown_until": NOW + 900},
        })
        out = self.collect(now=NOW + 60)
        self.assertEqual([a["id"] for a in out["alerts"]], ["launch:OPS:kimi"])

    def test_include_acked_marks_rows_and_orders_them_last(self):
        self._write_launch_failures({
            "A:claude": {"queue": "A", "engine": "claude", "reason": "r", "failed_at": NOW - 5},
            "B:claude": {"queue": "B", "engine": "claude", "reason": "r", "failed_at": NOW - 5},
        })
        wt_alerts.ack_wt_alerts(["launch:A:claude"], path=self.acks, now=NOW)
        out = self.collect(include_acked=True, now=NOW + 1)
        self.assertEqual([a["id"] for a in out["alerts"]], ["launch:B:claude", "launch:A:claude"])
        self.assertEqual([a["acked"] for a in out["alerts"]], [False, True])

    def test_ack_file_is_pruned_and_written_atomically(self):
        wt_alerts.ack_wt_alerts(["old"], path=self.acks, now=NOW - 40 * 86400)
        acks = wt_alerts.ack_wt_alerts(["new"], path=self.acks, now=NOW)
        self.assertEqual(set(acks), {"new"})
        self.assertEqual(set(json.loads(self.acks.read_text())), {"new"})
        self.assertFalse((self.acks.parent / (self.acks.name + ".tmp")).exists())


class TestStaticWiring(unittest.TestCase):
    def test_alert_strip_sits_above_the_queue_health_list(self):
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        strip = html.index('id="queueAlertStrip"')
        health = html.index('id="queueHealthStrip"')
        self.assertLess(strip, health)
        self.assertIn('id="queueAlertStrip" role="region" aria-label="WatchTower errors" aria-live="polite" hidden', html)

    def test_frontend_polls_and_acks_the_alert_api(self):
        js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("backgroundApiFetch('/api/watchtower/alerts'", js)
        self.assertIn("fetch('/api/watchtower/alerts/ack'", js)
        self.assertIn("function _renderQueueAlertStrip(", js)
        self.assertIn("_fetchWtAlerts(force).then(_renderQueueAlertStrip)", js)
        self.assertIn("class=\"fq-alerts-ack-all\"", js)
        css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn(".fq-alerts[hidden] { display: none; }", css)
        self.assertIn(".fq-alert-ack", css)

    def test_server_routes_are_registered(self):
        src = (PROJECT_ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('_adopt_ccc_module("wt_alerts")', src)
        self.assertIn('elif path == "/api/watchtower/alerts":', src)
        self.assertIn('if path == "/api/watchtower/alerts/ack":', src)


if __name__ == "__main__":
    unittest.main()
