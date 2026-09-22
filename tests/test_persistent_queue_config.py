"""CCC must use the same durable settings file as WatchTower."""

import json
import shutil

import server


def test_dashboard_and_watchtower_share_config_after_settings_removal(tmp_path, monkeypatch):
    config = server._wt_config
    legacy = tmp_path / ".watchtower/queue-config.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"PERSIST": {"engine": "codex", "auto_drain": False}}))
    monkeypatch.delenv("WATCHTOWER_CONFIG_FILE", raising=False)
    monkeypatch.delenv("WATCHTOWER_HOME", raising=False)
    monkeypatch.setenv("WATCHTOWER_DATA_DIR", str(tmp_path / "durable"))
    monkeypatch.setattr(config, "_LEGACY_CONFIG_FILE", legacy)
    monkeypatch.setattr(config, "CONFIG_FILE", legacy)

    assert server._wt_config_path() == tmp_path / "durable/queue-config.json"
    assert server._wt_read_config()["PERSIST"]["engine"] == "codex"
    shutil.rmtree(legacy.parent)
    config.set_engine("PERSIST", "claude")
    assert server._wt_read_config()["PERSIST"]["engine"] == "claude"
    # Direct dashboard writes must reach the file WT subsequently reads too.
    path = server._wt_config_path()
    data = json.loads(path.read_text())
    data["PERSIST"]["auto_drain"] = True
    path.write_text(json.dumps(data))
    assert config.auto_drain("PERSIST") is True


def test_explicit_config_path_still_wins(tmp_path, monkeypatch):
    explicit = tmp_path / "custom.json"
    monkeypatch.setenv("WATCHTOWER_CONFIG_FILE", str(explicit))
    assert server._wt_config_path() == explicit
