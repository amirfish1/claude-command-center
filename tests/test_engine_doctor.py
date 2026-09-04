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


def test_doctor_process_listing_parsers_are_batched_and_read_only(monkeypatch):
    server = _fresh_server()
    calls = []

    def fake_sys_run(cmd, timeout=3):
        calls.append(cmd)
        if cmd[:3] == [server._SYS_PS, "-axo", "pid=,lstart=,command="]:
            return (
                "111 Fri Sep  4 01:02:03 2026 /usr/bin/python3 server.py\n"
                "222 Fri Sep  4 01:03:03 2026 /usr/bin/python3 other.py\n"
                "333 Fri Sep  4 01:04:03 2026 /usr/bin/node server.py\n"
            )
        if cmd[:6] == [
            server._SYS_LSOF,
            "-nP",
            "-a",
            "-iTCP",
            "-sTCP:LISTEN",
            "-FpPn",
        ]:
            return "p111\nn*:8090\np222\nn*:3000\n"
        raise AssertionError(f"unexpected command: {cmd!r}")

    monkeypatch.setattr(server, "_sys_run", fake_sys_run)

    rows = server._doctor_server_process_rows()
    ports = server._doctor_listening_ports([111, 222])

    assert rows == [{
        "pid": 111,
        "started_at": "Fri Sep 4 01:02:03 2026",
        "cmd": "/usr/bin/python3 server.py",
    }]
    assert ports == {111: [8090], 222: [3000]}
    assert len(calls) == 2


def test_doctor_instances_warns_on_duplicate_repo_server(monkeypatch):
    server = _fresh_server()
    repo = str(server.CCC_ROOT)
    monkeypatch.setattr(server, "_doctor_server_process_rows", lambda: [
        {
            "pid": 111,
            "started_at": "Fri Sep  4 01:02:03 2026",
            "cmd": "/usr/bin/python3 server.py",
        },
        {
            "pid": 222,
            "started_at": "Fri Sep  4 01:03:03 2026",
            "cmd": "/usr/bin/python3 server.py --port 8099",
        },
        {
            "pid": 333,
            "started_at": "Fri Sep  4 01:04:03 2026",
            "cmd": "/usr/bin/python3 other.py",
        },
    ])
    monkeypatch.setattr(server, "_doctor_process_cwds", lambda pids: {
        111: repo,
        222: repo,
        333: repo,
    })
    monkeypatch.setattr(server, "_doctor_listening_ports", lambda pids: {
        111: [8090],
        222: [8099],
    })
    monkeypatch.setattr(server, "_launchd_print_job_pid", lambda label: 111)
    monkeypatch.setattr(server, "_read_registry_pruned", lambda: [
        {
            "pid": 111,
            "port": 8090,
            "install_path": repo,
            "started_at": "2026-09-04T01:02:03-07:00",
        },
        {
            "pid": 222,
            "port": 8099,
            "install_path": repo,
            "started_at": "2026-09-04T01:03:03-07:00",
        },
    ])

    report = server.build_doctor_instances()

    assert report["status"] == "warn"
    assert report["launchd_pid"] == 111
    assert "more than one" in report["warning"]
    assert report["fix"] == (
        "kill 222 && launchctl kickstart -k "
        "gui/$(id -u)/com.github.claude-command-center"
    )
    by_pid = {row["pid"]: row for row in report["instances"]}
    assert by_pid[111]["port"] == 8090
    assert by_pid[111]["launchd_owned"] is True
    assert by_pid[111]["registered"] is True
    assert by_pid[222]["port"] == 8099
    assert by_pid[222]["launchd_owned"] is False


def test_doctor_instances_warns_when_listener_is_not_launchd_owned(monkeypatch):
    server = _fresh_server()
    repo = str(server.CCC_ROOT)
    monkeypatch.setattr(server, "_doctor_server_process_rows", lambda: [
        {
            "pid": 222,
            "started_at": "Fri Sep  4 01:03:03 2026",
            "cmd": "/usr/bin/python3 server.py --port 8099",
        },
    ])
    monkeypatch.setattr(server, "_doctor_process_cwds", lambda pids: {222: repo})
    monkeypatch.setattr(server, "_doctor_listening_ports", lambda pids: {222: [8099]})
    monkeypatch.setattr(server, "_launchd_print_job_pid", lambda label: 111)
    monkeypatch.setattr(server, "_read_registry_pruned", lambda: [])

    report = server.build_doctor_instances()

    assert report["status"] == "warn"
    assert "not launchd-owned" in report["warning"]
    assert report["fix"] == (
        "kill 222 && launchctl kickstart -k "
        "gui/$(id -u)/com.github.claude-command-center"
    )


def test_doctor_instances_warns_on_single_manual_listener(monkeypatch):
    server = _fresh_server()
    repo = str(server.CCC_ROOT)
    monkeypatch.setattr(server, "_doctor_server_process_rows", lambda: [
        {
            "pid": 222,
            "started_at": "Fri Sep  4 01:03:03 2026",
            "cmd": "/usr/bin/python3 server.py --port 8099",
        },
    ])
    monkeypatch.setattr(server, "_doctor_process_cwds", lambda pids: {222: repo})
    monkeypatch.setattr(server, "_doctor_listening_ports", lambda pids: {222: [8099]})
    monkeypatch.setattr(server, "_launchd_print_job_pid", lambda label: None)
    monkeypatch.setattr(server, "_read_registry_pruned", lambda: [])

    report = server.build_doctor_instances()

    assert report["status"] == "warn"
    assert "not launchd-owned" in report["warning"]
    assert report["fix"] == (
        "kill 222 && launchctl kickstart -k "
        "gui/$(id -u)/com.github.claude-command-center"
    )


def test_doctor_instances_ignores_reused_pid_registry_from_other_repo(monkeypatch):
    server = _fresh_server()
    repo = str(server.CCC_ROOT)
    monkeypatch.setattr(server, "_doctor_server_process_rows", lambda: [
        {
            "pid": 222,
            "started_at": "Fri Sep  4 01:03:03 2026",
            "cmd": "/usr/bin/python3 server.py --port 8099",
        },
    ])
    monkeypatch.setattr(server, "_doctor_process_cwds", lambda pids: {222: repo})
    monkeypatch.setattr(server, "_doctor_listening_ports", lambda pids: {222: [8099]})
    monkeypatch.setattr(server, "_launchd_print_job_pid", lambda label: 222)
    monkeypatch.setattr(server, "_read_registry_pruned", lambda: [
        {
            "pid": 222,
            "port": 8101,
            "install_path": "/Users/person/other-repo",
            "repo_common_dir": "/Users/person/other-repo/.git",
            "started_at": "2026-09-04T00:00:00-07:00",
        },
    ])

    report = server.build_doctor_instances()

    assert report["status"] == "ok"
    row = report["instances"][0]
    assert row["port"] == 8099
    assert row["started_at"] == "Fri Sep  4 01:03:03 2026"
    assert row["registered"] is False


def test_doctor_instances_are_included_in_engine_doctor(monkeypatch):
    server = _fresh_server()
    expected = {"ok": True, "status": "ok", "instances": []}
    monkeypatch.setattr(server, "build_doctor_instances", lambda: expected)

    report = server.build_ccc_doctor()

    assert report["server_instances"] == expected


def test_doctor_instances_route_is_wired():
    source = (PROJECT_ROOT / "server.py").read_text(encoding="utf-8")

    assert 'path == "/api/doctor/instances"' in source
    assert "self.send_json(build_doctor_instances())" in source
