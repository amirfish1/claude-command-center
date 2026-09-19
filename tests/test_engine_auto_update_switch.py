"""Settings > Engines: the on/off switch for the hourly CLI update pass."""

import server


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_ENGINE_UPDATE_STATE_FILE", tmp_path / "engine-updates.json")
    calls = []
    monkeypatch.setattr(server, "_run_engine_updates_once", lambda: calls.append(1) or {"ok": True})
    monkeypatch.setattr(server, "_refresh_claude_model_catalog", lambda: {"ok": True})
    monkeypatch.setattr(server, "_worker_compat_maintenance_check", lambda: {"ok": True})
    return calls


def test_automatic_defaults_on(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert server._engine_update_status()["automatic"] is True


def test_switch_off_skips_hourly_pass_but_not_update_now(monkeypatch, tmp_path):
    calls = _isolate(monkeypatch, tmp_path)
    assert server._set_engine_auto_update(False)["automatic"] is False
    assert server._engine_update_status()["automatic"] is False

    server._engine_maintenance_once()
    assert calls == []

    server._engine_maintenance_once(force_updates=True)
    assert calls == [1]

    server._set_engine_auto_update(True)
    server._engine_maintenance_once()
    assert calls == [1, 1]
