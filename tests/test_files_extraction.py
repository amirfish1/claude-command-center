"""Tests for the per-conversation file index (server-side extraction)."""

import importlib
import os
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


if __name__ == "__main__":
    unittest.main()
