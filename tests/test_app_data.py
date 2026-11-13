"""Per-app data store: round trip, key and app validation, size cap."""
import json
import tempfile
import unittest
from pathlib import Path

from ccc_server import app_data as ad


def make_app(base, app_id="demo"):
    d = Path(base) / app_id
    d.mkdir()
    (d / "app.json").write_text(json.dumps({"label": "Demo"}))
    return d


class AppDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.folder = make_app(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_and_plain_file(self):
        val = {"book": {"2026-12-18": {"nl": True, "d": False}}, "hidden": ["2026-11-20"]}
        res, st = ad.write({"app": "demo", "key": "ski-state", "value": val}, self.base)
        self.assertEqual(st, 200)
        self.assertTrue(res["ok"])
        got, st = ad.read("demo", "ski-state", self.base)
        self.assertEqual((st, got["value"]), (200, val))
        on_disk = json.loads((self.folder / "data" / "ski-state.json").read_text())
        self.assertEqual(on_disk["value"], val)
        self.assertEqual([p.name for p in (self.folder / "data").iterdir()], ["ski-state.json"])

    def test_missing_key_reads_null(self):
        got, st = ad.read("demo", "nothing", self.base)
        self.assertEqual((st, got["value"]), (200, None))

    def test_overwrite(self):
        ad.write({"app": "demo", "key": "k", "value": 1}, self.base)
        ad.write({"app": "demo", "key": "k", "value": 2}, self.base)
        self.assertEqual(ad.read("demo", "k", self.base)[0]["value"], 2)

    def test_bad_keys_and_traversal(self):
        for key in ("", "../x", "a/b", "A", "x.json", "a" * 41, "..", None, 5):
            res, st = ad.write({"app": "demo", "key": key, "value": 1}, self.base)
            self.assertEqual(st, 400, key)
            self.assertEqual(ad.read("demo", key if isinstance(key, str) else "", self.base)[1], 400)
        self.assertFalse((self.folder / "data").exists())
        self.assertFalse((Path(self.base) / "x.json").exists())

    def test_unknown_or_bad_app(self):
        for app in ("nope", "../demo", "", None):
            self.assertEqual(ad.write({"app": app, "key": "k", "value": 1}, self.base)[1], 404, app)
            self.assertEqual(ad.read(app or "", "k", self.base)[1], 404, app)

    def test_size_cap(self):
        big = "x" * (ad.MAX_VALUE_BYTES + 10)
        res, st = ad.write({"app": "demo", "key": "k", "value": big}, self.base)
        self.assertEqual(st, 413)
        self.assertFalse((self.folder / "data" / "k.json").exists())

    def test_value_required_and_non_dict(self):
        self.assertEqual(ad.write({"app": "demo", "key": "k"}, self.base)[1], 400)
        self.assertEqual(ad.write(["x"], self.base)[1], 400)


if __name__ == "__main__":
    unittest.main()
