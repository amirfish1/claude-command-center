"""App routines: schedule parsing, due logic, script runs and state."""
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

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

    def test_stamp_follows_the_apps_own_files(self):
        d = make_app(self.base, [SCRIPT])
        (d / "index.html").write_text("<main></main>")
        (d / "trips.json").write_text("[]")
        first = ar.describe("demo", self.base)["stamp"]
        # CCC's own state and the page's saved clicks are not a change.
        (d / ar.STATE_NAME).write_text("{}")
        (d / "data").mkdir()
        (d / "data" / "ticks.json").write_text("[1]")
        self.assertEqual(ar.describe("demo", self.base)["stamp"], first)
        (d / "trips.json").write_text('[{"id": "jan"}]')
        data = ar.describe("demo", self.base)["stamp"]
        self.assertEqual(data["page"], first["page"])
        self.assertNotEqual(data["files"], first["files"])
        (d / "index.html").write_text("<main>new</main>")
        page = ar.describe("demo", self.base)["stamp"]
        self.assertNotEqual(page["page"], data["page"])
        self.assertEqual(page["files"], data["files"])

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

    def test_update_and_delete(self):
        d = make_app(self.base, [SCRIPT, dict(SCRIPT, id="two", name="Two")])
        res, st = ar.update_routine({"app": "demo", "id": "hello", "name": "  Morning  hi ",
                                     "icon": "☀", "schedule": "Every 6H"}, self.base)
        self.assertEqual(st, 200)
        meta = json.loads((d / "app.json").read_text())
        first = meta["routines"][0]
        self.assertEqual((first["name"], first["icon"], first["schedule"]), ("Morning hi", "☀", "every 6h"))
        self.assertEqual(first["run"], SCRIPT["run"])  # untouched fields stay
        self.assertEqual(meta["routines"][1]["name"], "Two")
        for bad in ({"schedule": "every 1m"}, {"schedule": "whenever"}, {"name": " "}, {"icon": ""}, {}):
            self.assertEqual(ar.update_routine(dict(bad, app="demo", id="hello"), self.base)[1], 400, bad)
        self.assertEqual(ar.update_routine({"app": "demo", "id": "nope", "name": "x"}, self.base)[1], 404)
        ar.set_enabled("demo", "two", False, self.base)
        self.assertEqual(ar.delete_routine("demo", "two", self.base)[1], 200)
        self.assertEqual([r["id"] for r in ar.describe("demo", self.base)["routines"]], ["hello"])
        self.assertNotIn("two", ar.load_state(d)["routines"])
        self.assertEqual(ar.delete_routine("demo", "two", self.base)[1], 404)


class AgentDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.defaults = self.base / "spawn-defaults.json"
        self.folder = make_app(self.base, [])
        self.patch = patch.object(ar, "SPAWN_DEFAULTS_FILE", self.defaults)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def save(self, **values):
        self.defaults.write_text(json.dumps(values))

    def test_builder_uses_worker_settings_and_rereads_for_new_agent(self):
        self.save(engine="claude", models={"claude": "session-model"},
                  worker_engine="codex", worker_model="worker-model",
                  worker_reasoning_effort="high", worker_auto_compact_k=300)
        with patch.object(ar, "_post_json", return_value={"session_id": "first"}) as post:
            self.assertEqual(ar.ensure_agent("demo", 0, apps_dir=self.base)[1], 200)
            payload = post.call_args.args[2]
            self.assertEqual({k: payload[k] for k in
                              ("engine", "model", "reasoning_effort", "auto_compact_k")},
                             {"engine": "codex", "model": "worker-model",
                              "reasoning_effort": "high", "auto_compact_k": 300})
            self.save(worker_engine="claude", worker_model="changed-model",
                      worker_reasoning_effort="medium")
            # An existing conversation is reused, not silently replaced.
            ar.ensure_agent("demo", 0, apps_dir=self.base)
            self.assertEqual(post.call_count, 1)
            ar.ensure_agent("demo", 0, fresh=True, apps_dir=self.base)
            self.assertEqual(post.call_args.args[2]["model"], "changed-model")
            self.assertEqual(post.call_args.args[2]["engine"], "claude")

    def test_agent_routine_uses_same_worker_defaults(self):
        self.save(worker_engine="codex", worker_model="routine-model",
                  worker_reasoning_effort="medium")
        routine = {"id": "watch", "name": "Watch", "prompt": "check", "kind": "agent"}
        with patch.object(ar, "_post_json", return_value={"session_id": "run"}) as post:
            ar._run_agent(self.folder, "demo", "Demo", routine, 0, 1)
            self.assertEqual(post.call_args.args[2]["model"], "routine-model")
            self.assertEqual(post.call_args.args[2]["engine"], "codex")

    def test_blank_worker_values_do_not_inherit_another_engines_model_or_effort(self):
        self.save(engine="claude", reasoning_effort="high",
                  models={"claude": "session-model"}, worker_engine="codex",
                  worker_model="", worker_reasoning_effort="")
        self.assertEqual(ar._agent_spawn_options(), {"engine": "codex", "reasoning_effort": ""})

    def test_unconfigured_or_inherited_worker_uses_session_defaults(self):
        self.assertEqual(ar._agent_spawn_options(), {})
        self.save(engine="codex", worker_engine="claude", worker_model="old-model",
                  worker_inherit_session=True)
        self.assertEqual(ar._agent_spawn_options(), {})


if __name__ == "__main__":
    unittest.main()


class AgentSuccessorTests(unittest.TestCase):
    """The app's agent follows its continuation chain to the newest session."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = make_app(self.tmp.name, [])
        (self.folder / ar.STATE_NAME).write_text(json.dumps(
            {"agent": {"session_id": "old-session-1", "started_at": 1}}))

    def tearDown(self):
        self.tmp.cleanup()

    def ident(self, **links):
        return {sid: {"continued_from_session_id": origin} for sid, origin in links.items()}

    def test_chain_resolves_to_newest(self):
        ident = self.ident(**{"mid-session-2": "old-session-1", "neon-session-3": "mid-session-2",
                              "unrelated-9": "someone-else"})
        self.assertEqual(ar.latest_successor("old-session-1", ident), "neon-session-3")
        self.assertEqual(ar.latest_successor("neon-session-3", ident), "neon-session-3")

    def test_cycle_terminates(self):
        ident = self.ident(**{"a-session-1": "b-session-2", "b-session-2": "a-session-1"})
        self.assertIn(ar.latest_successor("a-session-1", ident), {"a-session-1", "b-session-2"})

    def test_current_agent_forwards_and_persists(self):
        ident = self.ident(**{"mid-session-2": "old-session-1", "neon-session-3": "mid-session-2"})
        agent = ar.current_agent(self.folder, identity=ident)
        self.assertEqual(agent["session_id"], "neon-session-3")
        self.assertEqual(agent["earlier_session_ids"], ["old-session-1"])
        saved = json.loads((self.folder / ar.STATE_NAME).read_text())["agent"]
        self.assertEqual(saved["session_id"], "neon-session-3")
        self.assertEqual(saved["started_at"], 1)
        # Once persisted, a lineage source that forgot the link changes nothing.
        self.assertEqual(ar.current_agent(self.folder, identity={})["session_id"], "neon-session-3")

    def test_describe_and_ensure_agent_use_successor(self):
        ident = self.ident(**{"neon-session-3": "old-session-1"})
        with patch.object(ar, "_identity_map", return_value=ident):
            info = ar.describe("demo", self.tmp.name)
            self.assertEqual(info["agent"]["session_id"], "neon-session-3")
            res, status = ar.ensure_agent("demo", 1, apps_dir=self.tmp.name)
        self.assertEqual((status, res["session_id"], res["existing"]), (200, "neon-session-3", True))

    def test_no_lineage_leaves_agent_alone(self):
        with patch.object(ar, "_identity_map", return_value={}):
            self.assertEqual(ar.describe("demo", self.tmp.name)["agent"]["session_id"], "old-session-1")
