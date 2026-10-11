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


class NoRawPidProbe(unittest.TestCase):
    def test_no_direct_os_kill_zero(self):
        files = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split()
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
