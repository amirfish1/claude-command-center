"""Shared pytest fixtures.

`server.py` memoises its ps-backed liveness scans (`_ttl_memo`, ~3s TTL). Those
caches are module-global, so a value cached by one test would leak into the
next and make direct-scan assertions flaky. Reset them before every test. We
reach for whatever `server` module is currently imported, since the suite
re-imports it (`_fresh_server`) to pick up per-test env.
"""
import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_ccc_ttl_caches():
    def _reset():
        mod = sys.modules.get("server")
        reset = getattr(mod, "_reset_ttl_memo_caches", None)
        if reset:
            reset()
    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _restore_canonical_server_module():
    """Undo per-test server re-imports at teardown.

    Several tests pop "server" from sys.modules and re-import it to exercise
    import-time behavior. Extracted ccc_server/* modules resolve server names
    through sys.modules["server"] (the _core proxy), so leaving the fresh
    instance registered makes later test files patch a module object the
    proxy no longer looks at. Restore whatever was registered before the
    test so patches through the canonical module stay visible.
    """
    orig = sys.modules.get("server")
    yield
    if orig is not None and sys.modules.get("server") is not orig:
        sys.modules["server"] = orig
