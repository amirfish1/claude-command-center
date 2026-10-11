import tempfile
import unittest
from pathlib import Path

import server


class FolderViewerListingTests(unittest.TestCase):
    def test_lists_dirs_files_and_categories(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sub").mkdir()
            (root / ".hidden").write_text("x")
            (root / "a.png").write_bytes(b"12345")
            (root / "b.bin").write_bytes(b"1")
            r = server._browse_folder_for_viewer(str(root))
            self.assertTrue(r["ok"])
            self.assertEqual([x["name"] for x in r["dirs"]], ["sub"])
            files = {f["name"]: f for f in r["files"]}
            self.assertEqual(set(files), {"a.png", "b.bin"})
            self.assertEqual(files["a.png"]["category"], "images")
            self.assertEqual(files["a.png"]["size"], 5)
            self.assertIsNone(files["b.bin"]["category"])

    def test_file_path_lists_parent_with_selected(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.png"
            f.write_bytes(b"1")
            r = server._browse_folder_for_viewer(str(f))
            self.assertEqual(r["selected"], "a.png")
            self.assertEqual(r["path"], str(Path(d).resolve()))

    def test_missing_path_errors(self):
        self.assertFalse(server._browse_folder_for_viewer("/nonexistent/zzz")["ok"])


if __name__ == "__main__":
    unittest.main()
