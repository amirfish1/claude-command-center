"""ccc doctor / build_ccc_doctor() (W3-3): per-engine CLI/auth/BYOK health.

Runs against the real system probes (no mocking) -- this is intentionally a
structural/contract test (every engine present, correct byok_ready set,
dry-run smoke never spawns anything) rather than an assertion about which
CLIs happen to be installed on the machine running the suite.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _fresh_server():
    for name in ("server", "morning", "morning_store"):
        sys.modules.pop(name, None)
    return importlib.import_module("server")


def test_doctor_covers_every_orchestration_engine():
    server = _fresh_server()
    report = server.build_ccc_doctor()
    assert set(report["engines"]) == set(server._ORCHESTRATION_SPAWN_ENGINES)


def test_doctor_byok_ready_matches_byok_direct_env_engines():
    server = _fresh_server()
    report = server.build_ccc_doctor()
    for engine, row in report["engines"].items():
        expected = engine in server.BYOK_DIRECT_ENV_ENGINES
        assert row["byok_ready"] is expected, engine
        if not expected:
            assert row["byok_profile_present"] is False


def test_doctor_row_shape_and_smoke_is_dry_run_only():
    server = _fresh_server()
    report = server.build_ccc_doctor()
    for engine, row in report["engines"].items():
        assert isinstance(row["cli_present"], bool)
        assert row["auth_present"] in (True, False, None)
        assert isinstance(row["byok_ready"], bool)
        assert isinstance(row["byok_profile_present"], bool)
        smoke = row["smoke_dry_run"]
        assert smoke["ok"] == row["cli_present"]
        assert "dry-run" in smoke["note"]


def test_doctor_unknown_auth_probe_reports_none_not_false(monkeypatch):
    server = _fresh_server()
    # "kilo" has no dedicated auth probe (not in onboarding's 5, not opencode) --
    # confirm this is surfaced as "unknown" (None), never a false "not logged in".
    present, source = server._doctor_auth_present("kilo", onboarding_clis={})
    assert present is None
    assert source == "no-probe-implemented"


def test_doctor_reuses_onboarding_login_signal():
    server = _fresh_server()
    present, source = server._doctor_auth_present(
        "claude", onboarding_clis={"claude": {"logged_in": True}},
    )
    assert present is True
    assert source == "cli-login-check"
