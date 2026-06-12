"""Smoke tests for the anonymous opt-in telemetry module.

The trust contract: telemetry is OFF by default. These tests live to keep
that promise honest — every gate (env var, opt-in flag, install-id presence,
last-ping date) has at least one assertion below.

No tests in this file touch the network. `_send_telemetry_ping` is patched
out wherever a flow would hit it.
"""
import importlib
import json
import os
import pathlib
import re
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TelemetryTestBase(unittest.TestCase):
    """Reimports server with a clean $HOME each test so telemetry state
    lives in a throwaway dir. We can't just patch the module-level path
    constants because they're frozen at import time."""

    def setUp(self):
        self.tmp_home = tempfile.mkdtemp(prefix="ccc-telemetry-home-")
        self._prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(pathlib.Path(self.tmp_home).resolve())
        # Clear the kill-switch env var so each test starts from a known state.
        self._prev_disabled = os.environ.pop("CCC_TELEMETRY_DISABLED", None)
        self._prev_endpoint = os.environ.pop("CCC_TELEMETRY_ENDPOINT", None)
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._prev_home
        if self._prev_disabled is not None:
            os.environ["CCC_TELEMETRY_DISABLED"] = self._prev_disabled
        else:
            os.environ.pop("CCC_TELEMETRY_DISABLED", None)
        if self._prev_endpoint is not None:
            os.environ["CCC_TELEMETRY_ENDPOINT"] = self._prev_endpoint
        else:
            os.environ.pop("CCC_TELEMETRY_ENDPOINT", None)
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        shutil.rmtree(self.tmp_home, ignore_errors=True)


class TestDefaultsOff(TelemetryTestBase):
    """Defaults-OFF is the most load-bearing property. If any of these fail,
    we're shipping a privacy bug."""

    def test_state_dir_does_not_exist_before_use(self):
        # Just importing server must NOT create the state dir. The first
        # call to a telemetry function is what creates it (lazily).
        state_dir = pathlib.Path(self.tmp_home, ".config", "claude-command-center")
        self.assertFalse(state_dir.exists(),
                         "telemetry state dir created at import time — too eager")

    def test_load_telemetry_state_returns_not_asked_on_first_run(self):
        state = self.server._load_telemetry_state()
        self.assertIsNone(state["opt_in"],
                          "opt_in must be None (never asked) on first run, "
                          "not False or True — the bar relies on this tri-state")
        self.assertIsNone(state["asked_at"])

    def test_maybe_send_telemetry_is_no_op_when_never_asked(self):
        with mock.patch.object(self.server, "_send_telemetry_ping") as send:
            result = self.server._maybe_send_telemetry()
        self.assertEqual(result, "no-opt-in")
        send.assert_not_called()

    def test_install_id_not_created_when_opt_in_null(self):
        # We must NOT generate the install-id until the user opts in.
        # Generating it eagerly would mean "off-by-default" is a lie.
        self.server._maybe_send_telemetry()  # no-op
        self.assertFalse(self.server._telemetry_install_id_present(),
                         "install-id created before opt-in — leaks a stable id")


class TestEnvKillSwitch(TelemetryTestBase):
    def test_env_var_wins_over_opt_in(self):
        os.environ["CCC_TELEMETRY_DISABLED"] = "1"
        # Even an enthusiastically opted-in user must be silenced by the env.
        self.server._save_telemetry_state({
            "opt_in": True, "asked_at": "2026-01-01T00:00:00+00:00", "endpoint": None,
        })
        self.server._telemetry_load_or_init_install_id()  # so install-id present
        with mock.patch.object(self.server, "_send_telemetry_ping") as send:
            result = self.server._maybe_send_telemetry()
        self.assertEqual(result, "disabled-env")
        send.assert_not_called()

    def test_env_var_accepts_liberal_truthy_values(self):
        for val in ("1", "true", "TRUE", "yes", "ON", "Yes"):
            os.environ["CCC_TELEMETRY_DISABLED"] = val
            self.assertTrue(self.server._telemetry_disabled_env(),
                            f"expected {val!r} to disable telemetry")

    def test_env_var_falsy_values_do_not_disable(self):
        for val in ("0", "false", "no", "off", "", "maybe"):
            os.environ["CCC_TELEMETRY_DISABLED"] = val
            self.assertFalse(self.server._telemetry_disabled_env(),
                             f"{val!r} unexpectedly disabled telemetry")
        os.environ.pop("CCC_TELEMETRY_DISABLED", None)
        self.assertFalse(self.server._telemetry_disabled_env())


class TestInstallId(TelemetryTestBase):
    def test_install_id_is_generated_on_first_call(self):
        uid = self.server._telemetry_load_or_init_install_id()
        self.assertIsNotNone(uid)
        # UUIDv4 shape: 8-4-4-4-12 hex.
        self.assertRegex(uid, r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

    def test_install_id_is_idempotent_across_calls(self):
        first = self.server._telemetry_load_or_init_install_id()
        second = self.server._telemetry_load_or_init_install_id()
        third = self.server._telemetry_load_or_init_install_id()
        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_install_id_file_is_mode_0600(self):
        self.server._telemetry_load_or_init_install_id()
        p = self.server._telemetry_install_id_path()
        mode = stat.S_IMODE(p.stat().st_mode)
        self.assertEqual(mode, 0o600,
                         f"install-id is {oct(mode)}, must be 0o600")

    def test_state_dir_is_mode_0700(self):
        self.server._telemetry_state_dir()
        p = pathlib.Path(self.tmp_home, ".config", "claude-command-center")
        mode = stat.S_IMODE(p.stat().st_mode)
        self.assertEqual(mode, 0o700,
                         f"state dir is {oct(mode)}, must be 0o700")

    def test_missing_install_id_skips_ping(self):
        # Opt-in true, but install-id deleted (the "user reset" path).
        self.server._save_telemetry_state({
            "opt_in": True, "asked_at": "2026-01-01T00:00:00+00:00", "endpoint": None,
        })
        # Sanity: no install-id yet.
        self.assertFalse(self.server._telemetry_install_id_present())
        with mock.patch.object(self.server, "_send_telemetry_ping") as send:
            result = self.server._maybe_send_telemetry()
        # Either "no-install-id" or "sent" — but if install-id wasn't present
        # AND we didn't auto-create it via the opt-in path, must be skip.
        self.assertEqual(result, "no-install-id")
        send.assert_not_called()


class TestPayloadShape(TelemetryTestBase):
    def test_payload_has_exactly_the_documented_fields(self):
        # Pre-create the install-id so _build_telemetry_payload returns a dict.
        self.server._telemetry_load_or_init_install_id()
        payload = self.server._build_telemetry_payload()
        self.assertIsNotNone(payload)
        expected = {
            "schema_version", "install_id", "version",
            "platform", "engines", "last_active_date", "sessions_today",
        }
        self.assertEqual(set(payload.keys()), expected,
                         f"payload keys drifted from the public contract: {payload.keys()}")

    def test_payload_schema_version_is_int_one(self):
        self.server._telemetry_load_or_init_install_id()
        payload = self.server._build_telemetry_payload()
        self.assertEqual(payload["schema_version"], 2)

    def test_payload_install_id_matches_disk(self):
        uid = self.server._telemetry_load_or_init_install_id()
        payload = self.server._build_telemetry_payload()
        self.assertEqual(payload["install_id"], uid)

    def test_payload_version_matches_server_version(self):
        self.server._telemetry_load_or_init_install_id()
        payload = self.server._build_telemetry_payload()
        self.assertEqual(payload["version"], self.server.__version__)

    def test_payload_engines_is_comma_separated_string(self):
        self.server._telemetry_load_or_init_install_id()
        with (
            mock.patch.object(self.server, "_resolve_claude_bin", return_value={"available": True}),
            mock.patch.object(self.server, "_resolve_codex_bin", return_value={"available": False}),
            mock.patch.object(self.server, "_resolve_gemini_bin", return_value={"available": True}),
            mock.patch.object(self.server, "_resolve_cursor_bin", return_value={"available": False}),
            mock.patch.object(self.server, "_resolve_antigravity_bin", return_value={"available": True}),
            mock.patch.object(self.server, "_resolve_kilo_bin", return_value={"available": False}),
        ):
            payload = self.server._build_telemetry_payload()
        # claude,gemini,antigravity — order preserved, codex/kilo disabled in mock.
        self.assertEqual(payload["engines"], "claude,gemini,antigravity")

    def test_payload_last_active_date_is_iso_date_only(self):
        self.server._telemetry_load_or_init_install_id()
        payload = self.server._build_telemetry_payload()
        # Either "" (no transcripts) or YYYY-MM-DD — never a full timestamp.
        self.assertTrue(payload["last_active_date"] == "" or
                        re.match(r"^\d{4}-\d{2}-\d{2}$", payload["last_active_date"]),
                        f"last_active_date leaked clock time: "
                        f"{payload['last_active_date']!r}")


class TestOptInStateTransitions(TelemetryTestBase):
    def test_enable_then_disable_round_trip(self):
        self.server._save_telemetry_state({
            "opt_in": True, "asked_at": "2026-01-01T00:00:00+00:00", "endpoint": None,
        })
        self.assertIs(self.server._load_telemetry_state()["opt_in"], True)
        self.server._save_telemetry_state({
            "opt_in": False, "asked_at": "2026-01-02T00:00:00+00:00", "endpoint": None,
        })
        self.assertIs(self.server._load_telemetry_state()["opt_in"], False)

    def test_state_file_is_mode_0600(self):
        self.server._save_telemetry_state({
            "opt_in": True, "asked_at": "2026-01-01T00:00:00+00:00", "endpoint": None,
        })
        p = self.server._telemetry_state_path()
        mode = stat.S_IMODE(p.stat().st_mode)
        self.assertEqual(mode, 0o600,
                         f"telemetry.json is {oct(mode)}, must be 0o600")

    def test_corrupt_state_file_falls_back_to_not_asked(self):
        p = self.server._telemetry_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("this is not json {{{", encoding="utf-8")
        state = self.server._load_telemetry_state()
        self.assertIsNone(state["opt_in"])
        self.assertIsNone(state["asked_at"])


class TestLastPingDateGating(TelemetryTestBase):
    def _opt_in(self):
        self.server._save_telemetry_state({
            "opt_in": True, "asked_at": "2026-01-01T00:00:00+00:00", "endpoint": None,
        })
        self.server._telemetry_load_or_init_install_id()

    def test_first_ping_writes_today(self):
        self._opt_in()
        with mock.patch.object(self.server, "_send_telemetry_ping", return_value=True):
            result = self.server._maybe_send_telemetry()
        self.assertEqual(result, "sent")
        stamp = self.server._telemetry_read_last_ping_date()
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}$")

    def test_second_ping_same_day_is_no_op(self):
        self._opt_in()
        with mock.patch.object(self.server, "_send_telemetry_ping", return_value=True) as send:
            first = self.server._maybe_send_telemetry()
            second = self.server._maybe_send_telemetry()
        self.assertEqual(first, "sent")
        self.assertEqual(second, "already-today")
        # Only one send call across both invocations.
        self.assertEqual(send.call_count, 1)

    def test_failed_ping_does_not_update_last_ping_date(self):
        self._opt_in()
        with mock.patch.object(self.server, "_send_telemetry_ping", return_value=False):
            result = self.server._maybe_send_telemetry()
        self.assertEqual(result, "failed")
        # No date written → next hour's check will re-try.
        self.assertEqual(self.server._telemetry_read_last_ping_date(), "")


class TestEndpointResolution(TelemetryTestBase):
    def test_env_overrides_default_endpoint(self):
        os.environ["CCC_TELEMETRY_ENDPOINT"] = "https://example.invalid/ping"
        self.assertEqual(self.server._telemetry_resolved_endpoint(),
                         "https://example.invalid/ping")

    def test_default_endpoint_used_when_env_unset(self):
        # The default URL is a placeholder; we just assert it's the documented
        # value so the docs and code can't drift apart silently.
        self.assertEqual(self.server._telemetry_resolved_endpoint(),
                         "https://telemetry.claude-command-center.workers.dev/v1/ping")


if __name__ == "__main__":
    unittest.main()
