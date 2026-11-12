"""CCC-1202: rebindable report_to routes, resolved server-side at send time."""

import sqlite3

import pytest

import server
from ccc_server import continuation, ship_graph
from ccc_server import report_routes as rr


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "report-routes.json")
    monkeypatch.setattr(rr, "_default_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def manual_forward_store(tmp_path, monkeypatch):
    path = str(tmp_path / "manual-forwards.json")
    monkeypatch.setattr(continuation, "_manual_forward_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def lineage_conn(monkeypatch):
    """Fake SIDs must never touch the host's session index.

    forward_target() syncs the real session graph (git repos, WatchTower,
    every transcript) before answering. Against a developer's or release
    Mac's real ~/.claude that took longer than the request timeout, left the
    GET in flight, and then failed the restart-drain test that ran after it.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    ship_graph._init_db(conn)
    monkeypatch.setattr(continuation._sg, "_get_connection", lambda: conn)
    monkeypatch.setattr(continuation._sg, "_sync_all", lambda conn, force=False: None)
    # Likewise the "is the old sid mid-turn" check builds live activity for
    # every host session; the fake SIDs here are never working.
    monkeypatch.setattr(continuation._core, "_sessions_state_snapshot", lambda: {})
    yield conn
    conn.close()


def test_resolve_follows_rebind(store):
    rid = rr.create("dispatcher-old-sid", path=store)
    assert rr.is_route_id(rid)
    assert rr.resolve(rid, path=store) == "dispatcher-old-sid"
    moved = rr.rebind("dispatcher-new-sid", route_id=rid, path=store)
    assert moved == [rid]
    assert rr.resolve(rid, path=store) == "dispatcher-new-sid"
    assert rr.get(rid, path=store)["original_report_to"] == "dispatcher-old-sid"


def test_plain_sid_and_unknown_route_pass_through(store):
    rr.create("dispatcher-old-sid", path=store)
    assert rr.resolve("dispatcher-old-sid", path=store) == "dispatcher-old-sid"
    assert rr.resolve("rr_000000000000000000000000", path=store) == "rr_000000000000000000000000"


def test_bulk_rebind_moves_only_that_dispatchers_children(store):
    a1 = rr.create("dispatcher-a-sid", path=store)
    a2 = rr.create("dispatcher-a-sid", path=store)
    b1 = rr.create("dispatcher-b-sid", path=store)
    moved = rr.rebind("dispatcher-c-sid", from_report_to="dispatcher-a-sid", path=store)
    assert sorted(moved) == sorted([a1, a2])
    assert rr.resolve(b1, path=store) == "dispatcher-b-sid"
    assert rr.resolve(a2, path=store) == "dispatcher-c-sid"


def test_rebind_by_child_session(store):
    r1 = rr.create("dispatcher-a-sid", path=store)
    r2 = rr.create("dispatcher-a-sid", path=store)
    assert rr.set_child(r1, "child-one-sid", path=store)
    assert rr.rebind("dispatcher-z-sid", child_session_id="child-one-sid", path=store) == [r1]
    assert rr.resolve(r2, path=store) == "dispatcher-a-sid"
    assert [e["route_id"] for e in rr.list_routes(report_to="dispatcher-z-sid", path=store)] == [r1]


def test_rebind_requires_a_selector(store):
    rr.create("dispatcher-a-sid", path=store)
    with pytest.raises(ValueError):
        rr.rebind("dispatcher-z-sid", path=store)


def test_expired_routes_are_pruned_on_write(store):
    old = rr.create("dispatcher-a-sid", path=store, now=1000.0)
    rr.create("dispatcher-a-sid", path=store, now=1000.0 + rr.ROUTE_TTL_S + 1)
    assert rr.get(old, path=store) is None


@pytest.mark.parametrize("engine,uds", [("claude", True), ("claude", False), ("codex", True)])
def test_footer_addresses_route_but_keeps_dispatcher_line(monkeypatch, engine, uds):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds" if uds else "legacy")
    monkeypatch.setitem(server._CCC_PEER_STATE, "socket_path", "/tmp/cc-socks/1.sock")
    rid = "rr_abcdef0123456789abcdef01"
    out = server._wrap_prompt_with_return_address(
        "do the thing", "dispatcher-sid", engine=engine, route_id=rid,
    )
    assert f'"session_id": "{rid}"' in out
    assert '"session_id": "dispatcher-sid"' not in out
    # Spawn-hierarchy recovery still reads the dispatcher from the footer.
    assert server._parent_session_id_from_return_address_text(out) == "dispatcher-sid"


def test_footer_without_route_is_unchanged(monkeypatch):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "legacy")
    a = server._wrap_prompt_with_return_address("x", "dispatcher-sid", engine="codex")
    b = server._wrap_prompt_with_return_address("x", "dispatcher-sid", engine="codex", route_id=None)
    assert a == b
    assert '"session_id": "dispatcher-sid"' in a


def test_peer_report_envelope_resolves_route_at_send_time(monkeypatch):
    rid = rr.create("dispatcher-old-sid")
    rr.rebind("dispatcher-new-sid", route_id=rid)
    delivered = []
    monkeypatch.setattr(
        server, "_inject_text_into_session",
        lambda sid, text, **kw: delivered.append(sid),
    )
    server._ccc_peer_route_report(
        {"session_id": rid, "text": "STATUS: SUCCEEDED", "announced_from": "child"},
        "uds:/nowhere.sock",
    )
    assert delivered == ["dispatcher-new-sid"]


def _serve():
    import threading
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _request(httpd, path, payload=None):
    import json
    import urllib.request
    url = f"http://127.0.0.1:{httpd.server_port}{path}"
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_rebind_endpoint_and_inject_input_resolve_at_send_time(monkeypatch):
    rid = rr.create("dispatcher-old-sid")
    rr.set_child(rid, "child-http-sid")
    # Capture the sid inject-input resolves to, then stop the request there.
    seen = []

    def fake_alias(sid):
        seen.append(sid)
        return ""  # empty sid -> handler replies "missing session_id"

    monkeypatch.setattr(server, "_resolve_bridge_session_alias", fake_alias)
    httpd, thread = _serve()
    try:
        status, body = _request(httpd, "/api/report-routes/rebind", {"report_to": "dispatcher-new-sid"})
        assert status == 400 and not body["ok"]
        status, body = _request(httpd, "/api/report-routes/rebind", {
            "report_to": "dispatcher-new-sid", "from_report_to": "dispatcher-old-sid",
        })
        assert status == 200 and body["rebound"] == [rid]
        status, body = _request(httpd, "/api/report-routes?child_session_id=child-http-sid")
        assert [r["report_to"] for r in body["routes"]] == ["dispatcher-new-sid"]
        _request(httpd, "/api/inject-input", {"session_id": rid, "text": "STATUS: SUCCEEDED"})
        _request(httpd, "/api/inject-input", {"session_id": "plain-sid-123", "text": "hi"})
        assert seen == ["dispatcher-new-sid", "plain-sid-123"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_rebind_endpoint_records_manual_forward_for_forward_target_endpoint():
    """MEMORY-5: `from_report_to` names an old dispatcher wholesale, so the
    rebind endpoint should also record it as a manual forward -- the
    read-only forward-target endpoint (consulted by WatchTower) must then
    report it."""
    httpd, thread = _serve()
    try:
        status, body = _request(httpd, "/api/session/dispatcher-old-sid/forward-target")
        assert status == 200 and body["forwarded_to"] is None
        status, body = _request(httpd, "/api/report-routes/rebind", {
            "report_to": "dispatcher-new-sid", "from_report_to": "dispatcher-old-sid",
        })
        assert status == 200
        status, body = _request(httpd, "/api/session/dispatcher-old-sid/forward-target")
        assert status == 200 and body["forwarded_to"] == "dispatcher-new-sid"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
