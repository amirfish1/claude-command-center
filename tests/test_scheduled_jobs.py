# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import json
import tempfile
from pathlib import Path

from ccc_server.scheduled_jobs import (
    _parse_iso,
    _format_schedule,
    _read_file_tail,
    _collect_local_launchd_jobs,
    _collect_hermes_systemd_jobs,
    collect_scheduled_jobs,
    get_scheduled_job_log,
)


class TestScheduledJobs(unittest.TestCase):
    def test_parse_iso(self):
        self.assertIsNone(_parse_iso(None))
        self.assertIsNone(_parse_iso(0))
        # Microsecond timestamp
        ts_us = 1789613400000000
        iso = _parse_iso(ts_us)
        self.assertIsNotNone(iso)
        self.assertTrue(iso.startswith("2026-"))

    def test_format_schedule(self):
        self.assertEqual(_format_schedule({}), "On demand")
        self.assertEqual(_format_schedule({"StartInterval": 900}), "Every 15m")
        self.assertEqual(_format_schedule({"StartInterval": 3600}), "Every 1h")
        self.assertEqual(_format_schedule({"StartInterval": 45}), "Every 45s")
        self.assertEqual(
            _format_schedule({"StartCalendarInterval": {"Hour": 9, "Minute": 0}}),
            "Daily at 09:00",
        )
        self.assertEqual(
            _format_schedule({"StartCalendarInterval": {"Minute": 30}}),
            "Hourly at :30",
        )
        self.assertEqual(_format_schedule({"RunAtLoad": True}), "At login / load")

    def test_read_file_tail(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            for i in range(100):
                f.write(f"line {i}\n")
            f.flush()
            temp_path = f.name

        try:
            tail = _read_file_tail(temp_path, max_lines=5)
            lines = tail.strip().splitlines()
            self.assertEqual(len(lines), 5)
            self.assertEqual(lines[-1], "line 99")
            self.assertEqual(lines[0], "line 95")
        finally:
            Path(temp_path).unlink(missing_ok=True)

        self.assertIsNone(_read_file_tail("/nonexistent/path/to/log.txt"))

    def test_collect_local_launchd_jobs(self):
        jobs = _collect_local_launchd_jobs()
        self.assertIsInstance(jobs, list)
        for job in jobs:
            self.assertTrue(job["id"].startswith("laptop:"))
            self.assertEqual(job["host"], "laptop")
            self.assertEqual(job["manager"], "launchd")
            self.assertIn(job["status"], ("running", "success", "failed", "idle", "unloaded"))

    def test_collect_hermes_mocked(self):
        fake_timers = [
            {
                "unit": "bym-senior-review.timer",
                "activates": "bym-senior-review.service",
                "next": 1789613400000000,
                "last": 1789612201602013,
            }
        ]
        fake_show_output = (
            "Result=success\n"
            "ExecMainStatus=0\n"
            "Id=bym-senior-review.service\n"
            "ActiveState=inactive\n"
            "SubState=dead\n"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps(fake_timers), stderr=""),
                MagicMock(returncode=0, stdout=fake_show_output, stderr=""),
            ]
            jobs, host_info = _collect_hermes_systemd_jobs()
            self.assertEqual(host_info["status"], "online")
            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            self.assertEqual(job["id"], "hermes:bym-senior-review.service")
            self.assertEqual(job["name"], "bym-senior-review")
            self.assertEqual(job["status"], "success")
            self.assertEqual(job["exit_code"], 0)

    def test_collect_scheduled_jobs_structure(self):
        payload = collect_scheduled_jobs(force=True)
        self.assertTrue(payload["ok"])
        self.assertIn("hosts", payload)
        self.assertIn("laptop", payload["hosts"])
        self.assertIn("hermes", payload["hosts"])
        self.assertIn("jobs", payload)
        self.assertIn("summary", payload)

    def test_get_scheduled_job_log_missing(self):
        res = get_scheduled_job_log("laptop:nonexistent_service_12345")
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
