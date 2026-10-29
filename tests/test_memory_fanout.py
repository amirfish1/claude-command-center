# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Multi-machine S4: memory fan-out across paired peers and the merge.

No real peers: federation.PeerClient is swapped for a fake whose behaviour
is scripted per node (answer, raise a PeerError kind, or sleep past the
deadline). Everything else -- breaker, cache, RRF merge, dedupe, the
shipped honesty rule -- is the real code.
"""

import time

import pytest

import server  # noqa: F401 -- adopts ccc_server modules' names
import federation
from ccc_server import memory_api, memory_fanout as mf

PEER_A = {"node_id": "aaaaaaaa-0000-0000-0000-000000000001", "name": "hermes",
          "transport": {"type": "ssh", "host": "x@127.0.0.1"}, "secret": "sk-ant-test-XXXX"}
PEER_B = {"node_id": "bbbbbbbb-0000-0000-0000-000000000002", "name": "third",
          "transport": {"type": "ssh", "host": "y@127.0.0.1"}, "secret": "sk-ant-test-XXXX"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    mf.reset_state()
    yield
    mf.reset_state()


class _Script:
    """node_id -> callable(action, args) returning the inner result dict,
    or raising federation.PeerError, or sleeping."""

    def __init__(self):
        self.by_node = {}
        self.calls = []


@pytest.fixture
def script(monkeypatch):
    s = _Script()

    class FakeClient:
        def __init__(self, peer, self_node_id=None):
            self.peer = peer

        def request(self, method, path, body=None, timeout=30.0):
            node = self.peer["node_id"]
            s.calls.append((node, body["action"], body["args"], body["hops"]))
            behaviour = s.by_node[node]
            return {"ok": True, "result": behaviour(body["action"], body["args"])}

    monkeypatch.setattr(federation, "PeerClient", FakeClient)
    monkeypatch.setattr(federation, "load_peers", lambda: [PEER_A, PEER_B])
    return s


def _raise(kind, msg=""):
    def f(action, args):
        raise federation.PeerError(kind, msg or kind)
    return f


def _row(sid, node, rank_ts=0.0, **extra):
    return {"session_id": sid, "title": sid, "node_id": node, "node_name": node[:4],
            "ref": f"{node}:{sid}", "local": node == "me", "last_activity_ts": rank_ts, **extra}


# -- merge -------------------------------------------------------------------


def test_rrf_interleaves_nodes_and_breaks_ties_by_recency():
    local = [_row("s1", "me", 10), _row("s2", "me", 10)]
    peer = [_row("p1", "peer", 50), _row("p2", "peer", 5)]
    merged = mf.merge_recall([local, peer], limit=10)
    # Rank-1 rows tie on RRF score; the more recent one (p1) wins the tie.
    assert [r["session_id"] for r in merged] == ["p1", "s1", "s2", "p2"]


def test_dedupe_without_lease_keeps_later_copy_and_best_rank():
    local = [_row("x", "me", 100), _row("dup", "me", 100)]
    peer = [_row("dup", "peer", 200)]
    merged = mf.merge_recall([local, peer], limit=10)
    dup = [r for r in merged if r["session_id"] == "dup"]
    assert len(dup) == 1
    assert dup[0]["node_id"] == "peer"
    assert dup[0]["also_on"][0]["node_id"] == "me"
    # Best rank of its copies is the peer's rank 1, so it ties x for first.
    assert merged[0]["session_id"] == "dup"


def test_dedupe_prefers_lease_owner_over_recency():
    local = [_row("dup", "me", 999, lease_owner="peer", lease_handoff_at="2030-01-01T00:00:00+0000")]
    peer = [_row("dup", "peer", 10)]
    merged = mf.merge_recall([local, peer], limit=10)
    assert len(merged) == 1
    assert merged[0]["node_id"] == "peer"
    assert merged[0]["also_on"][0]["node_id"] == "me"


def test_kimi_prefixed_sid_dedupes_with_bare_uuid():
    local = [_row("session_abc", "me", 1)]
    peer = [_row("abc", "peer", 2)]
    assert len(mf.merge_recall([local, peer], limit=10)) == 1


def test_fork_after_handoff_keeps_both_rows_labelled():
    handoff = "2026-01-01T00:00:00+0000"
    after = mf._iso_to_epoch(handoff) + 3600
    local = [_row("dup", "me", after + 10, lease_owner="peer", lease_handoff_at=handoff)]
    peer = [_row("dup", "peer", after + 5)]
    merged = mf.merge_recall([local, peer], limit=10)
    assert len(merged) == 2
    assert all(r["forked"] for r in merged)
    owner = next(r for r in merged if r["node_id"] == "peer")
    other = next(r for r in merged if r["node_id"] == "me")
    assert "fork_of" not in owner
    assert other["fork_of"] == owner["ref"]


# -- fan_out -----------------------------------------------------------------


def test_fan_out_is_one_hop_and_reports_every_peer(script):
    script.by_node[PEER_A["node_id"]] = lambda a, args: {"results": []}
    script.by_node[PEER_B["node_id"]] = _raise("peer_offline")
    entries = mf.fan_out("memory_recall", {"q": "x", "limit": 5})
    by = {e["name"]: e for e in entries}
    assert by["hermes"]["status"] == "ok"
    assert by["third"]["status"] == "peer_offline"
    assert all(c[3] == 1 for c in script.calls)  # hops=1: a peer can't re-forward


def test_deadline_miss_reports_timeout_but_late_answer_is_kept(script):
    script.by_node[PEER_A["node_id"]] = lambda a, args: (time.sleep(0.6), {"results": [{"session_id": "late"}]})[1]
    script.by_node[PEER_B["node_id"]] = lambda a, args: {"results": []}
    t0 = time.time()
    entries = mf.fan_out("memory_recall", {"q": "x"}, deadline_s=0.1)
    assert time.time() - t0 < 0.45
    assert {e["name"]: e["status"] for e in entries}["hermes"] == "timeout"
    # The same question again while the first is in flight: not stacked.
    calls_before = len(script.calls)
    entries = mf.fan_out("memory_recall", {"q": "x"}, deadline_s=0.05, peers=[PEER_A])
    assert entries[0]["detail"] == "previous request to this peer still running"
    assert len(script.calls) == calls_before
    time.sleep(0.8)
    # The late success closed the breaker and filled the cache: a cold
    # connect that overran once doesn't keep the peer benched.
    assert mf._breaker_open(PEER_A["node_id"], time.time()) is None
    script.by_node[PEER_A["node_id"]] = _raise("peer_offline")
    entries = mf.fan_out("memory_recall", {"q": "x"}, peers=[PEER_A])
    assert entries[0]["stale"] is True
    assert entries[0]["result"]["results"][0]["session_id"] == "late"


def test_transport_timeout_opens_breaker_then_skips(script):
    script.by_node[PEER_A["node_id"]] = _raise("timeout")
    script.by_node[PEER_B["node_id"]] = lambda a, args: {"results": []}
    entries = mf.fan_out("memory_recall", {"q": "x"})
    assert {e["name"]: e["status"] for e in entries}["hermes"] == "timeout"
    entries = mf.fan_out("memory_recall", {"q": "y"})
    assert {e["name"]: e["status"] for e in entries}["hermes"] == "skipped_backoff"


def test_local_and_peers_run_concurrently(script):
    script.by_node[PEER_A["node_id"]] = lambda a, args: (time.sleep(0.5), {"results": []})[1]
    script.by_node[PEER_B["node_id"]] = lambda a, args: (time.sleep(0.5), {"results": []})[1]

    def slow_local(q, n):
        time.sleep(0.5)
        return {"query": q, "results": [], "indexing": False}

    t0 = time.time()
    mf.recall_all("q", 5, slow_local)
    assert time.time() - t0 < 0.85  # max(local, peers), not their sum (1.0)


def test_failed_peer_falls_back_to_cached_answer_marked_stale(script):
    script.by_node[PEER_A["node_id"]] = lambda a, args: {"results": [{"session_id": "c"}]}
    script.by_node[PEER_B["node_id"]] = lambda a, args: {"results": []}
    mf.fan_out("memory_recall", {"q": "x"})
    script.by_node[PEER_A["node_id"]] = _raise("http_error", "HTTP 500: boom")
    entries = mf.fan_out("memory_recall", {"q": "x"})
    a = next(e for e in entries if e["name"] == "hermes")
    assert a["status"] == "error"
    assert a["stale"] is True
    assert a["result"]["results"][0]["session_id"] == "c"


def test_old_peer_without_memory_actions_is_reported_not_dropped(script):
    script.by_node[PEER_A["node_id"]] = _raise("http_error", "HTTP 400: unsupported_capability")
    script.by_node[PEER_B["node_id"]] = lambda a, args: {"results": []}
    entries = mf.fan_out("memory_recall", {"q": "x"})
    assert {e["name"]: e["status"] for e in entries}["hermes"] == "unsupported_capability"


# -- recall_all ----------------------------------------------------------------


def test_recall_all_tags_rows_and_lists_nodes(script):
    script.by_node[PEER_A["node_id"]] = lambda a, args: {
        "results": [{"session_id": "remote1", "title": "on vm"}]}
    script.by_node[PEER_B["node_id"]] = _raise("timeout")
    local = lambda q, n: {"query": q, "results": [{"session_id": "local1", "title": "here"}],
                          "indexing": False}
    out = mf.recall_all("q", 10, local)
    refs = {r["session_id"]: r for r in out["results"]}
    assert refs["local1"]["local"] is True
    assert refs["remote1"]["local"] is False
    assert refs["remote1"]["ref"] == f"{PEER_A['node_id']}:remote1"
    assert out["partial"] is True
    assert out["peers_unreachable"] == ["third"]
    assert [n["status"] for n in out["nodes"]] == ["ok", "ok", "timeout"]


# -- shipped_all -----------------------------------------------------------------


def _not_found(topic):
    return {"shipped": False, "verdict": "NOT FOUND on reachable nodes (mac)", "evidence": []}


def test_shipped_never_says_not_found_cleanly_with_a_peer_down(script):
    script.by_node[PEER_A["node_id"]] = lambda a, args: _not_found(args["topic"])
    script.by_node[PEER_B["node_id"]] = _raise("peer_offline")
    out = mf.shipped_all("t", _not_found)
    assert out["verdict"].startswith("NOT FOUND on reachable nodes (")
    assert "third unreachable" in out["verdict"]
    assert "NOT SHIPPED" not in out["verdict"]
    assert out["partial"] is True


def test_peer_shipped_wins_and_is_attributed(script):
    script.by_node[PEER_A["node_id"]] = lambda a, args: {
        "shipped": True, "verdict": "SHIPPED", "confidence": 0.9,
        "evidence": [{"repo": "r", "commit": "1c5283f", "state": "on_default"}]}
    script.by_node[PEER_B["node_id"]] = lambda a, args: _not_found("t")
    out = mf.shipped_all("t", _not_found)
    assert out["verdict"] == "SHIPPED"
    assert out["verdict_node"] == "hermes"
    assert out["evidence"][0]["commit"] == "1c5283f"
    assert out["evidence"][0]["node_name"] == "hermes"


def test_committed_only_on_peer(script):
    script.by_node[PEER_A["node_id"]] = lambda a, args: {
        "shipped": True, "verdict": "COMMITTED ON hermes, NOT PUSHED",
        "evidence": [{"repo": "r", "commit": "abc1234", "state": "local_only"}]}
    script.by_node[PEER_B["node_id"]] = lambda a, args: _not_found("t")
    out = mf.shipped_all("t", _not_found)
    assert out["verdict"] == "COMMITTED ON hermes, NOT PUSHED"


def test_local_shipped_beats_peer_on_tie(script):
    shipped = lambda t: {"shipped": True, "verdict": "SHIPPED",
                         "evidence": [{"repo": "r", "commit": "aaa"}]}
    script.by_node[PEER_A["node_id"]] = lambda a, args: shipped("t")
    script.by_node[PEER_B["node_id"]] = lambda a, args: shipped("t")
    out = mf.shipped_all("t", shipped)
    assert out["verdict_node"] == mf._self_node()["name"]


# -- brief_all ---------------------------------------------------------------------


def test_brief_found_locally_never_fans_out(script):
    out = mf.brief_all("abcdef12", lambda q: {"found": True, "session_id": "abcdef12"})
    assert out["local"] is True
    assert script.calls == []


def test_brief_falls_back_to_peer(script):
    script.by_node[PEER_A["node_id"]] = lambda a, args: {"found": True, "session_id": args["q"]}
    script.by_node[PEER_B["node_id"]] = lambda a, args: {"found": False}
    out = mf.brief_all("abcdef12", lambda q: {"found": False, "session_id": None})
    assert out["found"] is True
    assert out["node_name"] == "hermes"
    assert out["local"] is False
    assert out["ref"] == f"{PEER_A['node_id']}:abcdef12"


def test_brief_global_ref_asks_only_that_peer(script):
    script.by_node[PEER_B["node_id"]] = lambda a, args: {"found": True, "session_id": args["q"]}
    out = mf.brief_all(f"{PEER_B['node_id']}:abcdef12",
                       lambda q: pytest.fail("local brief must not run for a peer ref"))
    assert out["node_name"] == "third"
    assert {c[0] for c in script.calls} == {PEER_B["node_id"]}


# -- local scope stays local ---------------------------------------------------------


def test_scope_local_recall_never_touches_peers(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("scope=local must not build a peer client")

    monkeypatch.setattr(federation, "PeerClient", boom)
    monkeypatch.setattr(federation, "load_peers", boom)
    monkeypatch.setattr(memory_api._sg, "search_sessions", lambda q, limit=20: [])
    out = memory_api.recall("anything", limit=5)
    assert out["results"] == []
    assert "nodes" not in out


def test_server_scope_param_defaults_local():
    assert server._memory_scope({}) == "local"
    assert server._memory_scope({"scope": ["ALL"]}) == "all"
    assert server._memory_scope({"scope": ["bogus"]}) == "local"
