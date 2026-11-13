"""Background-helper process: wire protocol, dashboard mirroring, fallback
(CCC-142). The child side runs in-thread against a fake server module; the
dashboard side is the real code in server.py / ship_graph."""

import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

import server
from ccc_server import bg_helper, session_fts, ship_graph


class _FakeServer:
    """Stands in for server.py inside the child."""

    _UX_FIXES_HEALTH_TTL = 15.0

    def __init__(self):
        self._ux_fixes_list_cache = {}
        self._ux_fixes_list_cache_lock = threading.Lock()
        self.version = 0
        self.entered = 0
        self.exited = 0
        self.ux_builds = 0

    def gh_queue_watch_enter(self):
        self.entered += 1

    def gh_queue_watch_exit(self):
        self.exited += 1

    def gh_queue_version(self):
        return self.version

    def set_board(self, items):
        with self._ux_fixes_list_cache_lock:
            self._ux_fixes_list_cache[("", "")] = {
                "items": items, "all_sig": tuple(i["ref"] for i in items), "ts": time.time(),
            }

    def _build_ux_fixes_health_payload_uncached(self):
        self.ux_builds += 1
        return {"ok": True, "projects": [], "count": self.ux_builds}


@pytest.fixture
def helper(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "h.sock"
    fake = _FakeServer()
    child = bg_helper._Child(fake)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(8)
    stop = threading.Event()

    def accept():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            child.handle(conn)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    saved = (dict(bg_helper._state), dict(bg_helper._stats))
    bg_helper._state.update(sock=str(path), proc=None, ready=True, errors=0)
    monkeypatch.delenv(bg_helper.CHILD_ENV, raising=False)
    server._gh_proxy.update(version=None, board_rev=-1, read_at=0.0)
    with server._ux_fixes_list_cache_lock:
        server._ux_fixes_list_cache.pop(("", ""), None)
    try:
        yield fake, child
    finally:
        stop.set()
        listener.close()
        bg_helper._state.clear()
        bg_helper._state.update(saved[0])
        bg_helper._stats.clear()
        bg_helper._stats.update(saved[1])
        server._gh_proxy.update(version=None, board_rev=-1, read_at=0.0)
        with server._ux_fixes_list_cache_lock:
            server._ux_fixes_list_cache.pop(("", ""), None)
        tmp.cleanup()


def test_gh_proxy_mirrors_board_and_version(helper):
    fake, child = helper
    fake.set_board([{"ref": "CCC-1"}, {"ref": "CCC-2"}])
    server._gh_proxy["read_at"] = time.time()  # a board is open
    v0 = server.gh_queue_version()

    assert server._gh_queue_proxy_tick() is True
    assert fake.entered == 1  # the child's real watch loop started
    with server._ux_fixes_list_cache_lock:
        board = server._ux_fixes_list_cache[("", "")]
    assert [i["ref"] for i in board["items"]] == ["CCC-1", "CCC-2"]
    assert server.gh_queue_version() == v0  # first sight is not a change

    # Unchanged board: the reply carries no items (cheap tick).
    reply = bg_helper.call("gh_tick", have_board_rev=server._gh_proxy["board_rev"])
    assert "items" not in reply

    # A remote change in the child bumps the dashboard's SSE version and
    # replaces the board memo.
    fake.version += 1
    fake.set_board([{"ref": "CCC-3"}])
    assert server._gh_queue_proxy_tick() is True
    assert server.gh_queue_version() == v0 + 1
    with server._ux_fixes_list_cache_lock:
        assert [i["ref"] for i in server._ux_fixes_list_cache[("", "")]["items"]] == ["CCC-3"]


def test_gh_watch_retires_in_child_when_ticks_stop(helper, monkeypatch):
    fake, child = helper
    server._gh_queue_proxy_tick()
    child.gh_last_tick -= bg_helper.GH_TICK_GRACE_S + 1
    monkeypatch.setattr(bg_helper.time, "sleep", lambda s: None)

    def exit_then_stop():
        fake.exited += 1
        raise StopIteration  # end the reaper loop after one retire

    fake.gh_queue_watch_exit = exit_then_stop
    with pytest.raises(StopIteration):
        child.gh_reaper()
    assert fake.exited == 1 and child.gh_entered is False


def test_ux_health_comes_from_child_and_respects_ttl(helper, monkeypatch):
    fake, child = helper
    monkeypatch.setattr(server, "_build_ux_fixes_health_payload_uncached",
                        lambda: pytest.fail("dashboard must not build in-process"))
    assert server._ux_fixes_health_from_helper()["count"] == 1
    assert server._ux_fixes_health_from_helper()["count"] == 1  # within TTL: reused
    assert server._ux_fixes_health_from_helper(force=True)["count"] == 2
    server._ux_fixes_health_refresh()
    assert server._ux_fixes_health_snapshot["data"]["count"] == 2


def test_ship_sync_delegates_to_child(helper, monkeypatch):
    fake, child = helper
    started = []
    monkeypatch.setattr(ship_graph.threading, "Thread",
                        lambda *a, **k: pytest.fail("dashboard must not sync in-process"))
    monkeypatch.setattr(bg_helper._Child, "ship_sync",
                        lambda self, req: started.append(1) or {"ok": True, "indexing": True})
    ship_graph._start_background_sync()
    assert started == [1]
    assert ship_graph.is_indexing() is True


def test_helper_down_falls_back_in_process(monkeypatch):
    saved = dict(bg_helper._state)
    try:
        bg_helper._state.update(ready=False, sock=None, started_at=0.0)
        assert server._gh_queue_proxy_tick() is False
        assert server._ux_fixes_health_from_helper() is None
        assert ship_graph._delegate_background_sync() is False
        # Booting helper: ship sync defers (the child syncs on start).
        bg_helper._state["started_at"] = time.monotonic()
        assert ship_graph._delegate_background_sync() is True
    finally:
        bg_helper._state.clear()
        bg_helper._state.update(saved)


def test_child_and_kill_switch_never_spawn(monkeypatch):
    monkeypatch.setenv("CCC_BG_HELPER", "0")
    assert bg_helper.start() is None
    monkeypatch.setenv("CCC_BG_HELPER", "1")
    monkeypatch.setenv(bg_helper.CHILD_ENV, "1")
    assert bg_helper.start() is None
    # Inside the child every path runs in-process.
    assert server._gh_queue_proxy_tick() is False
    assert ship_graph._delegate_background_sync() is False


def test_board_is_pulled_only_while_read(helper):
    fake, child = helper
    fake.set_board([{"ref": "CCC-1"}])
    server._gh_proxy["read_at"] = 0.0
    assert server._gh_queue_proxy_tick() is True
    with server._ux_fixes_list_cache_lock:
        assert ("", "") not in server._ux_fixes_list_cache  # nobody reads it
    # A board read marks it wanted; the next tick pulls it.
    server._ux_fixes_list_items_cached()
    assert server._gh_proxy["read_at"] > 0
    assert server._gh_queue_proxy_tick() is True
    with server._ux_fixes_list_cache_lock:
        assert [i["ref"] for i in server._ux_fixes_list_cache[("", "")]["items"]] == ["CCC-1"]


@pytest.mark.parametrize("module", ["bg_helper", "conv_reader"])
def test_child_exits_when_a_restarted_dashboard_replaces_its_socket(module, tmp_path, monkeypatch):
    # In-place dashboard restarts keep the pid, so getppid() never changes;
    # the replaced socket inode is what tells the old child to go.
    import importlib
    mod = importlib.import_module(f"ccc_server.{module}")
    path = tmp_path / "s.sock"
    path.write_text("old")
    inode = mod._socket_inode(path)
    path.unlink()
    path.write_text("new child")  # a fresh child published a new socket
    exits = []
    monkeypatch.setattr(mod.os, "_exit", lambda code: (exits.append(code), (_ for _ in ()).throw(SystemExit))[1])
    with pytest.raises(SystemExit):
        mod._watch_parent(os.getppid(), path, inode)
    assert exits == [0]


@pytest.fixture
def fts_state():
    saved = (session_fts._last_sync_ts, dict(session_fts._helper_indexing),
             session_fts._bg_sync_running)
    session_fts._last_sync_ts = 0.0
    session_fts._helper_indexing.update(value=False, ts=0.0)
    try:
        yield
    finally:
        session_fts._last_sync_ts = saved[0]
        session_fts._helper_indexing.clear()
        session_fts._helper_indexing.update(saved[1])
        session_fts._bg_sync_running = saved[2]


def test_fts_sync_delegates_to_child_once_per_ttl(helper, fts_state, monkeypatch):
    """MEMORY-20: with the helper up, the recall-index sync runs in the
    child, not on a dashboard thread, and kicks are throttled to the sync
    TTL (the dashboard never sees the child's own last-sync time)."""
    started = []
    monkeypatch.setattr(session_fts.threading, "Thread",
                        lambda *a, **k: pytest.fail("dashboard must not sync in-process"))
    monkeypatch.setattr(session_fts, "_refresh_vec_cache_async", lambda: None)
    monkeypatch.setattr(bg_helper._Child, "fts_sync",
                        lambda self, req: started.append(1) or {"ok": True, "indexing": True})
    session_fts._start_background_sync()
    assert started == [1]
    assert session_fts.is_indexing() is True
    assert time.time() - session_fts._last_sync_ts < 1  # next kick waits a TTL


def test_fts_sync_child_op_runs_real_sync_in_process(monkeypatch, fts_state):
    """Inside the child, the op runs session_fts's in-process path."""
    monkeypatch.setenv(bg_helper.CHILD_ENV, "1")
    ran = []

    class _T:
        def __init__(self, target=None, **k):
            self.target = target

        def start(self):
            ran.append(self.target)

    monkeypatch.setattr(session_fts.threading, "Thread", _T)
    session_fts._bg_sync_running = False
    reply = bg_helper._Child(object()).fts_sync({})
    assert reply["ok"] is True and len(ran) == 1


def test_fts_sync_falls_back_in_process_when_helper_unavailable(monkeypatch, fts_state):
    saved = dict(bg_helper._state)
    try:
        monkeypatch.delenv(bg_helper.CHILD_ENV, raising=False)
        bg_helper._state.update(ready=False, sock=None, started_at=0.0)
        assert session_fts._delegate_background_sync() is False  # down / CCC_BG_HELPER=0
        bg_helper._state["started_at"] = time.monotonic()
        assert session_fts._delegate_background_sync() is True  # booting: child syncs on start
        # Ready but the socket is gone: the call fails, so run in-process.
        bg_helper._state.update(ready=True, sock="/nonexistent/bg.sock", started_at=0.0)
        assert session_fts._delegate_background_sync() is False
        monkeypatch.setenv(bg_helper.CHILD_ENV, "1")
        assert session_fts._delegate_background_sync() is False  # the child itself
    finally:
        bg_helper._state.clear()
        bg_helper._state.update(saved)


# ---- live-status helper (CCC-319) ------------------------------------------


class _LiveFake(_FakeServer):
    def __init__(self):
        super().__init__()
        self.live_builds = 0
        self.live_rows = {"sid-file": {"is_live": True, "sidecar_status": "active"}}
        self.live_delay = 0.0

    def _build_live_sessions_activity_uncached(self, detail=None):
        self.live_builds += 1
        time.sleep(self.live_delay)
        if detail is not None:
            detail["candidates"] = len(self.live_rows)
            detail["phases"] = {"liveness": {"wall_ms": 1.0}}
        return dict(self.live_rows)


@pytest.fixture
def live_helper(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "live.sock"
    fake = _LiveFake()
    child = bg_helper._Child(fake)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(8)
    stop = threading.Event()

    def accept():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=child.handle, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    saved = dict(bg_helper._live_state)
    bg_helper._live_state.update(sock=str(path), proc=None, ready=True, errors=0)
    monkeypatch.delenv(bg_helper.CHILD_ENV, raising=False)
    monkeypatch.delenv("CCC_WORKER_PROCESS", raising=False)
    monkeypatch.setattr(server, "_live_state_only_in_dashboard", lambda: set())
    try:
        yield fake, child
    finally:
        stop.set()
        listener.close()
        bg_helper._live_state.clear()
        bg_helper._live_state.update(saved)
        tmp.cleanup()


def test_live_op_reports_when_it_was_read(live_helper):
    fake, _child = live_helper
    before = time.time()
    reply = bg_helper.call("live_activity", role="live", max_age_s=0)
    assert reply["data"] == fake.live_rows
    assert before <= reply["built_at"] <= time.time()
    assert reply["detail"]["phases"]["liveness"]["wall_ms"] == 1.0


def test_live_op_shares_a_fresh_build_but_rebuilds_a_stale_one(live_helper):
    fake, _child = live_helper
    bg_helper.call("live_activity", role="live", max_age_s=30)
    bg_helper.call("live_activity", role="live", max_age_s=30)
    assert fake.live_builds == 1
    time.sleep(0.05)
    bg_helper.call("live_activity", role="live", max_age_s=0.01)
    assert fake.live_builds == 2


def test_dashboard_reads_helper_and_keeps_its_read_time(live_helper):
    fake, _child = live_helper
    detail = {}
    data, built_at = server._live_activity_via_helper(detail)
    assert set(data) == {"sid-file"}
    assert abs(built_at - time.time()) < 5
    assert detail["helper"] is True and detail["helper_rtt_ms"] >= 0
    assert detail["helper_age_ms"] >= 0


def test_dashboard_overlays_state_only_it_can_see(live_helper, monkeypatch):
    fake, _child = live_helper
    fake.live_rows["sid-acp"] = {"is_live": True, "sidecar_status": None}
    monkeypatch.setattr(server, "_live_state_only_in_dashboard", lambda: {"sid-acp", "sid-gone"})
    monkeypatch.setattr(server, "_archive_session_is_live", lambda sid: sid == "sid-acp")
    monkeypatch.setattr(
        server, "_live_activity_entry_for_session",
        lambda sid: {"is_live": True, "sidecar_status": "active", "working_since": 5})
    data, _ = server._live_activity_via_helper({})
    assert data["sid-acp"]["sidecar_status"] == "active"
    assert data["sid-acp"]["working_since"] == 5
    assert "sid-gone" not in data
    assert "sid-file" in data


def test_dashboard_builds_in_process_when_helper_is_down(live_helper):
    bg_helper._live_state.update(ready=False)
    assert server._live_activity_via_helper({}) == (None, None)


def test_dashboard_builds_in_process_when_helper_stalls(live_helper, monkeypatch):
    fake, _child = live_helper
    fake.live_delay = 0.5
    monkeypatch.setattr(bg_helper, "LIVE_CALL_TIMEOUT_S", 0.1)
    assert server._live_activity_via_helper({}) == (None, None)
    assert bg_helper._live_state["errors"] == 1


def test_helper_off_switch_and_worker_and_child_skip_it(live_helper, monkeypatch):
    monkeypatch.setenv("CCC_LIVE_HELPER", "0")
    assert server._live_activity_via_helper({}) == (None, None)
    monkeypatch.delenv("CCC_LIVE_HELPER")
    monkeypatch.setenv("CCC_WORKER_PROCESS", "1")
    assert server._live_activity_via_helper({}) == (None, None)
    monkeypatch.delenv("CCC_WORKER_PROCESS")
    monkeypatch.setenv(bg_helper.CHILD_ENV, "1")
    assert server._live_activity_via_helper({}) == (None, None)


def test_status_age_follows_helper_read_not_reply(live_helper, monkeypatch):
    fake, _child = live_helper
    monkeypatch.setattr(server, "record_event", lambda *a, **k: None)
    snap = server._live_activity_snapshot
    saved = dict(snap)
    try:
        snap.update(ts=0.0, data=None)
        data = server.build_live_sessions_activity()
        assert "sid-file" in data
        assert 0 <= time.time() - snap["ts"] < 5
        assert snap["saved_at"] is None
    finally:
        snap.clear()
        snap.update(saved)
