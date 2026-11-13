import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ccc_server import app_routines as ar
from ccc_server import app_templates as at


class TemplatesTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.apps = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def test_lists_defaults(self):
        ids = {t["id"] for t in at.list_templates()}
        self.assertEqual(ids, {"tracker", "research", "watch", "blank"})
        for t in at.list_templates():
            self.assertTrue(t["routines"], t["id"])

    def test_every_template_is_valid(self):
        for t in at.list_templates():
            res, status = at.create({"template": t["id"], "label": "My " + t["id"]}, self.apps)
            self.assertEqual(status, 200, res)
            folder = self.apps / res["id"]
            routines, _meta = ar.load_routines(folder)
            self.assertTrue(routines, t["id"])
            html = (folder / "index.html").read_text()
            self.assertIn("app-kit.js", html)
            self.assertIn(f'data-app="{res["id"]}"', html)
            self.assertNotIn("{{", html)
            for r in routines:
                if r["kind"] == "script":
                    out = subprocess.run([sys.executable] + r["run"][1:], cwd=folder,
                                         capture_output=True, text=True, timeout=30)
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertIn("summary", ar.parse_script_output(out.stdout))
                else:
                    self.assertTrue(r["prompt"])

    def test_label_icon_id(self):
        res, _ = at.create({"template": "tracker", "label": "Taxes <b>", "icon": "💸", "id": "taxes"}, self.apps)
        manifest = json.loads((self.apps / "taxes" / "app.json").read_text())
        self.assertEqual((manifest["label"], manifest["icon"]), ("Taxes <b>", "💸"))
        self.assertNotIn("<b>", (self.apps / "taxes" / "index.html").read_text().split("<title>")[1].split("</title>")[0])

    def test_id_collisions_and_validation(self):
        at.create({"template": "blank", "label": "Trips"}, self.apps)
        res, _ = at.create({"template": "blank", "label": "Trips"}, self.apps)
        self.assertEqual(res["id"], "trips-2")
        self.assertEqual(at.create({"template": "blank", "id": "trips"}, self.apps)[1], 409)
        self.assertEqual(at.create({"template": "blank", "id": "../x"}, self.apps)[1], 400)
        self.assertEqual(at.create({"template": "../etc"}, self.apps)[1], 404)
        self.assertEqual(at.create({"template": "nope"}, self.apps)[1], 404)

    def test_tracker_reminder_alerts(self):
        res, _ = at.create({"template": "tracker"}, self.apps)
        folder = self.apps / res["id"]
        from datetime import date, timedelta
        (folder / "items.json").write_text(json.dumps(
            [{"title": "Soon", "date": (date.today() + timedelta(days=1)).isoformat()}]))
        out = subprocess.run([sys.executable, "reminder.py"], cwd=folder, capture_output=True, text=True)
        self.assertTrue(json.loads(out.stdout)["alert"])


if __name__ == "__main__":
    unittest.main()
