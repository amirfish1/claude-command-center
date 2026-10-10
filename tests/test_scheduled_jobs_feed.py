"""Jobs feed: parsing/filtering of canned systemctl/journalctl output (pure)."""
import subprocess
import unittest
from unittest import mock

from ccc_server import scheduled_jobs_feed as f

CANNED = """@@CCCJOB:TIMER bym-ship.timer
Unit=bym-ship.service
TimersCalendar={ OnCalendar=*-*-* 19:00:00 America/Los_Angeles ; next_elapse=Wed 2026-09-30 02:00:00 UTC }
TimersCalendar={ OnCalendar=*-*-* 09:00:00 America/Los_Angeles ; next_elapse=Wed 2026-09-30 16:00:00 UTC }
NextElapseUSecRealtime=Wed 2026-09-30 02:00:00 UTC
LastTriggerUSec=Tue 2026-09-29 16:00:01 UTC
ActiveState=active
UnitFileState=enabled
@@CCCJOB:SVC
Description=BYM scheduled release — pin next, PR to main
WorkingDirectory=/home/hermes/Apps/BYM+Finie
ExecStart={ path=/bin/bash ; argv[]=/bin/bash scripts/x.sh ; ignore_errors=no }
ActiveState=inactive
Result=success
ExecMainStatus=0
ExecMainStartTimestamp=@1790697601
ExecMainExitTimestamp=@1790697612
InvocationID=abc
@@CCCJOB:HIST
1790647213.1 host systemd[1]: bym-ship.service: Deactivated successfully.
1790697612.6 host systemd[1]: bym-ship.service: Failed with result 'exit-code'.
1790697700.6 host systemd[1]: bym-ship.service: Deactivated successfully.
@@CCCJOB:OUT
starting
Finished bym-ship.service - x.
bym-ship.service: Consumed 3s CPU time.
filed BECKY-TEACH-4 and BECKY-12 -> https://github.com/acme/repo/issues/125
opened https://github.com/acme/repo/pull/1857 and PR #9; SHA-256 UTF-8 2026-09-29 v1.2.3-4
CCC_OUTCOME: merged PR #12
\x1b[32mdone\x1b[0m
@@CCCJOB:ENDUNIT
@@CCCJOB:TIMER off.timer
Unit=off.service
TimersCalendar={ OnCalendar=*-*-* 03:15:00 ; next_elapse=(null) }
NextElapseUSecRealtime=
LastTriggerUSec=
ActiveState=inactive
UnitFileState=disabled
@@CCCJOB:SVC
Description=off
ExecStart={ path=/bin/bash ; argv[]=/bin/bash /opt/ccc-cloud/scripts/backup.sh ; ignore_errors=no }
ActiveState=inactive
Result=success
ExecMainStatus=0
ExecMainStartTimestamp=
InvocationID=
@@CCCJOB:HIST
@@CCCJOB:OUT
@@CCCJOB:ENDUNIT
@@CCCJOB:TIMER bad.timer
Unit=bad.service
TimersMonotonic={ OnUnitActiveUSec=1h ; next_elapse=Wed 2026-09-30 02:00:00 UTC }
NextElapseUSecRealtime=Wed 2026-09-30 02:00:00 UTC
UnitFileState=enabled
@@CCCJOB:SVC
Description=bad
ActiveState=failed
Result=exit-code
ExecMainStatus=3
ExecMainStartTimestamp=@1790697601
ExecMainExitTimestamp=@1790697661
@@CCCJOB:HIST
1790697661.0 host systemd[1]: bad.service: Failed with result 'exit-code'.
@@CCCJOB:OUT
@@CCCJOB:ENDUNIT
@@CCCJOB:END
"""


class HermesParse(unittest.TestCase):
    def setUp(self):
        self.jobs = {j["name"]: j for j in f.parse_hermes_output(CANNED)}

    def test_ok_job(self):
        j = self.jobs["bym-ship"]
        self.assertEqual(j["id"], "hermes:bym-ship.service")
        self.assertEqual(j["last_duration_s"], 11.0)
        self.assertEqual(j["exit_code"], 0)
        self.assertTrue(j["enabled"])
        self.assertEqual([h["ok"] for h in j["history"]], [True, False, True])
        self.assertEqual(j["next_run_at"], "2026-09-30T02:00:00+00:00")

    def test_outcome_prefers_ccc_outcome_and_strips_ansi(self):
        self.assertEqual(self.jobs["bym-ship"]["outcome"], "merged PR #12")
        self.assertEqual(self.jobs["bym-ship"]["outcome_kind"], "summary")
        self.assertEqual(f.pick_outcome(["a", "\x1b[32mdone\x1b[0m", ""]), ("done", "output"))

    def test_outcome_behind_log_timestamp(self):
        # CCC-1240: bym-ship's log() stamps every line, so the marker sits behind
        # a timestamp; it must still win over the chip/raw-output fallback.
        lines = ["2026-09-30T14:27:37Z CCC_OUTCOME: Shipped PR #1884 (3 commits).", "trailing noise"]
        self.assertEqual(f.pick_outcome(lines), ("Shipped PR #1884 (3 commits).", "summary"))
        self.assertEqual(f.pick_outcome(["[2026-09-30 14:27:37.1+00:00] CCC_OUTCOME: ok"]), ("ok", "summary"))
        self.assertEqual(f.pick_outcome(["NOTIFY: CCC_OUTCOME: nope"]), ("NOTIFY: CCC_OUTCOME: nope", "output"))

    def test_outcome_drops_systemd_lines(self):
        lines = ["Finished x.service - y.", "x.service: Deactivated successfully.", "x.service: Consumed 3s CPU time."]
        self.assertEqual(f.pick_outcome(lines), ("", ""))
        self.assertEqual(self.jobs["bad"]["outcome"], "")

    def test_description_project_and_dashes(self):
        j = self.jobs["bym-ship"]
        self.assertEqual(j["description"], "BYM scheduled release: pin next, PR to main")
        self.assertEqual((j["project"], j["repo_path"]), ("BYM+Finie", "/home/hermes/Apps/BYM+Finie"))
        self.assertEqual(self.jobs["off"]["project"], "ccc-cloud")
        self.assertEqual(self.jobs["bad"]["project"], "Other")

    def test_ticket_extraction(self):
        refs = {(t["kind"], t["ref"]) for t in self.jobs["bym-ship"]["tickets"]}
        self.assertIn(("watchtower", "BECKY-TEACH-4"), refs)
        self.assertIn(("watchtower", "BECKY-12"), refs)
        self.assertIn(("issue", "#125"), refs)
        self.assertIn(("pr", "PR #1857"), refs)
        self.assertIn(("pr", "PR #9"), refs)
        self.assertNotIn(("watchtower", "SHA-256"), refs)
        self.assertNotIn(("watchtower", "UTF-8"), refs)
        self.assertFalse([r for k, r in refs if k == "watchtower" and r.startswith(("v1", "2026"))])
        self.assertEqual(len(refs), len(self.jobs["bym-ship"]["tickets"]))

    def test_timeline(self):
        tl = self.jobs["bym-ship"]["timeline"]
        self.assertEqual(tl["kind"], "times")
        self.assertEqual(len(tl["minutes"]), 2)
        self.assertEqual(self.jobs["bad"]["timeline"], {"kind": "interval", "interval_s": 3600, "minutes": [], "weekdays": []})

    def test_disabled_and_failed(self):
        self.assertEqual(self.jobs["off"]["status"], "disabled")
        self.assertIsNone(self.jobs["off"]["next_run_at"])
        self.assertEqual(self.jobs["bad"]["status"], "failed")
        self.assertEqual(self.jobs["bad"]["exit_code"], 3)
        self.assertEqual(self.jobs["bad"]["schedule"], "Every 1h")

    def test_sorting_recent_first_disabled_last(self):
        order = [j["name"] for j in f.sort_jobs(list(self.jobs.values()))]
        self.assertEqual(order[0], "bad")  # same start, name tie-break
        self.assertEqual(order[-1], "off")

    def test_summary(self):
        s = f.summarize(list(self.jobs.values()))
        self.assertEqual((s["failed"], s["disabled"], s["attention"]), (1, 1, 1))


class Schedules(unittest.TestCase):
    def test_utc_daily_and_weekly(self):
        line = "TimersCalendar={ OnCalendar=*-*-* 08:15:00 UTC ; next_elapse=x }"
        self.assertRegex(f.format_systemd_schedule([line]), r"^Daily \d\d:\d\d$")
        wk = "TimersCalendar={ OnCalendar=Sun *-*-* 14:30:00 UTC ; next_elapse=x }"
        self.assertRegex(f.format_systemd_schedule([wk]), r"^Weekly Sun \d\d:\d\d$")

    def test_launchd(self):
        self.assertEqual(f.format_launchd_schedule({"StartInterval": 3600}), "Every 1h")
        self.assertEqual(
            f.format_launchd_schedule({"StartCalendarInterval": [{"Hour": 9, "Minute": 15}, {"Hour": 21, "Minute": 15}]}),
            "Daily 09:15, 21:15")
        self.assertEqual(
            f.format_launchd_schedule({"StartCalendarInterval": {"Weekday": 0, "Hour": 7, "Minute": 15}}),
            "Weekly Sun 07:15")

    def test_launchd_wrapper_and_project(self):
        d = {"Label": "com.x.job", "ProgramArguments": [
            "/Users/u/dev/ops/launchd-jobs/job", "com.x.job", "/bin/bash", "/Users/u/dev/ops/job.sh"]}
        self.assertEqual(f.script_path(d), "/Users/u/dev/ops/job.sh")
        self.assertEqual(f.project_from_paths(f.launchd_program_paths(d))[0], "ops")
        self.assertEqual(f.project_from_paths(["/Users/u/dev/tools/indexing/.venv/bin/x"])[0], "indexing")
        self.assertEqual(f.project_from_paths(["/etc/foo"]), ("Other", None))
        tl = f.launchd_timeline({"StartCalendarInterval": {"Weekday": 0, "Hour": 7, "Minute": 15}})
        self.assertEqual((tl["minutes"], tl["weekdays"]), ([435], ["Sun"]))

    def test_laptop_filter_drops_daemons(self):
        self.assertTrue(f.is_scheduled_plist({"StartInterval": 60}))
        self.assertTrue(f.is_scheduled_plist({"StartCalendarInterval": {"Hour": 1}}))
        self.assertFalse(f.is_scheduled_plist({"KeepAlive": True, "RunAtLoad": True}))

    def test_laptop_stale_rules(self):
        now = 1_000_000.0
        kw = dict(pid=None, exit_code=0, loaded=True, now=now)
        self.assertEqual(f.laptop_status(last_run_epoch=now - 4000, period_s=3600, **kw), "ok")
        self.assertEqual(f.laptop_status(last_run_epoch=now - 3 * 3600 - 1, period_s=3600, **kw), "stale")
        self.assertEqual(f.laptop_status(last_run_epoch=now - 2 * 86400 - 1, period_s=86400, **kw), "stale")
        self.assertEqual(f.laptop_status(last_run_epoch=now, period_s=60, **{**kw, "exit_code": 1}), "failed")
        self.assertEqual(f.laptop_status(last_run_epoch=now, period_s=60, **{**kw, "loaded": False}), "disabled")


class PerfBudget(unittest.TestCase):
    def test_hermes_collect_is_one_ssh(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=CANNED, stderr="")

        with mock.patch("platform.system", return_value="Darwin"), \
                mock.patch.object(f.subprocess, "run", fake_run):
            jobs, err = f.collect_hermes()
        self.assertIsNone(err)
        self.assertEqual(len(jobs), 3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "ssh")

    def test_offline_keeps_last_good(self):
        f._STATE["hermes"].update(jobs=[{"name": "x", "status": "ok", "host": "hermes"}], at=1.0, ok_at=1.0, error=None)
        f._STATE["laptop"].update(jobs=[], at=1.0)
        with mock.patch.object(f, "collect_laptop", return_value=[]), \
                mock.patch.object(f, "collect_hermes", return_value=(None, "ssh down")):
            f.refresh_now()
        p = f.build_payload()
        self.assertEqual(p["hosts"]["hermes"]["status"], "offline")
        self.assertTrue(p["hosts"]["hermes"]["stale_data"])
        self.assertEqual(len(p["jobs"]), 1)


class LocalHostTest(unittest.TestCase):
    def test_payload_names_the_local_host(self):
        with mock.patch.object(f.platform, "system", return_value="Linux"):
            self.assertEqual(f.build_payload()["local_host"], "hermes")
        with mock.patch.object(f.platform, "system", return_value="Darwin"):
            self.assertEqual(f.build_payload()["local_host"], "laptop")


if __name__ == "__main__":
    unittest.main()
