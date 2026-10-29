"""Persisted routing policy and validation; no provider calls are made."""
import copy
import json
import threading
import urllib.error
import urllib.request

import pytest
import server


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "SPAWN_DEFAULTS_FILE", tmp_path / "spawn-defaults.json")
    monkeypatch.setattr(server, "_worker_fallback_catalog_models", lambda engine: {
        "codex": [{"id": "gpt-6.1-sol"}],
        "claude": [{"id": "claude-sonnet-4-6"}],
    }.get(engine, []))
    monkeypatch.setattr(server, "_model_policy_blocks", lambda model: False)
    return tmp_path / "spawn-defaults.json"


def policy():
    return {"enabled": True, "models": [
        {"engine": "claude", "model": "claude-sonnet-4-6", "effort": "high"},
        {"engine": "codex", "model": "gpt-6.1-sol", "effort": "medium"},
    ]}


def test_legacy_read_does_not_enable_or_write_routes(isolated):
    legacy = server._factory_spawn_defaults()
    legacy.pop("worker_fallback")
    legacy.pop("model_profiles")
    isolated.write_text(json.dumps(legacy))
    before = isolated.read_text()
    loaded = server._load_spawn_defaults()
    assert loaded["worker_fallback"] == {"enabled": False, "models": []}
    assert loaded["model_profiles"] == {}
    assert isolated.read_text() == before


def test_order_effort_profiles_and_partial_saves_persist(isolated):
    routes = policy()
    profiles = {"deep": {"models": routes["models"][:1]}, "fast": {"models": []}}
    saved = server._save_spawn_defaults({"worker_fallback": routes, "model_profiles": profiles})
    assert saved["ok"]
    saved = server._save_spawn_defaults({"worker_auto_compact_k": 300})
    assert saved["worker_fallback"] == routes
    assert server._load_spawn_defaults()["model_profiles"] == profiles
    assert json.loads(isolated.read_text())["worker_fallback"] == routes


@pytest.mark.parametrize("change", [
    lambda p: p.update(enabled="true"),
    lambda p: p.update(models=[]),
    lambda p: p["models"].append(copy.deepcopy(p["models"][0])),
    lambda p: p["models"][1].update(effort="invalid"),
    lambda p: p["models"][1].update(model="unconfigured-model"),
    lambda p: p["models"][1].update(engine="unsupported"),
])
def test_invalid_policy_rejected_without_changing_saved_state(isolated, change):
    assert server._save_spawn_defaults({"worker_fallback": policy()})["ok"]
    before = isolated.read_text()
    bad = policy()
    change(bad)
    assert not server._save_spawn_defaults({"worker_fallback": bad})["ok"]
    assert isolated.read_text() == before


def test_profile_names_rejected_and_empty_profile_supported(isolated):
    assert not server._save_spawn_defaults({"model_profiles": {"unknown": {"models": []}}})["ok"]
    assert server._save_spawn_defaults({"model_profiles": {"standard": {"models": []}}})["ok"]


def test_profile_keeps_distinct_models_on_one_engine(isolated, monkeypatch):
    """A deep profile of opus then sonnet must survive load and unrelated saves;
    worker fallback stays one entry per engine."""
    monkeypatch.setattr(server, "_worker_fallback_catalog_models", lambda engine: [
        {"id": "claude-opus-5-5"}, {"id": "claude-sonnet-5-5"}] if engine == "claude" else [])
    rows = [{"engine": "claude", "model": "claude-opus-5-5", "effort": "high"},
            {"engine": "claude", "model": "claude-sonnet-5-5", "effort": "high"}]
    assert server._save_spawn_defaults({"model_profiles": {"deep": {"models": rows}}})["ok"]
    assert server._save_spawn_defaults({"auto_compact_k": 300})["ok"]
    assert server._load_spawn_defaults()["model_profiles"]["deep"]["models"] == rows
    assert json.loads(isolated.read_text())["model_profiles"]["deep"]["models"] == rows
    assert not server._save_spawn_defaults({"model_profiles": {"deep": {"models": rows + rows[:1]}}})["ok"]
    assert not server._save_spawn_defaults({"worker_fallback": {"enabled": True, "models": rows}})["ok"]


def test_http_save_then_reload_persists(isolated):
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/api/spawn-defaults"
    try:
        request = urllib.request.Request(url, data=json.dumps({"worker_fallback": policy(), "model_profiles": {"deep": {"models": policy()["models"][:1]}}}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.loads(response.read())["worker_fallback"] == policy()
        with urllib.request.urlopen(url, timeout=5) as response:
            saved = json.loads(response.read())
        assert saved["worker_fallback"] == policy()
        assert saved["model_profiles"]["deep"]["models"] == policy()["models"][:1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_profile_rejects_codex_without_managed_no_tools_guarantee(isolated):
    saved = server._save_spawn_defaults({"model_profiles": {"deep": {"models": policy()["models"][1:]}}})
    assert not saved["ok"]
    assert "no-tools" in saved["error"]


def test_ordinary_save_does_not_create_legacy_routing_keys(isolated):
    legacy = server._factory_spawn_defaults()
    legacy.pop("worker_fallback")
    legacy.pop("model_profiles")
    isolated.write_text(json.dumps(legacy))
    assert server._save_spawn_defaults({"auto_compact_k": 300})["ok"]
    stored = json.loads(isolated.read_text())
    assert "worker_fallback" not in stored
    assert "model_profiles" not in stored


def test_ordinary_ui_save_omits_routing_fields():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    body = text.split("const payload = focusedRouting ?", 1)[1].split("    try {", 1)[0]
    ordinary = body.rsplit(" : {", 1)[1]
    assert "worker_fallback:" not in ordinary
    assert "model_profiles:" not in ordinary


def test_profile_save_does_not_create_legacy_worker_chain(isolated):
    legacy = server._factory_spawn_defaults()
    legacy.pop("worker_fallback")
    legacy.pop("model_profiles")
    isolated.write_text(json.dumps(legacy))
    assert server._save_spawn_defaults({"model_profiles": {"standard": {"models": policy()["models"][:1]}}})["ok"]
    stored = json.loads(isolated.read_text())
    assert "worker_fallback" not in stored
    assert stored["model_profiles"]["standard"]["models"] == policy()["models"][:1]


def test_unsupported_stored_profile_remains_visible_on_read(isolated):
    raw = server._factory_spawn_defaults()
    raw["model_profiles"] = {"deep": {"models": policy()["models"][1:]}}
    isolated.write_text(json.dumps(raw))
    assert server._load_spawn_defaults()["model_profiles"] == raw["model_profiles"]
