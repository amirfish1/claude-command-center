"""The supported Python floor must import the production server."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _find_python39():
    candidates = [
        os.environ.get("CCC_TEST_PYTHON39"),
        sys.executable if sys.version_info[:2] == (3, 9) else None,
        "/usr/bin/python3" if sys.platform == "darwin" else None,
        shutil.which("python3.9"),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        probe = subprocess.run(
            [
                candidate,
                "-c",
                "import sys; print('%d.%d' % sys.version_info[:2])",
            ],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "3.9":
            return candidate
    raise unittest.SkipTest("Python 3.9 interpreter not available")


class TestPython39Compatibility(unittest.TestCase):
    def test_production_server_imports(self):
        python39 = _find_python39()
        result = subprocess.run(
            [python39, "-c", "import server; print(server.__version__)"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(), r"^\d+\.\d+\.\d+$")
