"""ccc_server/content_hash.py — the worker-staleness fingerprint.

The bug this guards against: the original fingerprint hashed server.py's
own bytes only. Once server.py was decomposed into ccc_server/*.py, a code
change confined entirely to an extracted module (e.g. ccc_server/ask.py)
never touched server.py, so run.sh's staleness check and the worker's own
reported hash silently agreed even though the worker was running stale
code. See ccc_server/content_hash.py's module docstring.
"""

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ccc_server.content_hash import compute

_SCRIPT = Path(__file__).resolve().parent.parent / "ccc_server" / "content_hash.py"


def _make_repo(root, version="1.2.3"):
    (root / "server.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    ccc_dir = root / "ccc_server"
    ccc_dir.mkdir()
    (ccc_dir / "a.py").write_text("A = 1\n", encoding="utf-8")
    (ccc_dir / "b.py").write_text("B = 2\n", encoding="utf-8")


class ComputeContentHashTest(unittest.TestCase):
    def test_returns_version_from_server_py(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            version, _ = compute(root)
        self.assertEqual(version, "1.2.3")

    def test_hash_changes_when_a_ccc_server_module_changes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            _, before = compute(root)
            (root / "ccc_server" / "a.py").write_text("A = 999\n", encoding="utf-8")
            _, after = compute(root)
        self.assertNotEqual(before, after)

    def test_hash_unchanged_when_server_py_alone_is_unchanged(self):
        """The regression this fixes: a hash keyed on server.py's bytes
        alone would NOT change here even though real code changed."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            server_bytes_before = (root / "server.py").read_bytes()
            _, before = compute(root)
            (root / "ccc_server" / "a.py").write_text("A = 999\n", encoding="utf-8")
            server_bytes_after = (root / "server.py").read_bytes()
            _, after = compute(root)
        self.assertEqual(server_bytes_before, server_bytes_after)
        self.assertNotEqual(before, after)

    def test_hash_stable_for_unchanged_content(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            _, first = compute(root)
            _, second = compute(root)
        self.assertEqual(first, second)

    def test_cli_prints_version_and_hash(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root, version="9.9.9")
            out = subprocess.run(
                [sys.executable, str(_SCRIPT), str(root)],
                capture_output=True, text=True, check=True)
        parts = out.stdout.split()
        self.assertEqual(parts, ["9.9.9", parts[1]])
        self.assertEqual(len(parts[1]), 16)

    def test_cli_matches_library_call(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            version, content_hash = compute(root)
            out = subprocess.run(
                [sys.executable, str(_SCRIPT), str(root)],
                capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout, f"{version} {content_hash}")

    def test_cli_missing_repo_prints_blank_without_raising(self):
        out = subprocess.run(
            [sys.executable, str(_SCRIPT), "/nonexistent/path/xyz"],
            capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout, " ")


if __name__ == "__main__":
    unittest.main()
