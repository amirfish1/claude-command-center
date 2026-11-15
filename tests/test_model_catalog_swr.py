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


def test_cold_http_catalog_returns_snapshot_without_joining_discovery(monkeypatch, tmp_path):
    import json
    snapshot = tmp_path / 'models.json'
    payload = {'catalog': {'example': {'models': [{'id': 'saved-model'}]}},
               'engines': {'example': ['saved-model']}}
    snapshot.write_text(json.dumps(payload))
    monkeypatch.setattr(server, '_MODEL_CATALOG_SNAPSHOT_FILE', snapshot)
    monkeypatch.setattr(server, '_MODEL_CATALOG_BOOT_CACHE', None)
    monkeypatch.setitem(server._MODEL_CATALOG_CACHE, 'data', None)
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def build(force_refresh=False):
        calls.append(force_refresh)
        started.set()
        release.wait(3)
        done.set()
        return payload
    monkeypatch.setattr(server, '_build_engine_model_catalog', build)
    server._MODEL_CATALOG_REFRESHING.clear()
    try:
        before = time.monotonic()
        for _ in range(50):
            assert server._read_engine_model_catalog() == dict(payload, pending=True)
        assert time.monotonic() - before < 1
        assert started.wait(1) and calls == [True]
    finally:
        release.set()
        assert done.wait(3)
        # Join the background persistence before the monkeypatch is undone.
        with server._MODEL_CATALOG_REFRESH_LOCK:
            pass
        deadline = time.monotonic() + 3
        while server._MODEL_CATALOG_REFRESHING.is_set() and time.monotonic() < deadline:
            time.sleep(.005)
        assert not server._MODEL_CATALOG_REFRESHING.is_set()


def test_first_install_catalog_is_pending_until_discovery(monkeypatch, tmp_path):
    monkeypatch.setattr(server, '_MODEL_CATALOG_SNAPSHOT_FILE', tmp_path / 'missing')
    monkeypatch.setattr(server, '_MODEL_CATALOG_BOOT_CACHE', None)
    monkeypatch.setitem(server._MODEL_CATALOG_CACHE, 'data', None)
    monkeypatch.setattr(server, '_schedule_engine_model_catalog_refresh', lambda: None)
    result = server._read_engine_model_catalog()
    assert result['pending'] and result['catalog'] == {} and result['engines'] == {}
