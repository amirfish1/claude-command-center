"""Release-script regression tests that do not import the application."""

from pathlib import Path
import subprocess
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestReleasePreflight(unittest.TestCase):
    def test_rejects_untracked_files(self):
        sentinel = PROJECT_ROOT / f".release-preflight-untracked-{uuid.uuid4().hex}"
        sentinel.write_text("must not be omitted from a release\n", encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    "bash",
                    "scripts/cut-release.sh",
                    "99.99.99",
                    "--dry-run",
                    "--skip-dmg",
                    "--skip-brew",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            sentinel.unlink(missing_ok=True)

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(sentinel.name, output)
        self.assertIn("untracked", output.lower())


if __name__ == "__main__":
    unittest.main()
