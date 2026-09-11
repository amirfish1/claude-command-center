"""Pi's presence in the orchestration engine allowlist (W3-3): payload
normalization + engine allowlist side of /api/sessions/spawn's dispatch
chain, mirroring the same check for aider in test_aider_engine.py."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _fresh_server():
    for name in ("server", "morning", "morning_store"):
        sys.modules.pop(name, None)
    return importlib.import_module("server")


def test_pi_is_a_known_orchestration_spawn_engine():
    server = _fresh_server()
    assert "pi" in server._ORCHESTRATION_SPAWN_ENGINES
    engine, model = server._spawn_request_engine_and_model({"engine": "pi"})
    assert engine == "pi"


def test_pi_engine_alias_normalizes():
    server = _fresh_server()
    assert server._normalize_orchestration_spawn_engine("PI") == "pi"
