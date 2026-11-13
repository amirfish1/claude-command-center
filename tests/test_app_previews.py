import os
import tempfile
import time
import unittest
from pathlib import Path

from ccc_server import app_previews as ap


def _app(root, name):
    d = Path(root) / name
    d.mkdir()
    (d / "app.json").write_text("{}")
    (d / "index.html").write_text("hi")
    return d


class AppPreviewTests(unittest.TestCase):
    def test_stale_logic_and_throttle(self):
        with tempfile.TemporaryDirectory() as root:
            d = _app(root, "ski")
            self.assertEqual(ap.due_apps(root, now=1000, last_attempt={}), ["ski"])
            (d / "preview.png").write_bytes(b"x")
            past = time.time() - 100
            for f in d.iterdir():
                if f.name != "preview.png":
                    os.utime(f, (past, past))
            self.assertEqual(ap.due_apps(root, now=1000, last_attempt={}), [])
            os.utime(d / "index.html", None)
            os.utime(d / "preview.png", (past, past))
            self.assertEqual(ap.due_apps(root, now=1000, last_attempt={}), ["ski"])
            self.assertEqual(ap.due_apps(root, now=1000, last_attempt={"ski": 900}), [])
            self.assertEqual(ap.due_apps(root, now=2000, last_attempt={"ski": 900}), ["ski"])

    def test_state_file_does_not_make_stale(self):
        with tempfile.TemporaryDirectory() as root:
            d = _app(root, "a")
            (d / "preview.png").write_bytes(b"x")
            future = time.time() + 50
            os.utime(d / "preview.png", (future, future))
            (d / "ccc-state.json").write_text("{}")
            self.assertFalse(ap.is_stale(d))

    def test_request_rejects_bad_app(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(ap.request({"app": "../x"}, 1, root)[1], 404)
            self.assertEqual(ap.request({"app": "nope"}, 1, root)[1], 404)


if __name__ == "__main__":
    unittest.main()
