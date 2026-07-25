"""Tests for GET /api/engines/installed (First Flight tour welcome chips).

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
    "kilo", "kimi", "hermes",
    "copilot", "grok", "copilotchat",
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


def test_returns_all_eleven_engines_in_stable_order():
    payload = server._detect_engines_installed()
    assert [row["engine"] for row in payload["engines"]] == ALL_ENGINES
    for row in payload["engines"]:
        assert row["kind"] in ("spawn", "readonly")
        assert isinstance(row["installed"], bool)
        assert isinstance(row["label"], str) and row["label"]
        assert isinstance(row["detail"], str)
    kinds = [row["kind"] for row in payload["engines"]]
    assert kinds == ["spawn"] * 8 + ["readonly"] * 3


def test_readonly_engines_absent_stores(monkeypatch, tmp_path):
    _point_readonly_stores_at(monkeypatch, tmp_path)
    rows = _by_engine(server._detect_engines_installed())
    assert rows["copilot"]["installed"] is False
    assert rows["grok"]["installed"] is False
    assert rows["copilotchat"]["installed"] is False


def test_readonly_engines_present_stores(monkeypatch, tmp_path):
    copilot_home, grok_home, vscode_user = _point_readonly_stores_at(
        monkeypatch, tmp_path
    )
    # Copilot: session-state/ dir alone (no db) counts as installed.
    (copilot_home / "session-state").mkdir(parents=True)
    # Grok: grok.db alone (no sessions/ dir) counts as installed.
    grok_home.mkdir(parents=True)
    (grok_home / "grok.db").write_bytes(b"fake-sqlite")
    # Copilot Chat: any chatSessions dir under the User dir counts.
    chat = vscode_user / "workspaceStorage" / "fakehash" / "chatSessions"
    chat.mkdir(parents=True)

    rows = _by_engine(server._detect_engines_installed())
    assert rows["copilot"]["installed"] is True
    assert rows["copilot"]["detail"]
    assert rows["grok"]["installed"] is True
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


def test_tour_js_references_installed_engines_endpoint():
    source = TOUR_JS.read_text(encoding="utf-8")
    assert "/api/engines/installed" in source
