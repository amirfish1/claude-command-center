"""Settings > Engines enable/disable: `disabled_engines` in spawn defaults."""

import server


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "SPAWN_DEFAULTS_FILE", tmp_path / "spawn-defaults.json")


def test_disabled_engines_round_trip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert server._load_spawn_defaults()["disabled_engines"] == []

    saved = server._save_spawn_defaults({"disabled_engines": ["kilo", "aider", "kilo", "nope"]})
    assert saved["ok"] is True
    assert saved["disabled_engines"] == ["kilo", "aider"]
    assert server._load_spawn_defaults()["disabled_engines"] == ["kilo", "aider"]


def test_other_saves_keep_disabled_engines(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    server._save_spawn_defaults({"disabled_engines": ["kilo"]})
    saved = server._save_spawn_defaults({"auto_compact_k": 300})
    assert saved["disabled_engines"] == ["kilo"]


def test_default_engines_cannot_be_disabled(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    server._save_spawn_defaults({"engine": "claude", "worker_engine": "codex"})
    saved = server._save_spawn_defaults({"disabled_engines": ["claude", "codex", "kilo"]})
    assert saved["disabled_engines"] == ["kilo"]

    # Picking a disabled engine as the default switches it back on.
    saved = server._save_spawn_defaults({"engine": "kilo"})
    assert saved["disabled_engines"] == []


def test_disabled_engines_must_be_a_list(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    rejected = server._save_spawn_defaults({"disabled_engines": "kilo"})
    assert rejected["ok"] is False
