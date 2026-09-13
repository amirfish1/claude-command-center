"""The conv-meta cache file must be redirectable, and the suite must redirect it.

server.py loads ~/.claude/command-center/conv_meta_cache.json at import. A test
process that imports server therefore holds the user's real cache in memory;
any test that adds fixture entries and triggers _save_conv_meta_cache() (or
clears the cache first) writes that back over the real file. Observed
2026-09-12: the live dashboard restarted onto a 1,000-entry tmp-path cache and
cold-reparsed every transcript (25 s archive list, 20 s archive_load).
"""
import importlib
import os
import sys
from pathlib import Path


def _fresh_server():
    for mod in ("server", "morning", "morning_store"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


def test_conv_meta_cache_file_honours_env_override(tmp_path, monkeypatch):
    target = tmp_path / "meta.json"
    monkeypatch.setenv("CCC_CONV_META_CACHE_FILE", str(target))
    server = _fresh_server()
    assert Path(server._CONV_META_CACHE_FILE) == target


def test_suite_never_points_at_the_real_cache_file():
    # conftest.py sets the override for the whole session, before any test
    # module imports server.
    real = Path.home() / ".claude" / "command-center" / "conv_meta_cache.json"
    configured = os.environ.get("CCC_CONV_META_CACHE_FILE", "")
    assert configured, "CCC_CONV_META_CACHE_FILE is not set for the test session"
    assert Path(configured) != real
    server = _fresh_server()
    assert Path(server._CONV_META_CACHE_FILE) != real
