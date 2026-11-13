import json
import tempfile
import unittest
from pathlib import Path

from ccc_server import app_feed


class AppFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.apps = Path(self.tmp.name)
        (self.apps / "demo").mkdir()
        (self.apps / "demo" / "app.json").write_text("{}")

    def test_append_and_read_newest_first(self):
        app_feed.append({"app": "demo", "title": "Old"}, self.apps, now=100)
        res, st = app_feed.append({"app": "demo", "title": "New", "url": "https://x.io/a",
                                   "image": "/app-static/demo/a.png", "routine": "scan"}, self.apps, now=200)
        self.assertEqual((st, res["count"]), (200, 2))
        items = app_feed.read("demo", self.apps)[0]["items"]
        self.assertEqual([i["title"] for i in items], ["New", "Old"])
        self.assertEqual(items[0]["routine"], "scan")

    def test_rejects_bad_input(self):
        self.assertEqual(app_feed.append({"app": "nope", "title": "x"}, self.apps)[1], 404)
        self.assertEqual(app_feed.append({"app": "../x", "title": "x"}, self.apps)[1], 404)
        self.assertEqual(app_feed.append({"app": "demo", "title": "  "}, self.apps)[1], 400)
        self.assertEqual(app_feed.read("nope", self.apps)[1], 404)

    def test_unsafe_urls_dropped(self):
        app_feed.append({"app": "demo", "title": "t", "url": "javascript:alert(1)",
                         "image": "//evil.com/x.png"}, self.apps)
        item = app_feed.read("demo", self.apps)[0]["items"][0]
        self.assertEqual((item["url"], item["image"]), ("", ""))

    def test_bounded_to_200_and_no_temp_left(self):
        for n in range(205):
            app_feed.append({"app": "demo", "title": f"t{n}"}, self.apps, now=n)
        data = json.loads((self.apps / "demo" / "feed.json").read_text())
        self.assertEqual(len(data), 200)
        self.assertEqual(data[-1]["title"], "t204")
        self.assertEqual([p.name for p in (self.apps / "demo").iterdir() if p.name.startswith(".feed-")], [])

    def test_corrupt_feed_recovers(self):
        (self.apps / "demo" / "feed.json").write_text("{not json")
        self.assertEqual(app_feed.append({"app": "demo", "title": "ok"}, self.apps)[1], 200)


if __name__ == "__main__":
    unittest.main()
