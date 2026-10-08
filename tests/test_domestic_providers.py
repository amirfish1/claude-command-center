import io
import json
import re
from pathlib import Path
from unittest import mock

import pytest
import server
from ccc_server import byok, domestic_providers as dp, free_providers
from ccc_server.domestic_presets import DOMESTIC_PRESETS, MODEL_ENV_VARS

KEY = "sk-test-XXXX-domestic-XXXX"
MODEL = "byok/kimi-intl/kimi-k3"
SETTINGS_PATHS = dp._settings_paths


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CCC_SSH_HOST", raising=False)
    monkeypatch.setattr(byok, "_state_dir", lambda: tmp_path / "byok")
    monkeypatch.setattr(byok, "_keychain_available", lambda: False)
    monkeypatch.setattr(dp, "_settings_paths", lambda cwd: [tmp_path / "settings.json"])
    monkeypatch.setattr(server, "_MODEL_CATALOG_CACHE", {"ts": 1.0, "data": {}})


def test_registry_has_five_families_and_separate_regions():
    rows = dp.catalog()["presets"]
    assert len(rows) == 9
    assert {p["family"] for p in rows} == {"glm", "kimi", "deepseek", "qwen", "minimax"}
    assert len({p["id"] for p in rows}) == len(rows)
    for p in rows:
        assert p["paid"] is True
        assert p["price_note"] and p["region_note"]
        assert p["base_url"].startswith("https://")
        assert p["signup_url"].startswith("https://")
        assert p["docs_url"].startswith("https://")
        assert re.fullmatch(p["key_regex"], KEY)
        assert p["model_ids"] == [f"byok/{p['id']}/{m}" for m in p["models"]]
        assert p["id"] not in {free["platform"] for free in free_providers.FREE_PROVIDERS}
    assert {p["no_key_status"] for p in rows if p["family"] == "qwen"} == {403}
    assert next(p for p in rows if p["id"] == "minimax-cn")["base_url"] == "https://api.minimax.cn/anthropic"


def test_catalog_only_checks_default_profile_index_not_keychain():
    assert byok.byok_set_key("work", "kimi-intl", KEY)
    with mock.patch.object(byok, "byok_get_key", side_effect=AssertionError("catalog read a secret")):
        assert not next(p for p in dp.catalog()["presets"] if p["id"] == "kimi-intl")["configured"]
    assert dp.save_key("kimi-intl", KEY)["ok"]
    with mock.patch.object(byok, "byok_get_key", side_effect=AssertionError("catalog read a secret")):
        assert next(p for p in dp.catalog()["presets"] if p["id"] == "kimi-intl")["configured"]
        assert next(m for m in dp.model_records() if m["id"] == MODEL)["available"]


@pytest.mark.parametrize("preset", [p["id"] for p in DOMESTIC_PRESETS])
def test_key_roundtrip_never_echoes_or_stores_plaintext(preset, tmp_path):
    result = dp.save_key(preset, KEY)
    assert result == {"ok": True, "validated": False, "message": "Key saved. Your first run will test it."}
    assert byok.byok_get_key("default", preset) == KEY
    assert KEY not in json.dumps(dp.catalog())
    assert KEY not in (tmp_path / "byok" / "index.json").read_text()
    assert KEY not in (tmp_path / "byok" / "profiles.enc.json").read_text()
    assert server._MODEL_CATALOG_CACHE["data"] is None
    assert dp.remove_key(preset)["ok"]
    assert byok.byok_get_key("default", preset) is None


@pytest.mark.parametrize("key", [None, [], {}, 12, "", "short", "quote\"test-XXXX-domestic", "sk-test XXXX-domestic", "x" * 4097])
def test_bad_key_is_not_saved_or_echoed(key):
    result = dp.save_key("kimi-intl", key)
    assert result["code"] == "key_format"
    assert not byok.byok_list_profiles()


@pytest.mark.parametrize("key", ["sk-ant-test-XXXX", "sk-ant-oat01-test-XXXX", "oauth-test-XXXX-XXXX"])
def test_claude_credentials_are_not_saved(key):
    result = dp.save_key("kimi-intl", key)
    assert result["code"] == "wrong_key_type"
    assert key not in json.dumps(result)
    assert not byok.byok_list_profiles()


@pytest.mark.parametrize("preset", ["qwen-cn", "qwen-intl"])
def test_coding_plan_key_is_rejected_for_pay_as_you_go(preset):
    result = dp.save_key(preset, "sk-sp-test-XXXX-XXXX")
    assert result["code"] == "wrong_key_type"
    assert "Coding Plan" in result["error"]


def test_unknown_presets_and_storage_exceptions_are_secret_safe():
    for invalid in (None, [], "https://example.com", KEY):
        result = dp.save_key(invalid, KEY)
        assert result["code"] == "unknown_preset"
        assert KEY not in json.dumps(result)
        assert dp.remove_key(invalid)["code"] == "unknown_preset"
    with mock.patch.object(byok, "byok_set_key", side_effect=RuntimeError(KEY)):
        result = dp.save_key("kimi-intl", KEY)
    assert result["code"] == "storage_error"
    assert KEY not in json.dumps(result)


def test_region_keys_do_not_cross_endpoints():
    assert dp.save_key("kimi-intl", KEY)["ok"]
    with pytest.raises(dp.DomesticProviderError) as error:
        dp.resolve_spawn("byok/kimi-cn/kimi-k3")
    assert error.value.code == "preset_key_missing"
    assert dp.resolve_spawn(MODEL)["env"]["ANTHROPIC_BASE_URL"] == "https://api.moonshot.ai/anthropic"


def test_all_tier_models_are_vendor_models_and_credentials_are_scrubbed():
    dp.save_key("kimi-intl", KEY)
    resolved = dp.resolve_spawn(MODEL)
    assert resolved["model"] == "kimi-k3"
    assert all(resolved["env"][name] == "kimi-k3" for name in MODEL_ENV_VARS)
    child = {"ANTHROPIC_API_KEY": "sk-ant-test-XXXX", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-test-XXXX",
             "CLAUDE_CODE_SESSION_KEY": "session-test-XXXX", "ANTHROPIC_AUTH_TOKEN": "old-test-XXXX",
             "CCC_SESSION_RUNTIME": "free", "PATH": "test-path"}
    dp.apply_env(child, resolved["env"])
    assert child["ANTHROPIC_AUTH_TOKEN"] == KEY
    assert child["PATH"] == "test-path"
    assert child["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_SESSION_KEY", "CCC_SESSION_RUNTIME"):
        assert name not in child


@pytest.mark.parametrize("model", ["byok/", "byok/unknown/test-XXXX", "byok/kimi-intl/not-a-model", "BYOK/kimi-intl/KIMI-K3"])
def test_unknown_qualified_models_refuse(model):
    with pytest.raises(dp.DomesticProviderError):
        dp.resolve_model(model)


@pytest.mark.parametrize("args", [("claude", MODEL, "free"), ("codex", MODEL, ""), ("opencode", MODEL, "")])
def test_free_and_other_engines_cannot_use_paid_presets(args):
    result = dp.request_error(*args)
    assert result["ok"] is False


def test_remote_and_non_default_profiles_refuse():
    assert dp.request_error("claude", MODEL, remote=True)["ok"] is False
    assert dp.request_error("claude", MODEL, key_profile="work")["ok"] is False
    assert dp.request_error("claude", MODEL, key_profile="default") is None
    assert dp.resolve_model("claude-sonnet-5") is None
    assert dp.resolve_spawn("claude-sonnet-5") is None


@pytest.mark.parametrize("env", [{"ANTHROPIC_BASE_URL": "https://example.com"},
                                 {"ANTHROPIC_API_KEY": "sk-ant-test-XXXX"},
                                 {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-test-XXXX"},
                                 {"ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-test"}])
def test_existing_settings_cannot_override_provider(env, tmp_path):
    dp.save_key("kimi-intl", KEY)
    settings = tmp_path / "settings.json"
    original = json.dumps({"env": env, "permissions": {"deny": ["test"]}})
    settings.write_text(original)
    with pytest.raises(dp.DomesticProviderError) as error:
        dp.resolve_spawn(MODEL)
    assert error.value.code == "routing_settings_conflict"
    assert KEY not in str(error.value)
    assert settings.read_text() == original


def test_matching_settings_and_non_routing_settings_are_preserved(tmp_path):
    dp.save_key("kimi-intl", KEY)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://api.moonshot.ai/anthropic"}, "permissions": {"deny": ["test"]}}))
    assert dp.resolve_spawn(MODEL)["model"] == "kimi-k3"
    settings.write_text("{bad JSON")
    with pytest.raises(dp.DomesticProviderError):
        dp.resolve_spawn(MODEL)


def test_settings_guard_includes_project_parent_and_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "_settings_paths", SETTINGS_PATHS)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "repo"
    subdir = project / "src"
    subdir.mkdir(parents=True)
    (project / ".git").mkdir()
    paths = dp._settings_paths(subdir)
    assert project / ".claude" / "settings.json" in paths
    assert subdir / ".claude" / "settings.local.json" in paths
    assert tmp_path / ".claude" / "settings.json" not in paths


class Handler:
    def __init__(self, body=b"{}", route="/api/domestic-providers/keys", length=None):
        self.rfile = io.BytesIO(body)
        self.path = route
        self.headers = {"Content-Length": str(len(body) if length is None else length)}
        self.responses = []

    def send_json(self, body, status=200):
        self.responses.append((body, status))


@pytest.mark.parametrize("body,length,status", [(b"[]", None, 400), (b"{bad", None, 400), (b"{}", 17000, 413), (b"{}", -1, 400), (b"{}", "bad", 400)])
def test_http_input_is_bounded_and_typed(body, length, status):
    handler = Handler(body, length=length)
    dp.handle(handler, "POST")
    assert handler.responses[0][1] == status


def test_http_catalog_save_and_remove():
    handler = Handler(json.dumps({"preset": "kimi-intl", "key": KEY}).encode())
    dp.handle(handler, "POST")
    assert handler.responses[0][0]["ok"]
    assert KEY not in json.dumps(handler.responses)
    get = Handler()
    dp.handle(get, "GET")
    assert any(p["id"] == "kimi-intl" and p["configured"] for p in get.responses[0][0]["presets"])
    remove = Handler(b'{"preset":"kimi-intl"}', "/api/domestic-providers/keys/remove")
    dp.handle(remove, "POST")
    assert remove.responses == [({"ok": True}, 200)]


def test_spawn_refuses_missing_key_before_prewarm_or_process(monkeypatch):
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_take_claude_prewarm_for_request", mock.Mock(side_effect=AssertionError("claimed paid prewarm")))
    result = server.spawn_session("Say hello.", model=MODEL)
    assert result["code"] == "preset_key_missing"


def test_worker_receives_qualified_model_not_a_secret(monkeypatch):
    calls = []
    def route(engine, operation, args, **kwargs):
        calls.append((engine, operation, args))
        return {"ok": True, "session_id": "test-session-XXXX"}
    monkeypatch.setattr(server, "_control_plane_engine_call", route)
    assert server.spawn_session("Say hello.", model=MODEL)["ok"]
    assert calls[0][2]["model"] == MODEL
    assert "env" not in calls[0][2] and "extra_env" not in calls[0][2]
    assert KEY not in json.dumps(calls)


def test_free_spawn_and_remote_spawn_refuse_before_worker(monkeypatch):
    monkeypatch.setattr(server, "_control_plane_engine_call", mock.Mock(side_effect=AssertionError("routed invalid preset")))
    assert server.spawn_session("Say hello.", model=MODEL, runtime="free")["code"] == "paid_preset_not_free"
    monkeypatch.setenv("CCC_SSH_HOST", "test-host")
    assert server.spawn_session("Say hello.", model=MODEL)["ok"] is False


def test_cold_resume_without_key_refuses_before_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_claude_subagent_parent_session_id", lambda sid: None)
    monkeypatch.setattr(server, "_get_session_override", lambda sid: {"model": MODEL})
    monkeypatch.setattr(server, "_spawned_sessions", [])
    monkeypatch.setattr(server, "_resolve_cwd_context", lambda cwd: {"cwd": str(tmp_path), "repo_path": str(tmp_path)})
    result = server.resume_session_headless("test-session-XXXX", "Say hello.", cwd=str(tmp_path))
    assert result["code"] == "preset_key_missing"


def test_live_process_cannot_silently_change_paid_provider(monkeypatch):
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_claude_subagent_parent_session_id", lambda sid: None)
    monkeypatch.setattr(server, "_get_session_override", lambda sid: {"model": MODEL})
    monkeypatch.setattr(server, "_spawned_sessions", [{"session_id": "test-session-XXXX", "model": "byok/kimi-cn/kimi-k3"}])
    monkeypatch.setattr(server, "_poll_spawn_entry", lambda entry: None)
    result = server.resume_session_headless("test-session-XXXX", "Say hello.")
    assert result["code"] == "preset_live_model_changed"
