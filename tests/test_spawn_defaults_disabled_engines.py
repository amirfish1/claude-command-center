"""Settings > Engines enable/disable: `disabled_engines` in spawn defaults."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import server


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "SPAWN_DEFAULTS_FILE", tmp_path / "spawn-defaults.json")


def test_disabled_engines_round_trip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert server._load_spawn_defaults()["disabled_engines"] == []

    saved = server._save_spawn_defaults({"disabled_engines": ["kilo", "aider", "kilo", "nope"]})
    assert saved["ok"] is True
    assert saved["disabled_engines"] == ["kilo", "aider"]
    assert server._load_spawn_defaults()["disabled_engines"] == ["kilo", "aider"]


def test_other_saves_keep_disabled_engines(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    server._save_spawn_defaults({"disabled_engines": ["kilo"]})
    saved = server._save_spawn_defaults({"auto_compact_k": 300})
    assert saved["disabled_engines"] == ["kilo"]


def test_default_engines_cannot_be_disabled(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    server._save_spawn_defaults({"engine": "claude", "worker_engine": "codex"})
    saved = server._save_spawn_defaults({"disabled_engines": ["claude", "codex", "kilo"]})
    assert saved["disabled_engines"] == ["kilo"]

    # Picking a disabled engine as the default switches it back on.
    saved = server._save_spawn_defaults({"engine": "kilo"})
    assert saved["disabled_engines"] == []


def test_disabled_engines_must_be_a_list(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    rejected = server._save_spawn_defaults({"disabled_engines": "kilo"})
    assert rejected["ok"] is False


def test_concurrent_first_run_writes_have_unique_temp_files(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    ready = threading.Barrier(2)
    replace = Path.replace
    temporary_files = []

    def synchronized_replace(source, target):
        if target == server.SPAWN_DEFAULTS_FILE:
            temporary_files.append(source)
            ready.wait(timeout=5)
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(server._write_spawn_defaults_file, {"engine": engine})
                for engine in ("claude", "codex")]
        for job in jobs:
            job.result(timeout=10)
    assert len(set(temporary_files)) == 2
    assert json.loads(server.SPAWN_DEFAULTS_FILE.read_text()) in (
        {"engine": "claude"}, {"engine": "codex"},
    )
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_defaults_write_preserves_previous_file_and_cleans_up(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    server._write_spawn_defaults_file({"engine": "claude"})
    with pytest.raises(TypeError):
        server._write_spawn_defaults_file({"engine": object()})
    assert json.loads(server.SPAWN_DEFAULTS_FILE.read_text()) == {"engine": "claude"}
    assert not list(tmp_path.glob("*.tmp"))
