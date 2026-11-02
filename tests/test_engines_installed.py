"""Tests for GET /api/engines/installed (engine probe used by first-run CLI setup).

Handler-level: exercises server._detect_engines_installed() directly with
monkeypatched env (COPILOT_HOME / GROK_HOME / CCC_VSCODE_USER_DIRS point at
tmp dirs; PATH emptied to simulate a missing spawn binary). No real CLIs.
All fixture data is obviously fake.
"""
import pathlib

import server

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOUR_JS = ROOT / "static" / "tour.js"

ALL_ENGINES = [
    "claude", "codex", "gemini", "cursor", "antigravity",
    "kilo", "opencode", "kimi", "hermes", "devin", "grok",
    "aider", "droid", "pi",
    "copilot", "copilotchat",
]


def _by_engine(payload):
    return {row["engine"]: row for row in payload["engines"]}


def _point_readonly_stores_at(monkeypatch, tmp_path):
    copilot_home = tmp_path / ".copilot"
    grok_home = tmp_path / ".grok"
    vscode_user = tmp_path / "vscode-user"
    monkeypatch.setenv("COPILOT_HOME", str(copilot_home))
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(vscode_user))
    return copilot_home, grok_home, vscode_user


def test_returns_all_engines_in_stable_order():
    payload = server._detect_engines_installed()
    assert [row["engine"] for row in payload["engines"]] == ALL_ENGINES
    for row in payload["engines"]:
        assert row["kind"] in ("spawn", "readonly")
        assert isinstance(row["installed"], bool)
        assert isinstance(row["label"], str) and row["label"]
        assert isinstance(row["detail"], str)
    kinds = [row["kind"] for row in payload["engines"]]
    assert kinds == ["spawn"] * 14 + ["readonly"] * 2


def test_readonly_engines_absent_stores(monkeypatch, tmp_path):
    _point_readonly_stores_at(monkeypatch, tmp_path)
    rows = _by_engine(server._detect_engines_installed())
    assert rows["copilot"]["installed"] is False
    assert rows["copilotchat"]["installed"] is False


def test_readonly_engines_present_stores(monkeypatch, tmp_path):
    copilot_home, _grok_home, vscode_user = _point_readonly_stores_at(
        monkeypatch, tmp_path
    )
    # Copilot: session-state/ dir alone (no db) counts as installed.
    (copilot_home / "session-state").mkdir(parents=True)
    # Copilot Chat: any chatSessions dir under the User dir counts.
    chat = vscode_user / "workspaceStorage" / "fakehash" / "chatSessions"
    chat.mkdir(parents=True)

    rows = _by_engine(server._detect_engines_installed())
    assert rows["copilot"]["installed"] is True
    assert rows["copilot"]["detail"]
    assert rows["copilotchat"]["installed"] is True
    assert rows["copilotchat"]["detail"] == str(chat)


def test_spawnable_missing_binary_reports_not_installed(monkeypatch, tmp_path):
    # kilo's resolver checks only CCC_KILO_BIN + shutil.which, so an empty
    # PATH deterministically yields unavailable without touching the disk.
    monkeypatch.delenv("CCC_KILO_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    payload = server._detect_engines_installed()
    rows = _by_engine(payload)
    assert rows["kilo"]["installed"] is False
    assert rows["kilo"]["detail"] == ""
    # ...and the probe never throws for the rest of the fleet either.
    assert len(payload["engines"]) == len(ALL_ENGINES)


def test_spawnable_present_binary_reports_installed(monkeypatch, tmp_path):
    fake_bin = tmp_path / "kilo"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("CCC_KILO_BIN", str(fake_bin))
    rows = _by_engine(server._detect_engines_installed())
    assert rows["kilo"]["installed"] is True
    assert rows["kilo"]["detail"] == str(fake_bin)


def test_engines_setup_is_not_part_of_the_tour():
    """Install/sign-in/re-detect lives in Settings > Engines (one implementation)
    and shows as a separate first-run screen, not as a guide step."""
    source = TOUR_JS.read_text(encoding="utf-8")
    for endpoint in ("status", "install-terminal", "login-terminal"):
        assert "/api/onboarding/" + endpoint not in source
    assert "cli-setup" not in source
    assert "Ensure agent CLIs" not in source
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "ccc-engines-first-run-done" in app
    # engine-connection.test.cjs exercises the first-run setup and its return
    # path in the browser; introductory wording is not part of that contract.


def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GROK_HOME", str(tmp_path / ".grok"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    for var in ("DEVIN_API_KEY", "XAI_API_KEY", "CCC_DEVIN_BIN", "CCC_GROK_BIN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))


def test_onboarding_status_lists_devin_and_grok_missing(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    clis = server._get_onboarding_status()["clis"]
    for engine, command, url in (
        ("devin", "devin", "https://cli.devin.ai/install.sh"),
        ("grok", "grok", "https://x.ai/cli/install.sh"),
    ):
        row = clis[engine]
        assert row["command"] == command
        assert row["available"] is False
        assert row["logged_in"] is False
        assert url in row["install_instruction"]
    assert clis["devin"]["login_instruction"] == "devin auth login"
    assert clis["grok"]["login_instruction"] == "grok login"
    # Every listed install command must be a runnable shell command, since the
    # Install button pastes it into a terminal verbatim.
    for row in clis.values():
        assert row["install_instruction"].split()[0] in ("curl", "npm")


def test_onboarding_status_devin_grok_present_and_signed_in(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("devin", "grok"):
        exe = bindir / name
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    (tmp_path / "xdg" / "devin").mkdir(parents=True)
    (tmp_path / "xdg" / "devin" / "credentials.toml").write_text("token = 'test'\n")
    (tmp_path / ".grok").mkdir()
    (tmp_path / ".grok" / "auth.json").write_text('{"k": {"t": 1}}')
    clis = server._get_onboarding_status()["clis"]
    assert clis["devin"]["available"] and clis["devin"]["logged_in"]
    assert clis["grok"]["available"] and clis["grok"]["logged_in"]
    # Detection is which() + file stats only: no subprocess on this path.
    import subprocess
    def boom(*a, **k):
        raise AssertionError("onboarding detection must not spawn a subprocess")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    server._get_onboarding_status()


def test_login_command_covers_devin_and_grok(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("devin", "grok"):
        exe = bindir / name
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert server._onboarding_login_command("devin")["argv"][1:] == ["auth", "login"]
    assert server._onboarding_login_command("grok")["argv"][1:] == ["login"]


def test_spawn_failure_gets_stable_engine_not_installed_code(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("CCC_CLAUDE_BIN", "")
    resolved = server._resolve_claude_bin()
    assert not resolved["available"] and resolved["code"] == "claude_unavailable"
    failed = {"ok": False, "error": resolved["reason"], "code": resolved["code"]}
    out = server._annotate_engine_not_installed(dict(failed), "/api/sessions/spawn")
    assert out["error_code"] == "engine_not_installed" and out["engine"] == "claude"
    assert out["error"] == resolved["reason"] and out["code"] == "claude_unavailable"
    # Per-engine endpoints and the grok `not_installed` code.
    assert server._annotate_engine_not_installed(
        {"ok": False, "code": "codex_unavailable"}, "/api/sessions/spawn-codex"
    )["engine"] == "codex"
    assert server._annotate_engine_not_installed(
        {"ok": False, "code": "not_installed"}, "/api/sessions/spawn-grok"
    )["engine"] == "grok"


def test_engine_not_installed_code_only_on_matching_failures():
    ok = {"ok": True, "code": "claude_unavailable"}
    assert "error_code" not in server._annotate_engine_not_installed(ok, "/api/sessions/spawn")
    other = {"ok": False, "error": "missing prompt"}
    assert "error_code" not in server._annotate_engine_not_installed(other, "/api/sessions/spawn")
    wrong_path = {"ok": False, "code": "claude_unavailable"}
    assert "error_code" not in server._annotate_engine_not_installed(wrong_path, "/api/other")


def test_composer_notice_is_wired_to_error_code():
    js = (ROOT / "static" / "app.js").read_text()
    assert "engine_not_installed" in js and "engineMissingNotice" in js
    assert "/api/engines/installed" in js


def test_first_run_has_optional_tailscale_step_and_defaults_row():
    js = (ROOT / "static" / "app.js").read_text()
    html = (ROOT / "static" / "index.html").read_text()
    assert "/api/phone-access/tailscale" in js and "ccc-tailscale-step-done" in js
    assert 'id="engTailscaleStep"' in html and 'id="engFirstRunDefaults"' in html
    assert "Use CCC from your phone or other machines" in html
    # The shared first-run routing editor is exercised behaviorally in
    # settings-routing-policy.test.cjs; its internal control IDs can change.
    assert "—" not in html[html.index('id="engTailscaleStep"'):html.index('id="engTailscaleStep"') + 1800]


def test_install_terminal_falls_back_for_engines_without_onboarding_entry(monkeypatch):
    monkeypatch.setattr(server.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(server, "_get_onboarding_status", lambda: {"clis": {}})
    monkeypatch.setattr(server, "_preferred_terminal_app", lambda: "Terminal")
    import subprocess
    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, *a, **k: seen.append(argv))
    res = server._launch_install_terminal("opencode")
    assert res["ok"] and res["command"] == server._FALLBACK_INSTALL_COMMANDS["opencode"] and seen
    assert not server._launch_install_terminal("not-an-engine")["ok"]
