"""Tests must never reach the user's live On-call store (it alerts a real person)."""
from pathlib import Path

from ccc_server.oncall import engine, store
from ccc_server.paths import COMMAND_CENTER_STATE_DIR


def test_the_test_process_uses_an_empty_oncall_store():
    live = (Path(COMMAND_CENTER_STATE_DIR) / "oncall").resolve()
    root = Path(store.default_root()).resolve()
    assert root != live and live not in root.parents


def test_the_shared_engine_is_off_in_tests():
    eng = engine.get()
    assert Path(eng.store.root).resolve() != (Path(COMMAND_CENTER_STATE_DIR) / "oncall").resolve()
    assert not eng.config().get("enabled")
