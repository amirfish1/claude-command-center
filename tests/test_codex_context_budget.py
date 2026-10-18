"""Codex must receive CCC's context budget on new and historical threads."""
from unittest import mock

import pytest

import server
from ccc_server import codex

_spawn_budget = server._spawn_auto_compact_k_tokens


@pytest.fixture(autouse=True)
def context_defaults(monkeypatch):
    monkeypatch.delenv("CCC_CODEX_CONTEXT_TOKENS", raising=False)
    monkeypatch.setattr(server, "_spawn_auto_compact_k_tokens", lambda: 250000)


def test_budget_and_headroom_reach_both_invocation_paths():
    expected = {
        "model_context_window": 250000,
        "model_auto_compact_token_limit": 225000,
        "model_auto_compact_token_limit_scope": "total",
    }
    assert codex._codex_app_server_thread_start_params()["config"] == expected
    assert codex._codex_context_window_args() == [
        "-c", "model_context_window=250000",
        "-c", "model_auto_compact_token_limit=225000",
        "-c", 'model_auto_compact_token_limit_scope="total"',
    ]


def test_budget_uses_role_specific_persisted_preference(monkeypatch):
    monkeypatch.setattr(server, "_spawn_auto_compact_k_tokens", lambda: 120000)
    assert codex._codex_context_config()["model_context_window"] == 120000
    assert codex._codex_context_config()["model_auto_compact_token_limit"] == 108000


@pytest.mark.parametrize("legacy_flag", ["1", "0"])
def test_legacy_full_window_flag_cannot_bypass_budget(monkeypatch, legacy_flag):
    monkeypatch.setenv("CCC_CODEX_CONTEXT_1M", legacy_flag)
    assert codex._codex_context_config()["model_context_window"] == 250000


@pytest.mark.parametrize("override", ["bad", "0", "-100", "9999", "1000001"])
def test_invalid_explicit_window_keeps_budget(monkeypatch, override):
    monkeypatch.setenv("CCC_CODEX_CONTEXT_TOKENS", override)
    assert codex._codex_context_config()["model_context_window"] == 250000


def test_larger_windows_require_explicit_numeric_override(monkeypatch):
    monkeypatch.setenv("CCC_CODEX_CONTEXT_TOKENS", "500000")
    assert codex._codex_context_config()["model_auto_compact_token_limit"] == 450000


def test_disabled_claude_compaction_does_not_remove_codex_ceiling(monkeypatch):
    monkeypatch.setattr(server, "_spawn_auto_compact_k_tokens", lambda: 0)
    assert codex._codex_context_config()["model_context_window"] == 250000


@pytest.mark.parametrize("method", ["thread/start", "thread/resume"])
def test_rpc_replaces_historical_context_and_preserves_other_options(method):
    params = {"threadId": "example-thread", "config": {
        "model_context_window": 1000000,
        "model_auto_compact_token_limit": 950000,
        "model_auto_compact_token_limit_scope": "body_after_prefix",
        "unrelated_option": True,
    }}
    with mock.patch.object(server, "_control_plane_engine_call", return_value={"response": {"result": {}}}) as route:
        codex._codex_app_server_request(method, params)
    config = route.call_args.args[2]["params"]["config"]
    assert config["model_context_window"] == 250000
    assert config["model_auto_compact_token_limit"] == 225000
    assert config["model_auto_compact_token_limit_scope"] == "total"
    assert config["unrelated_option"] is True
    assert params["config"]["model_context_window"] == 1000000


@pytest.mark.parametrize("worker, expected", [(False, 250000), (True, 180000)])
def test_real_budget_selects_dashboard_or_worker_defaults(monkeypatch, worker, expected):
    monkeypatch.setattr(server, "_spawn_auto_compact_k_tokens", _spawn_budget)
    monkeypatch.setattr(server, "_load_spawn_defaults", lambda: {
        "auto_compact_k": 250, "worker_auto_compact_k": 180,
    })
    if worker:
        monkeypatch.setenv("CCC_WORKER_PROCESS", "1")
    else:
        monkeypatch.delenv("CCC_WORKER_PROCESS", raising=False)
    assert codex._codex_context_config()["model_context_window"] == expected
