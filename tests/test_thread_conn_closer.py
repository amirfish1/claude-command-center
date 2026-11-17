"""OPS-1739: per-thread sqlite connections must be closed when the thread exits."""
import os
import threading

from ccc_server import ship_graph


def _fd_count():
    return len(os.listdir("/dev/fd"))


def test_ship_graph_thread_connections_do_not_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_SHIP_GRAPH_DB", str(tmp_path / "sg.sqlite"))
    ship_graph._get_connection().close()  # warm the main thread's connection
    ship_graph._tls.conn = None
    before = _fd_count()
    for _ in range(15):
        t = threading.Thread(target=lambda: ship_graph._get_connection().execute("select 1").fetchall())
        t.start()
        t.join()
    assert _fd_count() - before <= 2
