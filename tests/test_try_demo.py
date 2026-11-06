"""Pin the static browser demo: it stays backend-free, relocatable and invented."""
import importlib.util
import pathlib
import re
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("build_demo", ROOT / "scripts" / "build_demo.py")
build_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_demo)


class TryDemoBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = pathlib.Path(cls.tmp.name) / "demo"
        build_demo.build(cls.out)
        cls.index = (cls.out / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_fake_backend_loads_before_any_app_code(self):
        backend = self.index.index('src="demo/backend.js"')
        self.assertLess(self.index.index('src="demo/dataset.js"'), backend)
        self.assertLess(backend, self.index.index("<script>"))
        self.assertLess(backend, self.index.index("static/app.js"))

    def test_bundle_has_no_root_absolute_static_paths(self):
        for path in [self.out / "index.html", *self.out.glob("static/*.js"), *self.out.glob("static/*.css")]:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"""["'`(]/static/""", text), path.name)

    def test_app_code_is_the_real_app(self):
        def normal(text):
            return build_demo.relativize(text)
        source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        built = (self.out / "static" / "app.js").read_text(encoding="utf-8")
        self.assertEqual(normal(source), built)

    def test_no_third_party_requests_in_the_page(self):
        self.assertNotIn("fonts.googleapis.com", self.index)
        self.assertNotIn("fonts.gstatic.com", self.index)

    def test_only_tracked_static_files_are_copied(self):
        tracked = {pathlib.PurePosixPath(p).as_posix() for p in build_demo.tracked_static_files()}
        for path in (self.out / "static").rglob("*"):
            if path.is_file():
                self.assertIn(path.relative_to(self.out).as_posix(), tracked)

    def test_demo_says_it_is_invented(self):
        self.assertIn("Invented sessions", (ROOT / "demo" / "tour.js").read_text(encoding="utf-8"))
        self.assertIn("demo, invented data", self.index)


class TryDemoData(unittest.TestCase):
    def test_data_stays_in_the_invented_world(self):
        data = (ROOT / "demo" / "dataset.js").read_text(encoding="utf-8")
        self.assertIn("Fictional Tea Co.", data)
        self.assertIsNone(re.search(r"(?<![\w/])/home/(?!demo)|/Users/|@[a-z0-9-]+\.(com|ai|io)|https?://", data))
        self.assertIsNone(re.search(r"\b(sk-|ghp_|xox[bp]-)", data))
        self.assertIsNone(re.search(r"\$\d", data), "the demo should not invent money figures")

    def test_every_kind_of_moment_is_present(self):
        data = (ROOT / "demo" / "dataset.js").read_text(encoding="utf-8")
        for kind, least in (("decide", 5), ("approve", 1), ("working", 8), ("ready", 3)):
            self.assertGreaterEqual(data.count("kind: '%s'" % kind), least, kind)


if __name__ == "__main__":
    unittest.main()
