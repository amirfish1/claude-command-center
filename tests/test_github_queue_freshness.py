"""GitHub-backed queue freshness for the queue-events SSE.

A GitHub-backed queue keeps its tickets in GitHub Issues, so it never touches
the local ticket store the SSE stats — remote changes used to surface only via a
blind 60s beat against a stale-while-revalidate endpoint, which cost up to two
beats (~2 minutes) before a newly filed issue appeared without a manual reload.

These tests pin the replacement: a subscriber-gated poller that reads the
daemon-polled remote snapshot (a soft `list_items()` — the WatchTower daemon's
persisted cache owns live `gh` spending), warms the memo `/api/queue/list`
serves, and bumps a version counter the SSE folds into its change detection.

No network and no private data — `_q.list_items` is stubbed with synthetic rows
shaped like the GitHub backend's output (`github_repo` stamped on every row).
"""
import importlib
import sys
import time
from unittest import mock


def _load_server():
    sys.modules.pop("server", None)
    return importlib.import_module("server")


def _gh_row(ref, status="open", updated="2026-07-26T15:00:00Z"):
    return {
        "ref": ref, "project": "DEMO-GH", "status": status,
        "note": f"synthetic {ref}", "github_repo": "example/demo",
        "updated_at": updated,
    }


_LOCAL_ROW = {
    "ref": "LOCAL-1", "project": "LOCAL", "status": "open",
    "note": "file-backed ticket, no github_repo",
    "updated_at": "2026-07-26T15:00:00Z",
}


class _StubQueue:
    """Stands in for watchtower.queue: records whether fresh= was requested."""

    def __init__(self, rows):
        self.rows = rows
        self.fresh_calls = 0

    def list_items(self, status=None, lane=None, project=None, fresh=False, **kw):
        if fresh:
            self.fresh_calls += 1
        return list(self.rows)


class _LegacyQueue:
    """The stdlib fallback engine, which has no `fresh` keyword at all."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def list_items(self, status=None, lane=None):
        self.calls += 1
        return list(self.rows)


def _install_stub(server, stub):
    from ccc_server import core as _core
    _core._q = stub
    server._q = stub
    return stub


def test_signature_ignores_local_rows_and_ordering():
    server = _load_server()
    qe = sys.modules["ccc_server.queue_events"]

    a = qe._gh_queue_signature([_gh_row("D-1"), _gh_row("D-2"), _LOCAL_ROW])
    b = qe._gh_queue_signature([_LOCAL_ROW, _gh_row("D-2"), _gh_row("D-1")])
    assert a == b, "row order must not read as a change"
    assert len(a) == 2, "file-backed rows must not enter the GitHub signature"


def test_first_poll_is_baseline_then_change_bumps_version():
    server = _load_server()
    qe = sys.modules["ccc_server.queue_events"]
    stub = _install_stub(server, _StubQueue([_gh_row("D-1")]))

    qe._gh_watch["sig"] = None
    qe._gh_watch["version"] = 0

    assert qe._gh_queue_poll_once() is False, "baseline poll must not fire an event"
    assert qe.gh_queue_version() == 0
    assert stub.fresh_calls == 0, (
        "soft read: the WT daemon's persisted snapshot owns remote freshness; "
        "a forced refresh here duplicated the daemon's GraphQL spend"
    )

    assert qe._gh_queue_poll_once() is False, "unchanged remote must not fire"
    assert qe.gh_queue_version() == 0

    # A new issue appears.
    stub.rows.append(_gh_row("D-2"))
    assert qe._gh_queue_poll_once() is True
    assert qe.gh_queue_version() == 1

    # A status change on an existing issue also counts.
    stub.rows[0] = _gh_row("D-1", status="in_progress")
    assert qe._gh_queue_poll_once() is True
    assert qe.gh_queue_version() == 2


def test_poll_warms_the_memo_the_board_reads():
    """The push is only useful if the refetch it triggers returns fresh rows."""
    server = _load_server()
    qe = sys.modules["ccc_server.queue_events"]
    _install_stub(server, _StubQueue([_gh_row("D-1"), _gh_row("D-2")]))

    qe._ux_fixes_list_cache.clear()
    qe._gh_watch["sig"] = None
    qe._gh_queue_poll_once()

    entry = qe._ux_fixes_list_cache.get(("", ""))
    assert entry is not None, "poller must populate the unfiltered list memo"
    assert {r["ref"] for r in entry["items"]} == {"D-1", "D-2"}
    assert time.time() - entry["ts"] < 5, "memo must be stamped fresh, not stale"

    # And the endpoint serves it straight from that memo, no rebuild.
    served = qe._ux_fixes_list_items_cached(None, None)
    assert {r["ref"] for r in served} == {"D-1", "D-2"}


def test_fallback_engine_without_fresh_kwarg_does_not_explode():
    server = _load_server()
    qe = sys.modules["ccc_server.queue_events"]
    legacy = _install_stub(server, _LegacyQueue([_LOCAL_ROW]))

    qe._gh_watch["sig"] = None
    qe._gh_watch["version"] = 0
    assert qe._gh_queue_poll_once() is False
    assert legacy.calls == 1, "must retry without the unsupported keyword"
    assert qe.gh_queue_version() == 0


def test_watcher_starts_on_subscribe_and_retires_when_idle():
    server = _load_server()
    qe = sys.modules["ccc_server.queue_events"]
    _install_stub(server, _StubQueue([_gh_row("D-1")]))

    assert qe._gh_watch["thread"] is None, "no subscribers, no poller"

    qe.gh_queue_watch_enter()
    try:
        assert qe._gh_watch["subscribers"] == 1
        thread = qe._gh_watch["thread"]
        assert thread is not None and thread.is_alive()

        qe.gh_queue_watch_enter()
        assert qe._gh_watch["subscribers"] == 2
        assert qe._gh_watch["thread"] is thread, "one poller serves every subscriber"
        qe.gh_queue_watch_exit()
    finally:
        qe.gh_queue_watch_exit()

    # Dropping the last subscriber wakes the loop so it retires promptly
    # instead of sleeping out a full interval on nobody's behalf.
    deadline = time.time() + 5
    while time.time() < deadline and qe._gh_watch["thread"] is not None:
        time.sleep(0.05)
    assert qe._gh_watch["subscribers"] == 0
    assert qe._gh_watch["thread"] is None, "poller must not outlive its subscribers"


def test_list_items_cached_fresh_bypasses_ttl_and_requests_fresh_kwarg():
    """The queue panel's manual Refresh button (CCC visibility fixes): fresh=True
    must skip the memo entirely and ask the engine for a real fetch, not the
    stale-while-revalidate background thread."""
    server = _load_server()
    qe = sys.modules["ccc_server.queue_events"]
    stub = _install_stub(server, _StubQueue([_gh_row("D-1")]))

    qe._ux_fixes_list_cache.clear()
    qe._ux_fixes_list_items_cached(None, None)
    assert stub.fresh_calls == 0, "normal reads must not spend fresh= quota"

    stub.rows.append(_gh_row("D-2"))
    items = qe._ux_fixes_list_items_cached(None, None, fresh=True)
    assert stub.fresh_calls == 1
    assert {r["ref"] for r in items} == {"D-1", "D-2"}
    entry = qe._ux_fixes_list_cache.get(("", ""))
    assert entry is not None and time.time() - entry["ts"] < 5, (
        "fresh=True must also rewrite the memo so subsequent polls see it"
    )


def test_list_items_cached_fresh_falls_back_without_fresh_kwarg():
    server = _load_server()
    qe = sys.modules["ccc_server.queue_events"]
    legacy = _install_stub(server, _LegacyQueue([_LOCAL_ROW]))

    qe._ux_fixes_list_cache.clear()
    items = qe._ux_fixes_list_items_cached(None, None, fresh=True)
    assert legacy.calls == 1, "must retry without the unsupported keyword"
    assert {r["ref"] for r in items} == {"LOCAL-1"}


def test_synced_at_reflects_the_serving_cache_entry():
    server = _load_server()
    qe = sys.modules["ccc_server.queue_events"]
    _install_stub(server, _StubQueue([_gh_row("D-1")]))

    qe._ux_fixes_list_cache.clear()
    assert qe._ux_fixes_list_synced_at(None, None) is None, "nothing fetched yet"

    qe._ux_fixes_list_items_cached(None, None)
    synced = qe._ux_fixes_list_synced_at(None, None)
    assert synced is not None and time.time() - synced < 5


def test_github_sync_status_reports_rate_limit_state():
    """Backs the queue panel's degraded-sync notice (CCC visibility fixes)."""
    server = _load_server()
    qe = sys.modules["ccc_server.queue_events"]

    with mock.patch.object(qe, "github_rate_limited", return_value={
        "rate_limited": True, "backoff_seconds": 42, "last_remaining": 0,
    }):
        status = qe._github_sync_status()
    assert status["rate_limited"] is True
    assert status["backoff_seconds"] == 42
    assert status["retry_at"] is not None and status["retry_at"] > time.time()

    with mock.patch.object(qe, "github_rate_limited", return_value={
        "rate_limited": False, "backoff_seconds": 0, "last_remaining": 4800,
    }):
        status = qe._github_sync_status()
    assert status["rate_limited"] is False
    assert status["retry_at"] is None


def test_compute_queues_health_carries_backend_and_github_repo_from_config():
    """A queue's GH badge must survive a cold item cache (e.g. a GraphQL
    quota outage): the config is the source of truth, not cached items."""
    server = _load_server()
    qe = sys.modules["ccc_server.queue_events"]
    config = {
        "BECKY-DESIGN": {
            "auto_drain": True, "backend": "github",
            "github_repo": "amirfish1/BYM-Finie", "repo_path": "/tmp/BYM",
        },
        "LOCAL": {"auto_drain": False, "repo_path": "/tmp/local"},
    }
    with mock.patch.object(server, "_wt_read_config", return_value=config):
        rows = {r["queue"]: r for r in qe.compute_queues_health(health=[], wt_workers=[], items=[])}
    assert rows["BECKY-DESIGN"]["backend"] == "github"
    assert rows["BECKY-DESIGN"]["github_repo"] == "amirfish1/BYM-Finie"
    assert rows["LOCAL"]["backend"] == ""
    assert rows["LOCAL"]["github_repo"] == ""


def test_sse_folds_remote_version_into_change_detection():
    """The stream must treat a remote bump as a change, labelled 'remote'."""
    server = _load_server()
    stream = server.CommandCenterHandler._stream_queue_events
    assert "GitHub-backed" in (stream.__doc__ or "")

    import inspect
    body = inspect.getsource(stream)
    assert "gh_queue_version()" in body, "version must join the stat signature"
    assert body.count("gh_queue_version()") >= 2, "baseline and per-tick reads"
    assert '"remote"' in body, "remote deltas need their own `what` label"
    assert "gh_queue_watch_enter()" in body
    assert "gh_queue_watch_exit()" in body
    assert "finally:" in body, "refcount must be released on every exit path"
