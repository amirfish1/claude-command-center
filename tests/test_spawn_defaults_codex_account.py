"""Settings > Agents: the default Codex account for new sessions."""

import server
from ccc_server import codex_accounts


def _isolate(monkeypatch, tmp_path, accounts=("local-codex", "secondary")):
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "SPAWN_DEFAULTS_FILE", tmp_path / "spawn-defaults.json")
    for a in accounts:
        (tmp_path / a).mkdir(exist_ok=True)
    monkeypatch.setattr(codex_accounts, "configured", lambda: [{"id": a, "codex_home": str(tmp_path / a)} for a in accounts])
    monkeypatch.setattr(codex_accounts, "default_home", lambda: (tmp_path / "local-codex").resolve())


def test_codex_account_round_trip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert server._load_spawn_defaults().get("codex_account_id", "") == ""
    saved = server._save_spawn_defaults({"engine": "codex", "codex_account_id": "secondary"})
    assert saved["ok"] is True
    assert server._load_spawn_defaults()["codex_account_id"] == "secondary"
    # Other saves keep it; __current__ clears it.
    server._save_spawn_defaults({"auto_compact_k": 300})
    assert server._load_spawn_defaults()["codex_account_id"] == "secondary"
    server._save_spawn_defaults({"codex_account_id": "__current__"})
    assert server._load_spawn_defaults()["codex_account_id"] == ""


def test_unknown_codex_account_is_rejected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert server._save_spawn_defaults({"codex_account_id": "nope"})["ok"] is False
    assert server._save_spawn_defaults({"codex_account_id": 5})["ok"] is False


def test_spawn_uses_default_account_unless_named(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    server._save_spawn_defaults({"codex_account_id": "secondary"})
    assert server._apply_default_codex_account({"prompt": "x"})["codex_account_id"] == "secondary"
    # A removed account falls back to the machine login instead of failing.
    monkeypatch.setattr(codex_accounts, "configured", lambda: [{"id": "local-codex"}])
    assert "codex_account_id" not in server._apply_default_codex_account({"prompt": "x"})


def test_worker_account_publishes_home_for_watchtower(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    saved = server._save_spawn_defaults({"worker_codex_account_id": "secondary"})
    assert saved["ok"] is True
    stored = server.json.loads(server.SPAWN_DEFAULTS_FILE.read_text())
    assert stored["worker_codex_account_id"] == "secondary"
    assert stored["worker_codex_home"] == str((tmp_path / "secondary").resolve())
    assert sorted(stored["codex_account_homes"]) == sorted(
        str((tmp_path / a).resolve()) for a in ("local-codex", "secondary"))
    assert server._load_spawn_defaults()["worker_codex_account_id"] == "secondary"
    # The session account stays independent of the worker account.
    assert server._load_spawn_defaults()["codex_account_id"] == ""
    # The primary (machine default) home means "no override" for WatchTower.
    server._save_spawn_defaults({"worker_codex_account_id": "local-codex"})
    assert server.json.loads(server.SPAWN_DEFAULTS_FILE.read_text())["worker_codex_home"] == ""
    assert server._save_spawn_defaults({"worker_codex_account_id": "nope"})["ok"] is False
