# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Slice 2: static import-closure fingerprint."""

import os
import tempfile
import unittest
from pathlib import Path

from ccc_server import import_closure
from ccc_server.import_closure import compute_closure

REPO = Path(__file__).resolve().parent.parent


def _write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


class TestImportClosure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        _write(self.root, "ccc_server/__init__.py", "")
        _write(self.root, "ccc_server/a.py", "from ccc_server import b\n")
        _write(self.root, "ccc_server/b.py", "x = 1\n")
        _write(self.root, "ccc_server/unused.py", "y = 1\n")
        _write(self.root, "ccc_worker.py", "import ccc_server.a\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_walks_imports_and_excludes_unreached(self):
        r = compute_closure(self.root)
        self.assertEqual(
            r["files"],
            ["ccc_server/__init__.py", "ccc_server/a.py", "ccc_server/b.py", "ccc_worker.py"],
        )
        self.assertFalse(r["includes_server"])

    def test_unreached_edit_keeps_hash_reached_edit_changes_it(self):
        h0 = compute_closure(self.root)["hash"]
        _write(self.root, "ccc_server/unused.py", "y = 2\n")
        self.assertEqual(compute_closure(self.root)["hash"], h0)
        _write(self.root, "ccc_server/b.py", "x = 22\n")
        self.assertNotEqual(compute_closure(self.root)["hash"], h0)

    def test_adopt_calls_and_server_import_are_followed(self):
        _write(self.root, "server.py", '_adopt_ccc_module("unused")\n')
        _write(self.root, "ccc_worker.py", "def f():\n    import server\n")
        r = compute_closure(self.root)
        self.assertTrue(r["includes_server"])
        self.assertIn("ccc_server/unused.py", r["files"])

    def test_result_is_cached_until_a_file_changes(self):
        real = import_closure._closure_files
        calls = []
        import_closure._closure_files = lambda *a: (calls.append(1), real(*a))[1]
        try:
            compute_closure(self.root)
            compute_closure(self.root)
            self.assertEqual(len(calls), 1)
            p = _write(self.root, "ccc_server/b.py", "x = 333\n")
            os.utime(p, ns=(1, 1))
            compute_closure(self.root)
            self.assertEqual(len(calls), 2)
        finally:
            import_closure._closure_files = real

    def test_real_repo_closure_is_deterministic(self):
        a = compute_closure(REPO)
        self.assertEqual(a["hash"], compute_closure(REPO)["hash"])
        self.assertIn("worker_engines.py", a["files"])


if __name__ == "__main__":
    unittest.main()
