"""Tests for the anonymous open beacon and the retired opt-in ping's
remaining read-only surface.

The trust contract: telemetry needs no consent step because the beacon
carries no identity, and the env-var kill switch is the only gate. These
tests keep that promise honest — the kill switch, the once-per-UTC-day
gate, the week/month flag math, the geo-free payload shape, and the
maintainer dev-flag mechanisms all have at least one assertion below.

The opt-in daily ping (install_id, consent banner, heartbeat/active-seconds)
was retired 2026-09-28. `TestOptInPingRemoved` asserts its functions and
HTTP routes are gone outright, not just unused, so nothing can silently
resurrect it. `/api/telemetry/status` is public API and stays (additive
only); it now reports the retirement rather than gating anything.

No tests in this file touch the network. `_send_telemetry_open_beacon` is
patched out wherever a flow would hit it.
"""
import importlib
import json
import os
import pathlib
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
        self._prev_userprofile = os.environ.get("USERPROFILE")
        os.environ["HOME"] = str(pathlib.Path(self.tmp_home).resolve())
        os.environ["USERPROFILE"] = str(pathlib.Path(self.tmp_home).resolve())
        # Clear the kill-switch / override / dev-mode env vars so each test
        # starts from a known state.
        self._prev_disabled = os.environ.pop("CCC_TELEMETRY_DISABLED", None)
        self._prev_endpoint = os.environ.pop("CCC_TELEMETRY_ENDPOINT", None)
        self._prev_dev = os.environ.pop("CCC_TELEMETRY_DEV_MODE", None)
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._prev_home
        if self._prev_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = self._prev_userprofile
        for name, prev in (
            ("CCC_TELEMETRY_DISABLED", self._prev_disabled),
            ("CCC_TELEMETRY_ENDPOINT", self._prev_endpoint),
            ("CCC_TELEMETRY_DEV_MODE", self._prev_dev),
        ):
            if prev is not None:
                os.environ[name] = prev
            else:
                os.environ.pop(name, None)
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        import shutil
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def _write_legacy_state(self, data):
        p = self.server._telemetry_state_path()
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        p.write_text(json.dumps(data), encoding="utf-8")


class TestDefaultsOff(TelemetryTestBase):
    """Nothing telemetry-related should exist on disk, or send anything,
    just from importing the module."""

    def test_state_dir_does_not_exist_before_use(self):
        # Just importing server must NOT create the state dir. The first
        # call to a telemetry function is what creates it (lazily).
        state_dir = pathlib.Path(self.tmp_home, ".config", "claude-command-center")
        self.assertFalse(state_dir.exists(),
                         "telemetry state dir created at import time — too eager")

    def test_load_telemetry_state_returns_not_asked_on_first_run(self):
        state = self.server._load_telemetry_state()
        self.assertIsNone(state["opt_in"])
        self.assertIsNone(state["asked_at"])

    def test_install_id_not_present_by_default(self):
        # This build never generates a new install id.
        self.assertFalse(self.server._telemetry_install_id_present())


class TestOptInPingRemoved(TelemetryTestBase):
    """The 2026-09-28 retirement removed the ping's functions and HTTP
    routes outright, not just their call sites, so nothing can accidentally
    resurrect consent-gated identified telemetry."""

    REMOVED_FUNCTIONS = (
        "_telemetry_record_heartbeat",
        "_maybe_send_telemetry",
        "_build_telemetry_payload",
        "_save_telemetry_state",
        "_telemetry_load_or_init_install_id",
        "_telemetry_resolved_endpoint",
        "_send_telemetry_ping",
        "_telemetry_read_last_ping_date",
    )

    def test_ping_functions_are_gone(self):
        for name in self.REMOVED_FUNCTIONS:
            self.assertFalse(hasattr(self.server, name),
                             f"{name} should have been fully removed with the opt-in ping")

    def test_opt_in_and_heartbeat_routes_are_inert_stubs(self):
        # Public API, so the routes stay, but only as no-ops: the handler
        # block must not touch state, install ids, or the network.
        source = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        start = source.index('if path in ("/api/telemetry/opt-in", "/api/telemetry/heartbeat"):')
        block = source[start:source.index('if path == "/api/self-update":', start)]
        self.assertIn('"retired": True', block)
        for forbidden in ("_telemetry", "state", "install_id", "urlopen"):
            self.assertNotIn(forbidden, block.replace("/api/telemetry/", ""))

    def test_status_route_still_present(self):
        # Public API — additive only, must stay even though it no longer
        # gates anything.
        source = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn('"/api/telemetry/status"', source)


class TestLegacyStateFile(TelemetryTestBase):
    """telemetry.json / install-id are read-only leftovers from a
    pre-retirement install. This build never writes opt_in/asked_at/
    endpoint, but must still report them if an old file is present, and
    must not choke on a corrupt one."""

    def test_reads_a_pre_retirement_opt_in_choice(self):
        self._write_legacy_state({
            "opt_in": True, "asked_at": "2026-01-01T00:00:00+00:00", "endpoint": None,
        })
        state = self.server._load_telemetry_state()
        self.assertIs(state["opt_in"], True)
        self.assertEqual(state["asked_at"], "2026-01-01T00:00:00+00:00")

    def test_corrupt_state_file_falls_back_to_not_asked(self):
        p = self.server._telemetry_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("this is not json {{{", encoding="utf-8")
        state = self.server._load_telemetry_state()
        self.assertIsNone(state["opt_in"])
        self.assertIsNone(state["asked_at"])

    def test_legacy_install_id_presence_is_detected_but_never_created(self):
        self.assertFalse(self.server._telemetry_install_id_present())
        p = self.server._telemetry_install_id_path()
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        p.write_text("11111111-1111-4111-8111-111111111111", encoding="utf-8")
        self.assertTrue(self.server._telemetry_install_id_present())


class TestEnvKillSwitch(TelemetryTestBase):
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


class TestEndpointResolution(TelemetryTestBase):
    def test_default_endpoint_used_when_env_unset(self):
        self.assertEqual(self.server._telemetry_resolved_open_endpoint(),
                         "https://telemetry.claude-command-center.workers.dev/v1/open")

    def test_env_overrides_default_endpoint(self):
        os.environ["CCC_TELEMETRY_ENDPOINT"] = "https://example.invalid"
        self.assertEqual(self.server._telemetry_resolved_open_endpoint(),
                         "https://example.invalid/v1/open")

    def test_legacy_ping_suffix_is_swapped_to_open(self):
        # A staging/fork override written before the ping was retired
        # still ends in /v1/ping — keep it working by swapping the suffix.
        os.environ["CCC_TELEMETRY_ENDPOINT"] = "https://example.invalid/v1/ping"
        self.assertEqual(self.server._telemetry_resolved_open_endpoint(),
                         "https://example.invalid/v1/open")

    def test_open_suffix_passes_through_unchanged(self):
        os.environ["CCC_TELEMETRY_ENDPOINT"] = "https://example.invalid/v1/open"
        self.assertEqual(self.server._telemetry_resolved_open_endpoint(),
                         "https://example.invalid/v1/open")


class TestWeekMonthFlags(TelemetryTestBase):
    """first_this_week / first_this_month are computed entirely from the
    locally stored last-beacon date — no history beyond that ever exists,
    on disk or on the wire."""

    def test_iso_week_matches_python_isocalendar(self):
        self.assertEqual(self.server._telemetry_iso_week("2026-01-04"), (2026, 1))
        self.assertEqual(self.server._telemetry_iso_week("2026-01-05"), (2026, 2))

    def test_first_run_both_true(self):
        self.assertEqual(
            self.server._telemetry_compute_first_flags("", "2026-09-28"),
            (True, True))
        self.assertEqual(
            self.server._telemetry_compute_first_flags(None, "2026-09-28"),
            (True, True))

    def test_same_day_is_not_first_for_either(self):
        self.assertEqual(
            self.server._telemetry_compute_first_flags("2026-09-28", "2026-09-28"),
            (False, False))

    def test_iso_week_boundary_within_same_month(self):
        # 2026-01-04 is a Sunday in ISO week 1; 2026-01-05 is the Monday
        # that starts ISO week 2. Same calendar month throughout.
        first_week, first_month = self.server._telemetry_compute_first_flags(
            "2026-01-04", "2026-01-05")
        self.assertTrue(first_week)
        self.assertFalse(first_month)

    def test_calendar_month_boundary_within_same_iso_week(self):
        # 2026-12-28 and 2027-01-01 fall in the same ISO week (both
        # isocalendar()-report as ISO week 53 of ISO-year 2026) despite
        # crossing both a calendar month and a calendar year — proof the
        # week flag doesn't piggyback on the month string.
        first_week, first_month = self.server._telemetry_compute_first_flags(
            "2026-12-28", "2027-01-01")
        self.assertFalse(first_week)
        self.assertTrue(first_month)

    def test_ordinary_month_boundary(self):
        first_week, first_month = self.server._telemetry_compute_first_flags(
            "2026-09-30", "2026-10-01")
        self.assertTrue(first_month)

    def test_malformed_last_date_is_treated_as_first_week(self):
        first_week, first_month = self.server._telemetry_compute_first_flags(
            "not-a-date", "2026-09-28")
        self.assertTrue(first_week)


class TestAnonymousOpenBeacon(TelemetryTestBase):
    """The beacon carries no identity, so its only gates are the kill
    switch and a once-per-UTC-day limit."""

    def test_beacon_sends_once_then_skips_same_day(self):
        with mock.patch.object(self.server, "_send_telemetry_open_beacon",
                               return_value=True) as send:
            self.assertEqual(self.server._maybe_send_telemetry_open_beacon(), "sent")
            self.assertEqual(self.server._maybe_send_telemetry_open_beacon(), "already-today")
        self.assertEqual(send.call_count, 1)

    def test_beacon_refires_on_a_new_utc_day(self):
        self.server._telemetry_write_last_open_date("2000-01-01")
        with mock.patch.object(self.server, "_send_telemetry_open_beacon",
                               return_value=True) as send:
            self.assertEqual(self.server._maybe_send_telemetry_open_beacon(), "sent")
        self.assertEqual(send.call_count, 1)

    def test_failed_send_does_not_burn_the_day(self):
        with mock.patch.object(self.server, "_send_telemetry_open_beacon",
                               return_value=False):
            self.assertEqual(self.server._maybe_send_telemetry_open_beacon(), "failed")
        # Nothing recorded, so the next hourly pass retries.
        self.assertEqual(self.server._telemetry_read_last_open_date(), "")

    def test_kill_switch_blocks_the_beacon(self):
        os.environ["CCC_TELEMETRY_DISABLED"] = "1"
        with mock.patch.object(self.server, "_send_telemetry_open_beacon") as send:
            self.assertEqual(self.server._maybe_send_telemetry_open_beacon(), "disabled-env")
        send.assert_not_called()

    def test_kill_switch_short_circuits_send_itself(self):
        # Belt and suspenders: even a direct call (bypassing the gate)
        # must not fire while the env kill switch is set.
        os.environ["CCC_TELEMETRY_DISABLED"] = "1"
        with mock.patch.object(self.server.urllib.request, "urlopen") as urlopen:
            self.assertFalse(self.server._send_telemetry_open_beacon())
        urlopen.assert_not_called()

    def _capture_beacon(self, *args, **kwargs):
        captured = {}

        class _Resp:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        with mock.patch.object(self.server.urllib.request, "urlopen", _fake_urlopen):
            ok = self.server._send_telemetry_open_beacon(*args, **kwargs)
        captured["ok"] = ok
        return captured

    def test_beacon_payload_shape_is_exactly_schema_v2(self):
        captured = self._capture_beacon()
        self.assertTrue(captured["ok"])
        self.assertTrue(captured["url"].endswith("/v1/open"))
        self.assertEqual(sorted(captured["body"].keys()), [
            "first_this_month", "first_this_week", "platform",
            "schema_version", "version",
        ])
        self.assertEqual(captured["body"]["schema_version"], 2)
        self.assertEqual(captured["body"]["version"], self.server.__version__)
        self.assertEqual(captured["body"]["platform"], sys.platform)

    def test_beacon_payload_carries_no_identity(self):
        captured = self._capture_beacon()
        # No install id, no per-machine hash, no date string — only the two
        # booleans the client computed locally.
        for forbidden in ("install_id", "id", "date", "last_open", "ip"):
            self.assertNotIn(forbidden, captured["body"])

    def test_beacon_carries_the_flags_it_was_given(self):
        captured = self._capture_beacon(first_this_week=True, first_this_month=False)
        self.assertIs(captured["body"]["first_this_week"], True)
        self.assertIs(captured["body"]["first_this_month"], False)

    def test_beacon_flags_default_false(self):
        captured = self._capture_beacon()
        self.assertIs(captured["body"]["first_this_week"], False)
        self.assertIs(captured["body"]["first_this_month"], False)


class TestMaintainerDevFlag(TelemetryTestBase):
    """Either CCC_TELEMETRY_DEV_MODE or telemetry.json's "dev" key marks a
    beacon as "not-a-real-user" so the public page can report counts with
    and without the maintainer's own machine."""

    def test_dev_mode_off_by_default(self):
        self.assertFalse(self.server._telemetry_dev_mode())
        self.assertFalse(self.server._telemetry_dev_mode_env())
        self.assertFalse(self.server._telemetry_dev_mode_file())

    def test_dev_mode_env_flag(self):
        os.environ["CCC_TELEMETRY_DEV_MODE"] = "1"
        self.assertTrue(self.server._telemetry_dev_mode_env())
        self.assertTrue(self.server._telemetry_dev_mode())

    def test_dev_mode_file_flag(self):
        self._write_legacy_state({"dev": True})
        self.assertTrue(self.server._telemetry_dev_mode_file())
        self.assertTrue(self.server._telemetry_dev_mode())

    def test_dev_mode_file_ignores_unrelated_keys(self):
        # A pre-retirement opt_in:true file must NOT itself imply dev mode.
        self._write_legacy_state({
            "opt_in": True, "asked_at": "2026-01-01T00:00:00+00:00", "endpoint": None,
        })
        self.assertFalse(self.server._telemetry_dev_mode_file())
        self.assertFalse(self.server._telemetry_dev_mode())

    def test_dev_mode_either_mechanism_is_enough(self):
        # File-only.
        self._write_legacy_state({"dev": True})
        self.assertTrue(self.server._telemetry_dev_mode())
        # Reset and try env-only.
        pathlib.Path(self.server._telemetry_state_path()).unlink()
        self.assertFalse(self.server._telemetry_dev_mode())
        os.environ["CCC_TELEMETRY_DEV_MODE"] = "1"
        self.assertTrue(self.server._telemetry_dev_mode())

    def test_dev_flag_never_carries_an_identifier(self):
        os.environ["CCC_TELEMETRY_DEV_MODE"] = "1"
        captured = {}

        class _Resp:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def _fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        with mock.patch.object(self.server.urllib.request, "urlopen", _fake_urlopen):
            self.server._send_telemetry_open_beacon()
        self.assertEqual(sorted(captured["body"].keys()), [
            "dev", "first_this_month", "first_this_week", "platform",
            "schema_version", "version",
        ])
        self.assertIs(captured["body"]["dev"], True)

    def test_dev_flag_omitted_by_default(self):
        captured = {}

        class _Resp:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def _fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        with mock.patch.object(self.server.urllib.request, "urlopen", _fake_urlopen):
            self.server._send_telemetry_open_beacon()
        self.assertNotIn("dev", captured["body"])


if __name__ == "__main__":
    unittest.main()
