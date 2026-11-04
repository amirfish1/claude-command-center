"""Settings > Agents: the default Codex account for new sessions."""

import server
from ccc_server import codex_accounts


def _isolate(monkeypatch, tmp_path, accounts=("local-codex", "secondary")):
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "SPAWN_DEFAULTS_FILE", tmp_path / "spawn-defaults.json")
    monkeypatch.setattr(codex_accounts, "configured", lambda: [{"id": a} for a in accounts])


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
