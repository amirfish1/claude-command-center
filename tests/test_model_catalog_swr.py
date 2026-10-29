"""CCC-143: /api/engines/models must never rebuild synchronously on TTL expiry.

The rebuild took 2-17s under load and held the GIL while conversation opens
waited. An expired catalog is served immediately and refreshed in one
background thread; an explicit invalidation (data=None) still rebuilds.
"""
import threading
import time

import server


def test_expired_catalog_served_stale_and_refreshed_once(monkeypatch):
    calls = []
    real = server._build_engine_model_catalog

    def fake_thread(target=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                calls.append(name)
        return T()

    monkeypatch.setattr(server.threading, "Thread", fake_thread)
    monkeypatch.setitem(server._MODEL_CATALOG_CACHE, "data", {"engines": {"x": ["stale"]}})
    monkeypatch.setitem(server._MODEL_CATALOG_CACHE, "ts", time.monotonic() - server._MODEL_CATALOG_TTL_SEC - 1)
    server._MODEL_CATALOG_REFRESHING.clear()
    try:
        assert real() == {"engines": {"x": ["stale"]}}
        assert real() == {"engines": {"x": ["stale"]}}
        assert calls == ["model-catalog-refresh"], "one background refresh, not one per request"
    finally:
        server._MODEL_CATALOG_REFRESHING.clear()


def test_known_engines_index_matches_curated_table():
    for engine, options in server._ENGINE_CURATED_MODELS.items():
        for option in options:
            assert engine in server._model_catalog_known_engines(option.get("id"))
    assert server._model_catalog_known_engines("definitely-not-a-model") == set()
