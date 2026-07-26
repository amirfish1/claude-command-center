"""GitHub-backed queue freshness for the queue-events SSE.

A GitHub-backed queue keeps its tickets in GitHub Issues, so it never touches
the local ticket store the SSE stats — remote changes used to surface only via a
blind 60s beat against a stale-while-revalidate endpoint, which cost up to two
beats (~2 minutes) before a newly filed issue appeared without a manual reload.

These tests pin the replacement: a subscriber-gated poller that forces a remote
refresh, warms the memo `/api/queue/list` serves, and bumps a version counter
the SSE folds into its change detection.

No network and no private data — `_q.list_items` is stubbed with synthetic rows
shaped like the GitHub backend's output (`github_repo` stamped on every row).
"""
import importlib
import sys
import time


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
    assert stub.fresh_calls == 1, "the poller must force a remote refresh"

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
