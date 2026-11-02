"""Model catalog rebuilds are cheap under the boot burst (CCC-142)."""
import threading

import server


def test_one_rebuild_reads_the_policy_file_once(monkeypatch):
    stats = []
    orig_stat = type(server.MODEL_POLICY_FILE).stat
    policy = str(server.MODEL_POLICY_FILE)

    def counting_stat(self, *a, **k):
        if str(self) == policy:
            stats.append(1)
        return orig_stat(self, *a, **k)

    monkeypatch.setattr(type(server.MODEL_POLICY_FILE), "stat", counting_stat)
    server._build_engine_model_catalog(force_refresh=True)
    assert len(stats) == 1


def test_policy_snapshot_ends_with_the_build():
    server._build_engine_model_catalog(force_refresh=True)
    assert getattr(server._MODEL_POLICY_TLS, "blocked", None) is None


def test_concurrent_cold_callers_build_once(monkeypatch):
    calls = []
    impl = server._build_engine_model_catalog_impl

    def counting(force_refresh=False):
        if force_refresh or server._MODEL_CATALOG_CACHE["data"] is None:
            calls.append(1)
        return impl(force_refresh)

    monkeypatch.setattr(server, "_build_engine_model_catalog_impl", counting)
    monkeypatch.setitem(server._MODEL_CATALOG_CACHE, "data", None)
    gate = threading.Barrier(5)
    out = []

    def run():
        gate.wait()
        out.append(server._build_engine_model_catalog())

    ts = [threading.Thread(target=run) for _ in range(5)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert len(calls) == 1
    assert len(out) == 5 and all(o == out[0] for o in out)
