"""App routines: schedule parsing, due logic, script runs and state."""
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from ccc_server import app_routines as ar


def make_app(base, routines, app_id="demo", extra=None):
    d = Path(base) / app_id
    d.mkdir()
    meta = {"label": "Demo", "routines": routines}
    meta.update(extra or {})
    (d / "app.json").write_text(json.dumps(meta))
    return d


SCRIPT = {"id": "hello", "name": "Hello", "schedule": "daily 08:00", "kind": "script",
          "run": [sys.executable, "hello.py"]}


class ScheduleTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(ar.parse_schedule("daily 08:00"), {"type": "daily", "hour": 8, "minute": 0})
        self.assertEqual(ar.parse_schedule("Every 6h"), {"type": "every", "seconds": 21600})
        self.assertEqual(ar.parse_schedule("every 30 min"), {"type": "every", "seconds": 1800})
        for bad in ("", "daily 25:00", "every 1m", "hourly", "every 0h", None):
            self.assertIsNone(ar.parse_schedule(bad), bad)

    def test_text(self):
        self.assertEqual(ar.schedule_text(ar.parse_schedule("daily 08:00")), "Every day at 8:00 AM")
        self.assertEqual(ar.schedule_text(ar.parse_schedule("daily 13:30")), "Every day at 1:30 PM")
        self.assertEqual(ar.schedule_text(ar.parse_schedule("every 12h")), "Every 12 hours")

    def test_interval_due(self):
        s = ar.parse_schedule("every 1h")
        self.assertLessEqual(ar.next_run(s, None, 1000), 1000)
        self.assertEqual(ar.next_run(s, 1000, 2000), 4600)

    def test_daily_due_and_missed_slot(self):
        s = ar.parse_schedule("daily 08:00")
        tz = "America/Los_Angeles"
        at = lambda h, day=10: datetime(2026, 11, day, h, 0, tzinfo=ar.ZoneInfo(tz)).timestamp()
        # Never ran, already past today's slot: due.
        self.assertLessEqual(ar.next_run(s, None, at(9), tz), at(9))
        # Ran after today's slot: next is tomorrow 08:00.
        self.assertEqual(ar.next_run(s, at(8) + 5, at(9), tz), at(8, 11))
        # Before today's slot, ran yesterday after the slot: today 08:00.
        self.assertEqual(ar.next_run(s, at(9, 9), at(7), tz), at(8))
        # Server was down over the slot: due once.
        self.assertLessEqual(ar.next_run(s, at(9, 8), at(7), tz), at(7))
        self.assertIsNone(ar.next_run(None, None, 0))


class RoutineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_load_skips_invalid(self):
        d = make_app(self.base, [
            SCRIPT, dict(SCRIPT),  # duplicate id
            {"id": "shell", "kind": "script", "run": "rm -rf /"},  # not an argv list
            {"id": "Bad Id", "kind": "script", "run": ["true"]},
            {"id": "noprompt", "kind": "agent"},
            {"id": "watch", "kind": "agent", "prompt": "check prices"},
        ])
        self.assertEqual([r["id"] for r in ar.load_routines(d)[0]], ["hello", "watch"])

    def test_app_dir_rejects_traversal(self):
        make_app(self.base, [])
        self.assertIsNone(ar.app_dir("../demo", self.base))
        self.assertIsNone(ar.app_dir("missing", self.base))
        self.assertIsNotNone(ar.app_dir("demo", self.base))

    def test_script_run_records_result(self):
        d = make_app(self.base, [SCRIPT])
        (d / "hello.py").write_text('import json;print("noise");print(json.dumps({"summary":"All good","alert":True}))')
        res, status = ar.run_now("demo", "hello", 0, self.base, wait=True)
        self.assertEqual((res["ok"], status), (True, 200))
        row = ar.describe("demo", self.base)["routines"][0]
        self.assertEqual((row["status"], row["summary"], row["alert"]), ("ok", "All good", True))
        self.assertEqual(len(row["history"]), 1)
        self.assertEqual(ar.overview(self.base)["demo"]["alerts"], 1)

    def test_script_failure(self):
        d = make_app(self.base, [SCRIPT])
        (d / "hello.py").write_text('import sys;sys.stderr.write("boom\\n");sys.exit(3)')
        ar.run_now("demo", "hello", 0, self.base, wait=True)
        row = ar.describe("demo", self.base)["routines"][0]
        self.assertEqual((row["status"], row["summary"]), ("failed", "boom"))

    def test_plain_last_line_is_summary(self):
        self.assertEqual(ar.parse_script_output("a\nb\n"), ("b", False, None))
        self.assertEqual(ar.parse_script_output(""), ("Done", False, None))

    def test_toggle_and_tick(self):
        d = make_app(self.base, [dict(SCRIPT, schedule="every 1h")])
        (d / "hello.py").write_text('print("ran")')
        ar.set_enabled("demo", "hello", False, self.base)
        self.assertEqual(ar.tick(0, self.base), [])
        ar.set_enabled("demo", "hello", True, self.base)
        self.assertEqual(ar.tick(0, self.base), [("demo", "hello")])
        deadline = time.time() + 10
        while time.time() < deadline and ar.describe("demo", self.base)["routines"][0]["status"] != "ok":
            time.sleep(0.05)
        self.assertEqual(ar.describe("demo", self.base)["routines"][0]["summary"], "ran")
        self.assertEqual(ar.tick(0, self.base), [])  # not due again for an hour

    def test_unknown_routine(self):
        make_app(self.base, [SCRIPT])
        self.assertEqual(ar.run_now("demo", "nope", 0, self.base)[1], 404)
        self.assertEqual(ar.set_enabled("nope", "hello", True, self.base)[1], 404)

    def test_agent_report(self):
        make_app(self.base, [{"id": "watch", "kind": "agent", "prompt": "check", "schedule": "every 12h"}])
        res, status = ar.report({"app": "demo", "id": "watch", "summary": "Prices  unchanged"}, 0, self.base)
        self.assertEqual(status, 200)
        row = ar.describe("demo", self.base)["routines"][0]
        self.assertEqual((row["status"], row["summary"]), ("ok", "Prices unchanged"))
        self.assertIn("/api/apps/routines/report", ar.agent_prompt("demo", "Demo", {"id": "watch", "name": "W", "prompt": "p"}, 8090))


if __name__ == "__main__":
    unittest.main()
