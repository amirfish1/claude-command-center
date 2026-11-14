"""Sharing an app: install from a folder, focus state, setup routines, suggestions."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ccc_server import app_routines as ar
from ccc_server import app_share as share

APPS = [
    {"id": "sessions", "label": "Sessions", "url": "/", "nav": "/", "builtin": True},
    {"id": "ski", "label": "Ski", "icon": "S", "url": "/view/ski", "nav": "/view/ski"},
]


def make_source(base, name="ccc-ski", nested=None, manifest=None):
    root = Path(base) / name
    folder = root / nested if nested else root
    folder.mkdir(parents=True)
    (folder / "app.json").write_text(json.dumps(manifest or {"label": "Ski"}))
    (folder / "index.html").write_text("<main></main>")
    return root, folder


class FocusTests(unittest.TestCase):
    def test_unset(self):
        self.assertEqual(share.focus_state({}, APPS), {"app": "", "home": ""})
        self.assertEqual(share.focus_state(None, APPS), {"app": "", "home": ""})

    def test_lock_unlock_forget(self):
        cfg = {}
        res, status = share.set_focus(cfg, "ski", APPS)
        self.assertEqual(status, 200)
        self.assertEqual(res["focus"], {"app": "ski", "home": "ski", "url": "/view/ski",
                                        "label": "Ski", "icon": "S"})
        # Unlock keeps the offer to lock again.
        res, _ = share.set_focus(cfg, "", APPS)
        self.assertEqual((res["focus"]["app"], res["focus"]["home"]), ("", "ski"))
        res, _ = share.set_focus(cfg, "", APPS, forget=True)
        self.assertEqual(res["focus"], {"app": "", "home": ""})
        self.assertEqual(cfg, {})

    def test_unknown_app_is_refused_and_ignored(self):
        cfg = {}
        self.assertEqual(share.set_focus(cfg, "nope", APPS)[1], 404)
        self.assertEqual(cfg, {})
        # A focus id whose app is gone (removed, switched off) never locks the rail.
        self.assertEqual(share.focus_state({"focus_app": "gone", "focus_home": "gone"}, APPS),
                         {"app": "", "home": ""})
        self.assertEqual(share.focus_state({"focus_app": ["ski"]}, APPS)["app"], "")

    def test_offline_write_keeps_other_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom-links.json"
            path.write_text(json.dumps({"apps": [{"id": "hub"}]}))
            share.write_focus_offline("ski", path)
            cfg = json.loads(path.read_text())
            self.assertEqual((cfg["focus_app"], cfg["focus_home"], cfg["apps"]),
                             ("ski", "ski", [{"id": "hub"}]))
            share.write_focus_offline("", path)
            self.assertEqual(json.loads(path.read_text()), {"apps": [{"id": "hub"}], "focus_home": "ski"})


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.apps = self.base / "apps"

    def test_install_from_folder_root(self):
        root, folder = make_source(self.base, manifest={
            "label": "Ski", "routines": [
                {"id": "setup", "kind": "script", "run": ["python3", "setup.py"], "setup": True}]})
        (folder / "ccc-state.json").write_text("{}")
        (folder / ".venv").mkdir()
        (folder / ".git").mkdir()
        res, status = share.install(str(root), self.apps)
        self.assertEqual(status, 200, res)
        # Repo name minus the ccc- prefix; another machine's state stays behind.
        self.assertEqual((res["id"], res["url"]), ("ski", "/view/ski"))
        self.assertEqual(res["setup"], [["python3", "setup.py"]])
        dest = self.apps / "ski"
        self.assertTrue((dest / "index.html").is_file())
        for left in ("ccc-state.json", ".venv", ".git"):
            self.assertFalse((dest / left).exists(), left)

    def test_install_finds_the_only_nested_app(self):
        root, _ = make_source(self.base, name="bundle", nested="trips")
        res, status = share.install(str(root), self.apps)
        self.assertEqual((status, res["id"]), (200, "trips"))

    def test_never_overwrites(self):
        root, _ = make_source(self.base)
        self.assertEqual(share.install(str(root), self.apps)[1], 200)
        (self.apps / "ski" / "index.html").write_text("mine")
        res, status = share.install(str(root), self.apps)
        self.assertEqual(status, 409)
        self.assertEqual((self.apps / "ski" / "index.html").read_text(), "mine")
        self.assertEqual(share.install(str(root), self.apps, app_id="ski2")[1], 200)

    def test_bad_sources(self):
        self.assertEqual(share.install("", self.apps)[1], 400)
        self.assertEqual(share.install(str(self.base / "missing"), self.apps)[1], 404)
        empty = self.base / "empty"
        empty.mkdir()
        self.assertEqual(share.install(str(empty), self.apps)[1], 400)
        root, _ = make_source(self.base)
        self.assertEqual(share.install(str(root), self.apps, app_id="../x")[1], 400)
        self.assertEqual(share.install(str(root), self.apps, app_id="sessions")[1], 400)
        self.assertFalse(self.apps.exists())

    def test_source_url(self):
        self.assertEqual(share.source_url("someone/ccc-ski"), "https://github.com/someone/ccc-ski.git")
        self.assertEqual(share.source_url("https://example.com/a/b.git"), "https://example.com/a/b.git")
        self.assertIsNone(share.source_url(str(self.base)))
        self.assertIsNone(share.source_url("./folder"))


class SetupAndSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.app = self.base / "demo"
        self.app.mkdir()
        (self.app / "setup.py").write_text("print('{\"summary\": \"Tools installed\"}')\n")
        (self.app / "app.json").write_text(json.dumps({
            "label": "Demo",
            "suggestions": [{"label": "Change the dates", "prompt": "Change the dates of: "},
                            "Search for hotels", {"label": ""}, 7],
            "routines": [
                {"id": "setup", "name": "Setup", "kind": "script", "setup": True,
                 "run": [sys.executable, "setup.py"]},
                {"id": "manual", "name": "Manual", "kind": "script",
                 "run": [sys.executable, "setup.py"]}],
        }))

    def _wait(self, rid):
        for _ in range(100):
            if ar.load_state(self.app)["routines"].get(rid, {}).get("status") not in (None, "running"):
                return
            time.sleep(0.05)
        self.fail("routine did not finish")

    def test_setup_runs_once_on_first_tick(self):
        # Only the setup routine starts by itself; an unscheduled one never does.
        self.assertEqual(ar.tick(0, self.base), [("demo", "setup")])
        self._wait("setup")
        info = ar.describe("demo", self.base)
        row = {r["id"]: r for r in info["routines"]}
        self.assertEqual((row["setup"]["status"], row["setup"]["summary"]), ("ok", "Tools installed"))
        self.assertTrue(row["setup"]["setup"])
        self.assertFalse(row["manual"]["setup"])
        self.assertEqual(row["manual"]["status"], "never")
        self.assertEqual(ar.tick(0, self.base), [])

    def test_failed_setup_is_not_retried_in_a_loop(self):
        (self.app / "setup.py").write_text("import sys\nprint('no network', file=sys.stderr)\nsys.exit(1)\n")
        self.assertEqual(ar.tick(0, self.base), [("demo", "setup")])
        self._wait("setup")
        self.assertEqual(ar.describe("demo", self.base)["routines"][0]["status"], "failed")
        self.assertEqual(ar.tick(0, self.base), [])

    def test_suggestions(self):
        self.assertEqual(ar.describe("demo", self.base)["suggestions"], [
            {"label": "Change the dates", "prompt": "Change the dates of: "},
            {"label": "Search for hotels", "prompt": "Search for hotels"}])


if __name__ == "__main__":
    unittest.main()
