"""CCC.app server ownership: launch plan and service-command helpers.

Same extraction approach as tests/test_macapp_stale_server.py: compile the
pure helper block of scripts/macapp/main.swift with a small driver.
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
check(serverLaunchPlan(serviceInstalled: true, storedMode: nil) == .startService, "installed agent starts")
check(serverLaunchPlan(serviceInstalled: true, storedMode: "app") == .startService, "installed agent beats app choice")
check(serverLaunchPlan(serviceInstalled: false, storedMode: "app") == .spawnChild, "app choice spawns child")
check(serverLaunchPlan(serviceInstalled: false, storedMode: nil) == .askConsent, "no choice asks")
check(serverLaunchPlan(serviceInstalled: false, storedMode: "service") == .askConsent, "removed agent asks again")
check(serviceCommandShouldKeepWaiting(exitStatus: 0, jobLoaded: false), "success waits for bind")
check(serviceCommandShouldKeepWaiting(exitStatus: 1, jobLoaded: true), "loaded job keeps waiting")
check(!serviceCommandShouldKeepWaiting(exitStatus: 1, jobLoaded: false), "failed and unloaded falls back")
check(!serviceCommandShouldKeepWaiting(exitStatus: 3, jobLoaded: false), "not installed falls back")
print("ok")
'''


class MacAppServerServiceSourceTests(unittest.TestCase):
    def setUp(self):
        self.src = MAIN.read_text()

    def _func(self, name, next_name):
        return self.src[self.src.index(name):self.src.index(next_name)]

    def test_quit_only_stops_an_owned_child(self):
        quit_body = self._func("    func applicationWillTerminate(", "    func installTerminationSignalHandlers(")
        self.assertIn("stopOwnedProcess()", quit_body)
        self.assertNotIn("serviceCommandProcess", quit_body)
        self.assertNotIn("--stop-service", quit_body)
        stop_body = self._func("    func stopOwnedProcess()", "    func closeServerLog()")
        self.assertIn("serverProcess", stop_body)
        self.assertNotIn("serviceCommandProcess", stop_body)

    def test_service_label_is_not_the_bundle_id(self):
        self.assertIn('"com.github.claude-command-center.server"', self.src)
        self.assertIn("--start-service", self.src)
        self.assertIn("--stop-service", self.src)


@unittest.skipUnless(shutil.which("swiftc"), "swiftc not available")
class MacAppServerServiceHelperTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
