import json
import tempfile
import unittest
from pathlib import Path

from ccc_server import app_concierge, app_routines


class ConciergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.apps = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_slug_unique(self):
        (self.apps / "ski-trips").mkdir()
        slug, _ = app_concierge.create_app("ski trips", self.apps)
        self.assertEqual(slug, "ski-trips-2")
        self.assertTrue(app_routines.valid_id(slug))

    def test_empty_wish_rejected(self):
        res, status = app_concierge.concierge({"wish": " "}, 1, self.apps)
        self.assertEqual(status, 400)

    def test_create_files(self):
        slug, label = app_concierge.create_app("Track my plants", self.apps)
        meta = json.loads((self.apps / slug / "app.json").read_text())
        self.assertEqual(meta["label"], label)
        html = (self.apps / slug / "index.html").read_text()
        self.assertIn(f'data-app="{slug}"', html)
        self.assertIn("data-ccc-content", html)

    def test_flow_spawns_and_briefs(self):
        spawned, sent = [], []
        orig = app_routines._post_json
        app_routines._post_json = lambda port, path, body, timeout=60: (spawned.append(body) or {"session_id": "sid1"})
        try:
            res, status = app_concierge.concierge(
                {"wish": "watch the snow"}, 1, self.apps,
                inject=lambda p, s, t: (sent.append((s, t)) or {"ok": True}), sleep=lambda s: None)
        finally:
            app_routines._post_json = orig
        self.assertEqual(status, 200)
        self.assertEqual(res["session_id"], "sid1")
        self.assertTrue(res["briefed"])
        self.assertEqual(sent[0][0], "sid1")
        self.assertIn("at most 3 short questions", sent[0][1])
        self.assertEqual(res["url"], f"/view/{res['app']}")

    def test_spawn_failure(self):
        orig = app_routines._post_json
        app_routines._post_json = lambda *a, **k: {}
        try:
            res, status = app_concierge.concierge({"wish": "x thing"}, 1, self.apps)
        finally:
            app_routines._post_json = orig
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
