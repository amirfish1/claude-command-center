import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkerBadgeCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        cls.app_css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

    def render_badge(self, data):
        start = self.app_js.index("  function renderWorkerBadge(data) {")
        end = self.app_js.index("  function renderControlPlaneStatus(data) {", start)
        render_source = self.app_js[start:end]
        script = (
            """
const classes = new Set();
const $workerBadge = {
  classList: {
    add: (...names) => names.forEach(name => classes.add(name)),
    remove: (...names) => names.forEach(name => classes.delete(name)),
  },
  title: "",
  ariaLabel: "",
  setAttribute: (_name, value) => { $workerBadge.ariaLabel = value; },
};
const $workerWord = { textContent: "" };
const $workerCount = { textContent: "", hidden: true };
"""
            + render_source
            + f"""
renderWorkerBadge({json.dumps(data)});
console.log(JSON.stringify({{
  word: $workerWord.textContent,
  count: $workerCount.textContent,
  hidden: $workerCount.hidden,
  title: $workerBadge.title,
  classes: Array.from(classes),
}}));
"""
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "TZ": "America/Los_Angeles"},
        )
        return json.loads(result.stdout)

    def test_online_worker_shows_last_restart_time_without_job_count(self):
        badge = self.render_badge(
            {
                "ok": True,
                "worker": {
                    "capabilities": ["engine-execution-v1"],
                    "started_at": 1785093480,
                },
                "active": 2,
                "queued": 1,
                "uncertain": 5,
                "drain": {"enabled": False},
            }
        )
        self.assertEqual(badge["word"], "Worker restarted Jul 26, 12:18 PM")
        self.assertTrue(badge["hidden"])
        self.assertNotIn("needs review", badge["title"].lower())
        self.assertNotIn("is-uncertain", badge["classes"])

    def test_paused_worker_has_no_ambiguous_count(self):
        badge = self.render_badge(
            {
                "ok": True,
                "worker": {"capabilities": ["engine-execution-v1"]},
                "active": 0,
                "queued": 4,
                "uncertain": 0,
                "drain": {"enabled": True},
            }
        )
        self.assertEqual(badge["word"], "Worker paused")
        self.assertTrue(badge["hidden"])

    def test_worker_label_does_not_wrap(self):
        start = self.app_css.index(".worker-word {")
        end = self.app_css.index("}", start)
        self.assertIn("white-space: nowrap", self.app_css[start:end])


if __name__ == "__main__":
    unittest.main()
