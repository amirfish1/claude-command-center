"""Copy-and-open-as-MD message action: server-side file writer."""
import tempfile
import unittest
from pathlib import Path

import server


class SaveMessageMarkdownTests(unittest.TestCase):
    def test_writes_markdown_with_slug_from_first_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = server._save_message_markdown("# Plan: Ship it!\n\nbody", base_dir=d)
            self.assertEqual(Path(d), p.parent)
            self.assertTrue(p.name.endswith("-plan-ship-it.md"))
            self.assertEqual("# Plan: Ship it!\n\nbody\n", p.read_text())

    def test_never_overwrites_and_rejects_empty(self):
        with tempfile.TemporaryDirectory() as d:
            a = server._save_message_markdown("same", base_dir=d)
            b = server._save_message_markdown("same", base_dir=d)
            self.assertNotEqual(a, b)
            with self.assertRaises(ValueError):
                server._save_message_markdown("   ", base_dir=d)

    def test_title_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as d:
            p = server._save_message_markdown("x", title="../../etc/passwd", base_dir=d)
            self.assertEqual(Path(d), p.parent)


if __name__ == "__main__":
    unittest.main()
