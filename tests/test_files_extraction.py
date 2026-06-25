"""Tests for the per-conversation file index (server-side extraction)."""

import importlib
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class TestCategorize(unittest.TestCase):
    def setUp(self):
        # Re-import server fresh; some sibling tests mutate sys.modules.
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def test_image_extensions_categorized_as_images(self):
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                    ".heic", ".bmp", ".tiff"):
            with self.subTest(ext=ext):
                self.assertEqual(
                    self.server._categorize_file_target("/tmp/x" + ext),
                    "images",
                )

    def test_pdf_categorized(self):
        self.assertEqual(self.server._categorize_file_target("/x/a.pdf"), "pdfs")

    def test_uppercase_extensions_normalized(self):
        # Real conversations contain `.PNG`, `.PDF`, etc. Categorizer must
        # be case-insensitive on the extension.
        self.assertEqual(self.server._categorize_file_target("/x/a.PDF"), "pdfs")
        self.assertEqual(self.server._categorize_file_target("/x/Y.JPEG"), "images")

    def test_excluded_extensions_return_none(self):
        # Code/scripts MUST NOT categorize — they're the load-bearing
        # security clamp on /api/reveal-file. If an attacker convinces the
        # extractor a `.sh` is a file, the modal could render it and the
        # opener would shell out. The whitelist is closed by design.
        for ext in (".py", ".sh", ".js", ".ts", ".rb", ".go", ".rs", ".app",
                    ".command", ".workflow", ".applescript",
                    ".json", ".yaml", ".yml", ".toml", ".css", ".sql",
                    ".lock", ".txt"):
            with self.subTest(ext=ext):
                self.assertIsNone(
                    self.server._categorize_file_target("/tmp/x" + ext),
                    f"{ext} must NOT categorize — it would weaken the opener clamp",
                )

    def test_no_extension_returns_none(self):
        self.assertIsNone(self.server._categorize_file_target("/tmp/somefile"))
        self.assertIsNone(self.server._categorize_file_target("https://example.com/"))

    def test_url_with_known_extension_categorizes(self):
        self.assertEqual(
            self.server._categorize_file_target("https://drive.google.com/foo.pdf"),
            "pdfs",
        )


class TestExtractor(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

        # Point _resolve_conversation_path at our fixture by patching
        # _conversation_dirs() to return the fixtures dir, where we
        # symlink/copy the fixture under the conversation-id name the
        # extractor expects. Simplest: monkey-patch the resolver itself.
        self.fixture = REPO / "tests" / "fixtures" / "files-extraction.jsonl"
        self._orig_resolve = self.server._resolve_conversation_path
        self.server._resolve_conversation_path = lambda cid: self.fixture

    def tearDown(self):
        self.server._resolve_conversation_path = self._orig_resolve

    def test_extracts_expected_files_per_category(self):
        result = self.server._extract_files_from_conversation("ignored")
        self.assertIn("groups", result)
        self.assertIn("count", result)
        self.assertFalse(result["truncated"])

        groups = result["groups"]

        def targets(cat):
            return [r["target"] for r in groups.get(cat, [])]

        self.assertEqual(set(targets("images")),
                         {"/Users/testuser/Desktop/diagram.png"})
        self.assertEqual(set(targets("pdfs")),
                         {"/Users/testuser/Apps/foo/notes.pdf",
                          "https://example.com/spec.pdf"})
        self.assertEqual(set(targets("presentations")),
                         {"/Users/testuser/Downloads/deck.pptx"})
        self.assertEqual(set(targets("videos")),
                         {"https://example.com/video.mp4"})
        self.assertEqual(set(targets("markdown")),
                         {"/Users/testuser/Apps/foo/intro.md"})
        self.assertEqual(set(targets("html")),
                         {"/Users/testuser/Apps/foo/report.html"})

        # Total == sum across non-empty groups.
        self.assertEqual(result["count"],
                         sum(len(v) for v in groups.values()))

    def test_excluded_extensions_never_appear(self):
        result = self.server._extract_files_from_conversation("ignored")
        all_targets = []
        for rows in result["groups"].values():
            all_targets.extend(r["target"] for r in rows)
        for t in all_targets:
            self.assertFalse(
                t.lower().endswith(".sh"),
                f"shell script leaked into extractor: {t}",
            )
            self.assertFalse(
                t.lower().endswith(".py"),
                f"python file leaked into extractor: {t}",
            )

    def test_de_duplicates_repeats(self):
        # `/Users/testuser/Apps/foo/intro.md` appears twice in the fixture
        # (tool_result + Bash command). Must collapse to one row.
        result = self.server._extract_files_from_conversation("ignored")
        md_targets = [r["target"] for r in result["groups"].get("markdown", [])]
        self.assertEqual(md_targets.count("/Users/testuser/Apps/foo/intro.md"), 1)

    def test_de_duplicated_repeats_track_last_line(self):
        result = self.server._extract_files_from_conversation("ignored")
        intro = next(
            r for r in result["groups"].get("markdown", [])
            if r["target"] == "/Users/testuser/Apps/foo/intro.md"
        )
        self.assertEqual(intro["first_line"], 3)
        self.assertEqual(intro["last_line"], 4)

    def test_each_row_has_label_target_kind_first_and_last_line(self):
        result = self.server._extract_files_from_conversation("ignored")
        for cat, rows in result["groups"].items():
            for r in rows:
                with self.subTest(cat=cat, target=r.get("target")):
                    self.assertIn("label", r)
                    self.assertIn("target", r)
                    self.assertIn("kind", r)
                    self.assertIn("first_line", r)
                    self.assertIn("last_line", r)
                    self.assertIn(r["kind"], ("path", "url"))

    def test_missing_jsonl_returns_empty(self):
        self.server._resolve_conversation_path = lambda cid: Path("/no/such/file.jsonl")
        result = self.server._extract_files_from_conversation("ignored")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["groups"], {})
        self.assertFalse(result["truncated"])


if __name__ == "__main__":
    unittest.main()
