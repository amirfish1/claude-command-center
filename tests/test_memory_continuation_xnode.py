# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Multi-machine S6 remainder (MEMORY-16): continuation origins across nodes.

Two isolated node fixtures, each with its own ship_graph session_meta DB.
A node's brief is the real lineage.continuation_links() over its own DB;
the peer client is a fake that answers a routed memory_brief with the OTHER
node's forced-local brief. The resolution logic is the real brief_all.
"""

import sqlite3

import pytest

import server  # noqa: F401 -- adopts ccc_server modules' names
import federation
from ccc_server import fleet, lineage, memory_fanout as mf, ship_graph

HERE = "11111111-0000-0000-0000-00000000000a"
PEER = {"node_id": "22222222-0000-0000-0000-00000000000b", "name": "hermes-gcp",
        "transport": {"type": "ssh", "host": "x@127.0.0.1"}, "secret": "sk-ant-test-XXXX"}
THIRD = {"node_id": "33333333-0000-0000-0000-00000000000c", "name": "third",
         "transport": {"type": "ssh", "host": "y@127.0.0.1"}, "secret": "sk-ant-test-XXXX"}

X = "aaaaaaaa-1111-2222-3333-444444444444"  # origin, on HERE
Y = "bbbbbbbb-1111-2222-3333-444444444444"  # its successor, on PEER


def make_node(rows):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    ship_graph._init_db(conn)
    for i, (sid, origin) in enumerate(rows):
        conn.execute("INSERT INTO session_meta (sid, start_ts, continuation_origin) VALUES (?, ?, ?)",
                     (sid, 1000.0 + i, origin))
    return conn


def node_brief(conn, fuzzy=True):
    def brief(q):
        if not conn.execute("SELECT 1 FROM session_meta WHERE sid = ?", (q,)).fetchone():
            if not fuzzy:
                return {"query": q, "session_id": None, "found": False}
            # Like the real resolve_session: no exact hit falls back to a
            # recall search, which can name some unrelated session.
            row = conn.execute("SELECT sid FROM session_meta LIMIT 1").fetchone()
            if not row:
                return {"query": q, "session_id": None, "found": False}
            q = row[0]
        links = lineage.continuation_links(conn, q)
        return {"query": q, "session_id": q, "found": True, "title": f"title {q[:4]}", **links}
    return brief


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    mf.reset_state()
    w = {"briefs": {}, "calls": [], "down": set(), "peers": [PEER]}

    class FakeClient:
        def __init__(self, peer, self_node_id=None):
            self.peer = peer

        def request(self, method, path, body=None, timeout=30.0, pipe=False):
            node = self.peer["node_id"]
            w["calls"].append((node, body["action"], dict(body["args"])))
            if node in w["down"]:
                raise federation.PeerError("peer_offline", "ssh: connect timed out")
            if node not in w["briefs"]:
                raise federation.PeerError("http_error", "HTTP 400: unsupported_capability")
            return {"ok": True, "result": w["briefs"][node](body["args"]["q"])}

    monkeypatch.setattr(federation, "PeerClient", FakeClient)
    monkeypatch.setattr(federation, "load_peers", lambda: list(w["peers"]))
    monkeypatch.setattr(federation, "node_identity",
                        lambda: {"node_id": HERE, "display_name": "mac"})
    monkeypatch.setattr(federation, "node_id", lambda: HERE)
    yield w
    mf.reset_state()


def test_dangling_origin_resolves_on_peer_with_node_tag(world):
    here = make_node([(Y, X)])  # Y continued from X, X lives on the peer
    world["briefs"][PEER["node_id"]] = node_brief(make_node([(X, "")]))
    out = mf.brief_all(Y, node_brief(here))
    p = out["predecessor"]
    assert p["session_id"] == X and p["node_name"] == "hermes-gcp" and p["local"] is False
    assert p["ref"] == f"{PEER['node_id']}:{X}"
    assert "dangling" not in p
    assert [c[1:] for c in world["calls"]] == [("memory_brief", {"q": X})]
    assert out["node_names"][PEER["node_id"]] == "hermes-gcp"


def test_moved_session_shows_successor_on_new_owner(world):
    # X was handed off HERE -> PEER (lease), then continued there as Y.
    here = make_node([(X, "")])
    world["briefs"][PEER["node_id"]] = node_brief(make_node([(X, ""), (Y, X)]))
    federation.write_lease(X, PEER["node_id"], note="handoff")
    out = mf.brief_all(X, node_brief(here))
    assert [(s["session_id"], s["node_name"], s["local"]) for s in out["successors"]] == [
        (Y, "hermes-gcp", False)]
    assert out["successors"][0]["ref"] == f"{PEER['node_id']}:{Y}"


def test_peer_sourced_brief_resolves_origin_locally_without_network(world):
    # Asking HERE about Y: only PEER has Y, and Y's origin X is HERE.
    here = make_node([(X, "")])
    world["briefs"][PEER["node_id"]] = node_brief(make_node([(Y, X)]))
    out = mf.brief_all(Y, node_brief(here, fuzzy=False))
    assert out["local"] is False and out["session_id"] == Y
    p = out["predecessor"]
    assert p["session_id"] == X and p["local"] is True and p["node_name"] == "mac"
    # The peer that answered is never asked again about its own dangling edge.
    assert [c[1:] for c in world["calls"]] == [("memory_brief", {"q": Y})]


def test_same_node_lineage_never_fans_out(world):
    here = make_node([(X, ""), (Y, X)])
    out = mf.brief_all(Y, node_brief(here))
    assert out["predecessor"]["session_id"] == X and out["predecessor"]["local"] is True
    assert out["predecessor"]["ref"] == X  # bare ids stay local
    assert world["calls"] == []
    out = mf.brief_all(X, node_brief(here))
    assert [s["session_id"] for s in out["successors"]] == [Y]
    assert world["calls"] == []


def test_missing_origin_stays_dangling_and_reports_status(world):
    here = make_node([(Y, X)])
    world["briefs"][PEER["node_id"]] = node_brief(make_node([("cccccccc-0000-0000-0000-000000000000", "")]))
    out = mf.brief_all(Y, node_brief(here))
    # The peer's recall-search fallback named another session: not a match.
    assert out["predecessor"] == {"session_id": X, "ref": X, "dangling": True}
    assert [n["status"] for n in out["continuation_status"]] == ["ok"]


def test_offline_and_unsupported_peers_keep_local_result_and_edge(world):
    world["peers"] = [PEER, THIRD]
    world["down"].add(PEER["node_id"])  # THIRD has no brief: unsupported
    here = make_node([(Y, X)])
    out = mf.brief_all(Y, node_brief(here))
    assert out["found"] and out["session_id"] == Y
    assert out["predecessor"]["dangling"] is True
    assert out["partial"] is True
    assert {n["name"]: n["status"] for n in out["continuation_status"]} == {
        "hermes-gcp": "peer_offline", "third": "unsupported_capability"}


def test_same_sid_on_two_peers_is_ambiguous_unless_leased(world):
    world["peers"] = [PEER, THIRD]
    here = make_node([(Y, X)])
    world["briefs"][PEER["node_id"]] = node_brief(make_node([(X, "")]))
    world["briefs"][THIRD["node_id"]] = node_brief(make_node([(X, "")]))
    out = mf.brief_all(Y, node_brief(here))
    p = out["predecessor"]
    assert p["ambiguous"] is True and sorted(p["candidates"]) == sorted(
        [f"{PEER['node_id']}:{X}", f"{THIRD['node_id']}:{X}"])
    mf.reset_state()
    federation.write_lease(X, THIRD["node_id"], note="handoff")
    p = mf.brief_all(Y, node_brief(here))["predecessor"]
    assert p["node_name"] == "third" and p["also_on"] == ["hermes-gcp"]


def test_cyclic_cross_node_origins_terminate_in_one_hop(world):
    here = make_node([(Y, X)])
    world["briefs"][PEER["node_id"]] = node_brief(make_node([(X, Y)]))
    out = mf.brief_all(Y, node_brief(here))
    assert out["predecessor"]["session_id"] == X
    assert len(world["calls"]) == 1


def test_injected_scope_all_on_peer_route_stays_local(monkeypatch):
    seen = []

    def self_api(method, path, query=None, body=None, timeout=None):
        seen.append((path, dict(query or {})))
        return {"found": False}

    monkeypatch.setattr(fleet, "_federation_self_api", self_api)
    monkeypatch.setattr(mf, "fan_out", lambda *a, **k: pytest.fail("peer route fanned out"))
    env = federation.make_route_envelope("memory_brief", {"q": X, "scope": "all"}, hops=1)
    fleet._federation_execute_route(env)
    assert seen and "scope" not in seen[0][1]
    assert seen[0][0] == f"/api/memory/brief/{X}"


def test_local_brief_links_mark_unindexed_origin_dangling():
    conn = make_node([(Y, X)])
    links = lineage.continuation_links(conn, Y)
    assert links == {"predecessor": {"session_id": X, "ref": X, "dangling": True}, "successors": []}
    conn = make_node([(X, ""), (Y, X)])
    assert lineage.continuation_links(conn, X)["successors"] == [{"session_id": Y, "ref": Y}]
    assert lineage.continuation_links(conn, Y)["predecessor"] == {"session_id": X, "ref": X}
