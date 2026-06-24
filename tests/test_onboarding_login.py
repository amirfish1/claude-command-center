import importlib
import os
import sys
import time
import unittest
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TestOnboardingInlineLogin(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")

    def tearDown(self):
        cancel_all = getattr(self.server, "_cancel_all_onboarding_login_sessions", None)
        if cancel_all:
            cancel_all()

    def test_login_command_uses_cli_auth_subcommands(self):
        with mock.patch.object(
            self.server,
            "_resolve_claude_bin",
            return_value={"available": True, "bin": "/usr/local/bin/claude"},
        ), mock.patch.object(
            self.server,
            "_resolve_codex_bin",
            return_value={"available": True, "bin": "/usr/local/bin/codex"},
        ):
            claude = self.server._onboarding_login_command("claude")
            codex = self.server._onboarding_login_command("codex")

        self.assertTrue(claude["ok"])
        self.assertEqual(claude["argv"], ["/usr/local/bin/claude", "auth", "login"])
        self.assertEqual(claude["command"], "/usr/local/bin/claude auth login")
        self.assertTrue(codex["ok"])
        self.assertEqual(codex["argv"], ["/usr/local/bin/codex", "login"])
        self.assertEqual(codex["command"], "/usr/local/bin/codex login")

    def test_login_output_hints_extract_urls_and_device_codes(self):
        hints = self.server._onboarding_login_output_hints(
            "Open https://example.test/device and enter code ABCD-EFGH."
        )

        self.assertEqual(hints["urls"], ["https://example.test/device"])
        self.assertEqual(hints["codes"], ["ABCD-EFGH"])

    def test_inline_login_session_streams_input_and_can_cancel(self):
        with mock.patch.object(
            self.server,
            "_onboarding_login_command",
            return_value={
                "ok": True,
                "engine": "claude",
                "argv": ["/bin/cat"],
                "command": "/bin/cat",
            },
        ):
            started = self.server._start_onboarding_login_session("claude")

        self.assertTrue(started["ok"])
        session_id = started["session_id"]

        sent = self.server._send_onboarding_login_input(session_id, "hello from test\n")
        self.assertTrue(sent["ok"])

        status = {}
        deadline = time.time() + 3
        while time.time() < deadline:
            status = self.server._onboarding_login_status(session_id, offset=0)
            if "hello from test" in status.get("output", ""):
                break
            time.sleep(0.05)

        self.assertTrue(status["ok"])
        self.assertIn("hello from test", status.get("output", ""))

        cancelled = self.server._cancel_onboarding_login_session(session_id)
        self.assertTrue(cancelled["ok"])
        self.assertFalse(cancelled["running"])

    def test_onboarding_ui_wires_inline_login_panel(self):
        app_js = open(os.path.join(PROJECT_ROOT, "static", "app.js"), encoding="utf-8").read()
        app_css = open(os.path.join(PROJECT_ROOT, "static", "app.css"), encoding="utf-8").read()

        self.assertIn("/api/onboarding/login/start", app_js, "missing login start endpoint")
        self.assertIn("/api/onboarding/login/status", app_js, "missing login status endpoint")
        self.assertIn("/api/onboarding/login/input", app_js, "missing login input endpoint")
        self.assertIn("/api/onboarding/login/cancel", app_js, "missing login cancel endpoint")
        self.assertIn("onb-login-panel", app_js, "missing inline login panel markup")
        self.assertIn(".onb-login-panel", app_css, "missing inline login panel styles")
