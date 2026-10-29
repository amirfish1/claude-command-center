"""Focused contract tests for confirm-card session proposals."""

import server  # registers the engine-aware effort validator on ccc_server.core
from ccc_server import assistant_actions as actions


def test_spawn_proposal_preserves_engine_aware_effort(monkeypatch, tmp_path):
    monkeypatch.setattr("ccc_server.core._validate_reasoning_effort",
                        lambda value, engine, strict=False: "max" if value == "max" and engine == "claude" else None)
    params = actions.validate("spawn_session", {
        "cwd": str(tmp_path), "prompt": "triage", "engine": "claude", "effort": "max",
    })
    assert params["effort"] == "max"
    calls = []
    executor = actions.make_executor("http://example.test", post=lambda base, path, body: calls.append(body) or {"ok": True})
    assert executor("spawn_session", params)["ok"]
    assert calls == [{"cwd": str(tmp_path.resolve()), "prompt": "triage", "engine": "claude", "effort": "max"}]
