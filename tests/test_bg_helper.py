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
from ccc_server import bg_helper, ship_graph


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
