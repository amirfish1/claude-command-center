"""CCC.app first launch: Moment Zero deep-link decision, cccNotify payload
parsing, and splash/bridge wiring in scripts/macapp/main.swift.

The pure helper slice (between the `ccc-slice-begin/end: first-run helpers`
markers) is compiled and run under a driver, like test_macapp_stale_server.
Skipped when swiftc is unavailable (Linux CI without a toolchain).
"""
import re
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
let base = URL(string: "http://localhost:8090")!

// dashboardURLWithOnboarding
check(dashboardURLWithOnboarding(base).absoluteString == "http://localhost:8090/?onboarding=1",
      "appends onboarding=1")
let kept = dashboardURLWithOnboarding(URL(string: "http://localhost:8090/?repo=x")!)
check(kept.absoluteString.contains("repo=x") && kept.absoluteString.contains("onboarding=1"),
      "existing query items kept")
let once = dashboardURLWithOnboarding(dashboardURLWithOnboarding(base))
check(once.absoluteString.components(separatedBy: "onboarding=").count - 1 == 1,
      "flag never duplicated")

// shouldOpenOnboardingOnLaunch
check(shouldOpenOnboardingOnLaunch(didInstallThisLaunch: true, didOpenDashboardBefore: false,
                                   onboardingCompleted: nil, anyEngineLoggedIn: false),
      "install this launch => onboarding")
check(shouldOpenOnboardingOnLaunch(didInstallThisLaunch: true, didOpenDashboardBefore: true,
                                   onboardingCompleted: true, anyEngineLoggedIn: true),
      "install this launch => onboarding even on a veteran machine")
check(!shouldOpenOnboardingOnLaunch(didInstallThisLaunch: false, didOpenDashboardBefore: true,
                                    onboardingCompleted: false, anyEngineLoggedIn: false),
      "returning app user => plain dashboard")
check(shouldOpenOnboardingOnLaunch(didInstallThisLaunch: false, didOpenDashboardBefore: false,
                                   onboardingCompleted: false, anyEngineLoggedIn: false),
      "first app load + unfinished onboarding => open")
check(!shouldOpenOnboardingOnLaunch(didInstallThisLaunch: false, didOpenDashboardBefore: false,
                                    onboardingCompleted: true, anyEngineLoggedIn: false),
      "onboarding completed => plain")
check(!shouldOpenOnboardingOnLaunch(didInstallThisLaunch: false, didOpenDashboardBefore: false,
                                    onboardingCompleted: false, anyEngineLoggedIn: true),
      "an engine already logged in => veteran => plain")
check(!shouldOpenOnboardingOnLaunch(didInstallThisLaunch: false, didOpenDashboardBefore: false,
                                    onboardingCompleted: nil, anyEngineLoggedIn: false),
      "status unknown => never force it")

// parseOnboardingStatusHint
let s1 = parseOnboardingStatusHint(["completed": true,
                                    "clis": ["claude": ["logged_in": true],
                                             "codex": ["logged_in": false]]])
check(s1.completed == true && s1.anyLoggedIn == true, "status: completed + logged in")
let s2 = parseOnboardingStatusHint(["completed": false,
                                    "clis": ["claude": ["logged_in": false]]])
check(s2.completed == false && s2.anyLoggedIn == false, "status: fresh machine")
let s3 = parseOnboardingStatusHint([:])
check(s3.completed == nil && s3.anyLoggedIn == false, "status: unknown shape stays nil")
let s4 = parseOnboardingStatusHint(["completed": false])
check(s4.completed == false && s4.anyLoggedIn == false, "status: no clis key tolerated")

// parseCCCNotifyMessage
let r1 = parseCCCNotifyMessage(["title": "Done", "body": "Cost $0, saved $1.80",
                                "kind": "success", "url": "/?s=1", "tag": "task-9"])
check(r1 == CCCNotifyRequest(title: "Done", body: "Cost $0, saved $1.80",
                             kind: "success", url: "/?s=1", tag: "task-9"),
      "full dict parses")
let r2 = parseCCCNotifyMessage("Task finished")
check(r2 == CCCNotifyRequest(title: "CCC", body: "Task finished",
                             kind: "", url: "", tag: ""), "bare string becomes body")
check(parseCCCNotifyMessage(42) == nil, "number body rejected")
check(parseCCCNotifyMessage(nil) == nil, "nil body rejected")
check(parseCCCNotifyMessage([String: Any]()) == nil, "empty dict rejected")
check(parseCCCNotifyMessage(["body": "   "]) == nil, "whitespace-only dict rejected")
check(parseCCCNotifyMessage(["title": "Only a title"]) != nil, "title-only is displayable")
let r3 = parseCCCNotifyMessage(["title": "  Padded  ", "kind": 7])!
check(r3.title == "Padded" && r3.kind == "", "trims; non-string kind ignored")

// resolveNotifyURL
check(resolveNotifyURL("/?s=abc", base: base)?.absoluteString == "http://localhost:8090/?s=abc",
      "site-relative path resolves against CCC_URL")
check(resolveNotifyURL("https://example.com/x", base: base)?.absoluteString
        == "https://example.com/x", "absolute https passes through")
check(resolveNotifyURL("javascript:alert(1)", base: base) == nil, "javascript: rejected")
check(resolveNotifyURL("data:text/html,<b>x</b>", base: base) == nil, "data: rejected")
check(resolveNotifyURL("", base: base) == nil, "empty rejected")
check(resolveNotifyURL("   ", base: base) == nil, "whitespace rejected")
check(resolveNotifyURL("no-scheme-text", base: base) == nil, "schemeless word rejected")

// dashboardAPIURL
check(dashboardAPIURL(base, path: "/api/onboarding/status")?.absoluteString
        == "http://localhost:8090/api/onboarding/status", "api path on bare origin")
check(dashboardAPIURL(URL(string: "https://h.example/ccc/")!, path: "/api/x")?.absoluteString
        == "https://h.example/ccc/api/x", "keeps a mounted path prefix")
check(dashboardAPIURL(URL(string: "http://h:8090/?s=1")!, path: "/api/x")?.absoluteString
        == "http://h:8090/api/x", "drops base query")

print("ok")
'''


@unittest.skipUnless(shutil.which("swiftc"), "swiftc not available")
class MacAppFirstLaunchTests(unittest.TestCase):
    def test_pure_helpers(self):
        src = MAIN.read_text()
        start = src.index("// ccc-slice-begin: first-run helpers")
        end = src.index("// ccc-slice-end: first-run helpers")
        body = (
            "import Foundation\n"
            "#if canImport(FoundationNetworking)\n"
            "import FoundationNetworking\n"
            "#endif\n"
            + src[start:end]
            + DRIVER
        )
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


class MacAppFirstLaunchStaticTests(unittest.TestCase):
    """Wiring checks that don't need a compiler — the runtime paths that
    decide the URL, register the bridge, and drive the splash."""

    @classmethod
    def setUpClass(cls):
        cls.src = MAIN.read_text()

    def test_load_dashboard_resolves_first_url(self):
        src = self.src
        load = src[src.index("    func loadDashboard() {"):src.index("func resolveFirstDashboardURL")]
        self.assertIn("resolveFirstDashboardURL", load)
        self.assertIn("webView.load", load)

    def test_onboarding_deeplink_path(self):
        src = self.src
        self.assertIn('URLQueryItem(name: "onboarding", value: "1")', src)
        self.assertIn("dashboardURLWithOnboarding(CCC_URL)", src)
        self.assertIn('CCCDidOpenDashboard', src)
        # The didFinish hook records the first successful dashboard load.
        finish = src[src.index("func webView(_ webView: WKWebView, didFinish"):src.index("// MARK: WKUIDelegate")]
        self.assertIn("UserDefaults.standard.set(true, forKey: CCC_DID_OPEN_DASHBOARD_KEY)", finish)

    def test_notify_bridge_registered_and_wired(self):
        src = self.src
        self.assertIn('controller.add(nb, name: "cccNotify")', src)
        self.assertIn("UNUserNotificationCenter.current().delegate = notificationDelegate", src)
        self.assertIn("func deliverNativeNotification", src)
        self.assertIn("func handleNotificationOpen", src)
        self.assertIn("flushPendingNotificationURL", src)

    def test_splash_exists_and_is_used(self):
        src = self.src
        self.assertIn("final class CCCSplashView: NSView", src)
        self.assertIn("func showSplash", src)
        self.assertIn("func hideSplash", src)
        # Reduce-motion is honored by the native animation paths.
        self.assertIn("accessibilityDisplayShouldReduceMotion", src)

    def test_no_em_dash_in_new_user_copy(self):
        # The onboarding tips and splash copy are novice-facing; COMMON.md
        # forbids em dashes in user copy.
        tips = self.src[self.src.index("private static let tips = ["):]
        tips = tips[:tips.index("]")]
        self.assertNotIn("\u2014", tips)


if __name__ == "__main__":
    unittest.main()
