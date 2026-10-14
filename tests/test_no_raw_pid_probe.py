"""Guard: `os.kill(pid, 0)` terminates the target on Windows (issue #142).

All liveness probes must go through ccc_server.paths._is_pid_alive, which
uses OpenProcess on win32.  No other non-test source may call it directly.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERN = re.compile(r"os\.kill\([^,()]+(?:\([^()]*\))?[^,()]*,\s*0\s*\)")
# Standalone dev scripts that never run on Windows.
ALLOW = {"ccc_server/paths.py", "scripts/e2e_failover.py"}


def _source_files():
    try:
        return subprocess.run(
            ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # No git metadata (e.g. a `git archive` export): walk the tree instead.
        skip = {"node_modules", "venv", "site-packages"}
        return [
            str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py")
            if not any(part.startswith(".") or part in skip for part in p.relative_to(ROOT).parts)
        ]


class NoRawPidProbe(unittest.TestCase):
    def test_no_direct_os_kill_zero(self):
        files = _source_files()
        bad = []
        for rel in files:
            if rel.startswith("tests/") or rel in ALLOW:
                continue
            p = ROOT / rel
            if not p.is_file():
                continue
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                code = line.split("#", 1)[0]
                if PATTERN.search(code) and "`" not in code:
                    bad.append(f"{rel}:{n}: {line.strip()}")
        self.assertEqual(bad, [], "use ccc_server.paths._is_pid_alive instead of os.kill(pid, 0)")


if __name__ == "__main__":
    unittest.main()
