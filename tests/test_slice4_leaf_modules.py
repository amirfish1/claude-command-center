# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Slice 4: ccc_server.activity_log and ccc_server.repo_paths leaf modules."""

import subprocess
import sys
import unittest

import server
from ccc_server import activity_log, repo_paths

ACTIVITY_NAMES = [
    "ACTIVITY_LOG_FILE", "_activity_log_preview", "_ACTIVITY_LOG_DIR_READY",
    "_log_activity", "_recent_error_ts", "_recent_error_lock",
    "_RECENT_ERROR_WINDOW_S", "_record_server_error", "_recent_error_count",
]
REPO_NAMES = [
    "_REPO_PINS_FILE", "_load_repo_pins", "_git_root_cache", "_find_git_root",
    "RepoContextError", "repo_log_dir", "_KNOWN_REPO_PATHS_CACHE",
    "_KNOWN_REPO_PATHS_TTL_S", "_KNOWN_REPO_PATHS_LOCK",
    "_KNOWN_REPO_PATHS_REBUILD_LOCK", "_invalidate_known_repo_paths",
    "_known_repo_paths_memo_hit", "_known_repo_paths",
    "_git_toplevel_for_existing_dir", "_has_project_marker",
    "_nearest_marked_repo_dir", "_plus_space_path_candidates",
    "_resolve_repo_path_check", "resolve_repo_path", "_git",
]


class TestSlice4LeafModules(unittest.TestCase):
    def test_modules_do_not_import_server(self):
        for mod in ("activity_log", "repo_paths"):
            code = (
                f"import sys; import ccc_server.{mod}; "
                "assert 'server' not in sys.modules, 'server was imported'"
            )
            res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)

    def test_server_reexports_same_objects(self):
        for mod, names in ((activity_log, ACTIVITY_NAMES), (repo_paths, REPO_NAMES)):
            for name in names:
                self.assertTrue(hasattr(server, name), name)
                self.assertIs(getattr(server, name), getattr(mod, name), name)

    def test_names_no_longer_defined_in_server_source(self):
        import ast
        tree = ast.parse(open(server.__file__).read())
        defined = set()
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                defined.add(n.name)
            elif isinstance(n, ast.Assign):
                defined.update(t.id for t in n.targets if isinstance(t, ast.Name))
        self.assertEqual(defined & set(ACTIVITY_NAMES + REPO_NAMES), set())

    def test_server_patch_reaches_moved_log_activity(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "a.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", p), \
                    mock.patch.object(server, "_ACTIVITY_LOG_DIR_READY", False):
                server._log_activity("cat", "verb", "hello")
            self.assertIn("hello", p.read_text())

    def test_server_patch_reaches_known_repo_paths_uncached(self):
        from unittest import mock
        server._invalidate_known_repo_paths()
        with mock.patch.object(server, "_known_repo_paths_uncached", return_value=["/x"]):
            self.assertEqual(repo_paths._known_repo_paths(), ["/x"])
        server._invalidate_known_repo_paths()

    def test_server_patch_reaches_resolve_repo_path(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(server, "_known_repo_paths", return_value=[]):
                with self.assertRaises(server.RepoContextError):
                    repo_paths.resolve_repo_path(d)  # plain dir, not known


if __name__ == "__main__":
    unittest.main()
