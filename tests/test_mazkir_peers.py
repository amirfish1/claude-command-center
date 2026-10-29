# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Mazkir sees sessions from paired CCC machines (multi-machine S4)."""

from ccc_server import mazkir


def _fan_out(rows, status="ok"):
    def fake(action, args):
        assert action == "memory_recall"
        return [{"node_id": "n1", "name": "hermes", "status": status, "stale": False,
                 "result": {"results": rows} if rows is not None else None}]
    return fake


def test_peer_rows_become_labelled_candidates():
    cands, peers = mazkir.peer_prefetch("session waste", fan_out=_fan_out([
        {"session_id": "c3f811d6-aaaa", "title": "Token-waste analyzer", "repo": "agent-tools",
         "date": "2026-09-27", "snippet": "opening prompt",
         "match": {"turn": 4, "snippet": "the waste skill"}}]))
    assert cands == [{"session_id": "c3f811d6-aaaa", "title": "Token-waste analyzer",
                      "snippet": "the waste skill", "first_ts": "2026-09-27",
                      "last_ts": "2026-09-27", "cwd": "agent-tools",
                      "node": "hermes", "node_id": "n1"}]
    assert peers == [{"name": "hermes", "status": "ok", "stale": False, "rows": 1}]
    line = mazkir._fmt_candidate(1, cands[0])
    assert "machine=hermes" in line
    src = mazkir.source_row(cands[0])
    assert src["title"].startswith("[hermes] ")
    assert src["local"] is False and src["node"] == "hermes"


def test_peer_failure_never_breaks_prefetch():
    def boom(action, args):
        raise RuntimeError("transport bug")
    assert mazkir.peer_prefetch("q", fan_out=boom) == ([], [])
    cands, peers = mazkir.peer_prefetch("q", fan_out=_fan_out(None, status="timeout"))
    assert cands == [] and peers[0]["status"] == "timeout"


def test_trace_reports_each_peer():
    trace = mazkir.build_trace("src", 3, 3, 900, {}, [], [
        {"name": "hermes", "status": "ok", "rows": 2, "stale": False},
        {"name": "third", "status": "peer_offline", "rows": 0, "stale": False}])
    tools = {t["tool"]: t["detail"] for t in trace}
    assert tools["hermes · memory recall"] == "2 sessions"
    assert tools["third · memory recall"] == "peer offline"


def test_local_source_row_unchanged():
    src = mazkir.source_row({"session_id": "abc12345", "title": "t", "cwd": "/x/repo"})
    assert "node" not in src and "local" not in src
