"""Shared pytest fixtures.

`server.py` memoises its ps-backed liveness scans (`_ttl_memo`, ~3s TTL). Those
caches are module-global, so a value cached by one test would leak into the
next and make direct-scan assertions flaky. Reset them before every test. We
reach for whatever `server` module is currently imported, since the suite
re-imports it (`_fresh_server`) to pick up per-test env.
"""
import os
import sys
import tempfile
import threading
import time

# Bound at import: tests monkeypatch time.monotonic/sleep, and fixture
# teardowns below run before monkeypatch undoes that.
_real_monotonic = time.monotonic
_real_sleep = time.sleep

import pytest

# Keep every test process away from the user's real conv-meta cache: server.py
# loads it at import and any _save_conv_meta_cache() from a test would write
# fixture entries (or a cleared cache) back over it. Set before any test
# module imports server; a real override in the environment wins.
os.environ.setdefault(
    "CCC_CONV_META_CACHE_FILE",
    os.path.join(tempfile.mkdtemp(prefix="ccc-test-meta-"), "conv_meta_cache.json"),
)


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
def _drain_recall_background_syncs(monkeypatch):
    """Let a test's background ship_graph / session_fts sync finish before
    monkeypatch restores the env. Depending on monkeypatch makes this teardown
    run first; otherwise the still-running thread re-reads the restored env,
    walks this host's real transcript corpus while holding the module sync
    lock, and later tests' non-blocking syncs silently skip.
    """
    yield
    deadline = _real_monotonic() + 15
    for name, flag in (("ccc_server.ship_graph", "_bg_sync_running"),
                       ("ccc_server.session_fts", "_bg_sync_running"),
                       ("ccc_server_session_fts", "_bg_sync_running")):
        mod = sys.modules.get(name)
        while mod is not None and getattr(mod, flag, False) and _real_monotonic() < deadline:
            _real_sleep(0.02)
    # Same for the server's own detached workers (CCC-274): an archive
    # response refresh rebuilds the list from transcripts for ~10s and a
    # spawn-marker revalidation rescans the marker dir. Outliving the test,
    # they read this host's real ~/.claude state and hit functions later
    # tests patch and count.
    # Keyed on the live threads, not the in-flight flags: a test that sets a
    # flag by hand must not stall every later teardown.
    for thread in threading.enumerate():
        target = getattr(thread, "_target", None)
        if getattr(target, "__qualname__", "") in _DRAINED_SERVER_WORKERS:
            thread.join(max(0.0, deadline - _real_monotonic()))


@pytest.fixture(autouse=True)
def _no_cold_archive_scan(monkeypatch):
    """Never start the census path's cold archive warm-up under test.

    _census_identity_map kicks _archive_refresh_response_cache_async whenever
    the archive response cache is empty, which every isolated test state is.
    The refresh parses this host's real transcript corpus (~10k files, well
    past any teardown drain) on a detached thread, into later tests that
    patch and count process-global functions (CCC-274). Tests that exercise
    the kick replace this function themselves.
    """
    srv = sys.modules.get("server")
    if srv is not None and hasattr(srv, "_archive_refresh_response_cache_async"):
        monkeypatch.setattr(
            srv, "_archive_refresh_response_cache_async", lambda key, options: False,
        )
    yield


_DRAINED_SERVER_WORKERS = frozenset({
    "_archive_refresh_response_cache_async.<locals>.refresh",
    "_load_spawn_markers_locked.<locals>._revalidate",
})


@pytest.fixture(autouse=True)
def _reset_inject_dedupe_window():
    """Clear the inject duplicate-suppression window between tests.

    The window is process-global by design (see _inject_dedupe_recent): in
    production every inject funnels through one server process. Under pytest
    that makes two tests injecting the same text into the same session id
    order-dependent — the second one gets legitimately suppressed.
    """
    def _reset():
        mod = sys.modules.get("server")
        recent = getattr(mod, "_inject_dedupe_recent", None)
        if isinstance(recent, dict):
            recent.clear()
        inflight = getattr(mod, "_inject_dedupe_inflight", None)
        if isinstance(inflight, dict):
            inflight.clear()
    _reset()
    yield
    _reset()


def _restore_server(orig):
    """Re-register `orig` as "server" and re-sync the extracted modules to it.

    A fresh `import server` reloads every ccc_server.* module in place
    (see server._adopt_ccc_module), so restoring only sys.modules leaves
    `orig` holding stale copies of module state (queues, classes, caches)
    that the reloaded modules no longer share. Reload the modules again with
    `orig` registered and re-adopt their names onto it, so both sides see
    one consistent set of objects.
    """
    import importlib
    import ccc_server
    sys.modules["server"] = orig
    seen = []
    for mod in list(ccc_server._registry.values()):
        if mod not in seen:
            seen.append(mod)
    seen.sort(key=lambda m: list(sys.modules).index(m.__name__)
              if m.__name__ in sys.modules else 0)
    for mod in seen:
        if sys.modules.get(mod.__name__) is not mod:
            continue
        mod = importlib.reload(mod)
        for k, v in vars(mod).items():
            if not k.startswith("__") and k != "_core":
                setattr(orig, k, v)


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
        _restore_server(orig)


@pytest.fixture(autouse=True, scope="module")
def _restore_canonical_server_module_after_file():
    """Class-scoped re-imports (setUpClass) outlive the per-test restore:
    the function-scoped fixture sees the fresh instance as the status quo
    and keeps it for the rest of the class — correct within the file, but
    it must not leak into the next file. Snapshot per test file, restore
    when the file is done."""
    orig = sys.modules.get("server")
    yield
    if orig is not None and sys.modules.get("server") is not orig:
        _restore_server(orig)


@pytest.fixture(autouse=True)
def isolated_attribution_state(tmp_path, monkeypatch):
    """Legacy suites cover rollback; attribution integration tests opt in."""
    monkeypatch.setenv("WATCHTOWER_STATE_DIR", str(tmp_path / "wt-attribution"))
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path / "ccc-attribution"))
    monkeypatch.setenv("CCC_ATTRIBUTION_EMIT", "0")
    monkeypatch.setenv("WT_UDS_RECONCILE_S", "0")


@pytest.fixture(autouse=True)
def isolated_sender_identity(monkeypatch):
    import sender_envelope
    for key in sender_envelope._INHERITED:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sender_envelope, '_nearest_engine', lambda: '')
