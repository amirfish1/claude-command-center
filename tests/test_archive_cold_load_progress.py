"""The first-ever archive build (fresh install, no persisted cache) can take
minutes. It must feed /api/archive/loading-status so the loading UI can show
"Reading conversation transcripts (340 of 1200)" instead of a bare spinner."""

import server


_OPTS = dict(include_prs=False, resolve_pr_states=False,
             resolve_effective=False, resolve_worktree_dirty=False)
_KEY = server._archive_response_cache_key(**_OPTS)


def _reset_status():
    with server._ARCHIVE_LOAD_STATUS_LOCK:
        server._ARCHIVE_LOAD_STATUS.update({"active": False, "steps": {}, "order": []})


def test_cold_build_reports_transcript_progress(monkeypatch):
    _reset_status()
    monkeypatch.setattr(server, "_archive_response_cache_get", lambda _key: None)
    seen = []

    def fake_compute(key, cache_options, serve_generation=None, progress_step=None):
        assert progress_step is not None, "cold build must get a progress callback"
        progress_step("transcripts", state="running", count=50, total=120)
        seen.append(server._archive_load_snapshot())
        return [{"session_id": "a"}], False

    monkeypatch.setattr(server, "_archive_compute_rows", fake_compute)
    rows, from_cache, _ver = server._archive_serve_rows_versioned(_KEY, _OPTS)

    assert rows == [{"session_id": "a"}]
    assert from_cache is False
    mid = seen[0]
    assert mid["active"] is True
    step = next(s for s in mid["steps"] if s["key"] == "transcripts")
    assert (step["count"], step["total"]) == (50, 120)
    assert server._archive_load_snapshot()["active"] is False


def test_cold_build_failure_clears_active(monkeypatch):
    _reset_status()
    monkeypatch.setattr(server, "_archive_response_cache_get", lambda _key: None)

    def boom(*_a, **_kw):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(server, "_archive_compute_rows", boom)
    try:
        server._archive_serve_rows_versioned(_KEY, _OPTS)
    except RuntimeError:
        pass
    snap = server._archive_load_snapshot()
    assert snap["active"] is False
    assert snap["phase"] == "error"


def test_concurrent_cold_caller_does_not_reset_running_count():
    _reset_status()
    assert server._archive_load_begin_if_idle() is True
    server._archive_load_set_step("transcripts", state="running", count=300, total=900)
    assert server._archive_load_begin_if_idle() is False
    step = next(s for s in server._archive_load_snapshot()["steps"] if s["key"] == "transcripts")
    assert step["count"] == 300
    _reset_status()
