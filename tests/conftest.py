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
