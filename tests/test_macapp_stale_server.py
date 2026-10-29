"""CCC.app stale-server detection: the pure Swift helpers in main.swift.

Extracts the helper block from scripts/macapp/main.swift, compiles it with a
tiny driver, and runs it. Skipped when swiftc is unavailable (Linux CI).
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

MAIN = Path(__file__).resolve().parent.parent / "scripts" / "macapp" / "main.swift"

DRIVER = r'''
func check(_ ok: Bool, _ name: String) {
    if !ok { print("FAIL: \(name)"); exit(1) }
}
let a = String(repeating: "a", count: 40), b = String(repeating: "b", count: 40)
check(serverIsStale(serverRev: a, diskRev: b), "differing revs are stale")
check(!serverIsStale(serverRev: a, diskRev: a), "equal revs are current")
check(!serverIsStale(serverRev: "", diskRev: b), "unknown server rev is never stale")
check(!serverIsStale(serverRev: a, diskRev: ""), "unknown disk rev is never stale")
check(!serverIsStale(serverRev: a, diskRev: a + "\n"), "trailing newline ignored")
let old = ServerVersionInfo(pid: 10, startedAt: 100, codeRev: a)
check(!staleRestartCompleted(before: old, now: old, diskRev: b), "same process, still stale")
check(!staleRestartCompleted(before: old, now: ServerVersionInfo(pid: 11, startedAt: 200, codeRev: a), diskRev: b),
      "new process on old code is not done")
check(staleRestartCompleted(before: old, now: ServerVersionInfo(pid: 11, startedAt: 200, codeRev: b), diskRev: b),
      "new process on disk code is done")
check(staleRestartCompleted(before: old, now: ServerVersionInfo(pid: 10, startedAt: 200, codeRev: b), diskRev: b),
      "same pid but new started_at (execvp) is done")
check(!staleRestartCompleted(before: old, now: ServerVersionInfo(pid: 11, startedAt: 200, codeRev: ""), diskRev: b),
      "new process with unknown rev is not done")
print("ok")
'''


@unittest.skipUnless(shutil.which("swiftc"), "swiftc not available")
class MacAppStaleServerTests(unittest.TestCase):
    def test_pure_helpers(self):
        src = MAIN.read_text()
        start = src.index("struct ServerVersionInfo")
        end = src.index("/// Same resolution as server.py")
        body = "import Foundation\n" + src[start:end] + DRIVER
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "main.swift").write_text(body)
            exe = td / "t"
            build = subprocess.run(
                ["swiftc", "-o", str(exe), str(td / "main.swift")],
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.stdout.strip(), "ok", run.stdout + run.stderr)

    def test_bootstrap_only_reconciles_local_bound_port(self):
        src = MAIN.read_text()
        # Remote targets return from bootstrap() before continueBootstrap().
        boot = src[src.index("    func bootstrap() {"):src.index("    func waitForDeveloperTools()")]
        self.assertIn("CCC_TARGET_IS_REMOTE", boot)
        self.assertLess(boot.index("CCC_TARGET_IS_REMOTE"), boot.index("continueBootstrap()"))
        self.assertIn("replaceStaleServerThenLoad()", src)


if __name__ == "__main__":
    unittest.main()
