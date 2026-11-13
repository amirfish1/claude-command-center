# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Multi-machine S6: `ccc_server.fleet._federation_record_cross_node_spawn_edge`.

After `/api/sessions/spawn` proxies a spawn to a peer via
`_federation_spawn_on_node`, this node's own session graph must record the
child as a global ref -- otherwise only the peer's side of the parent/child
edge exists (it already accepts the globalized parent ref
`_federation_spawn_on_node` sends), and `ccc brief`/family-tree queries
against the dispatching node's local parent never find the cross-node
child. No real HTTP/peer call here: `proxied` is the already-resolved
result `_federation_spawn_on_node` would have returned.
"""

import pytest

import server  # noqa: F401 -- adopts ccc_server modules' names before fleet needs them
from ccc_server import fleet


@pytest.fixture
def isolated_session_graph(tmp_path, monkeypatch):
    graph = server._SessionGraph(tmp_path / "session-graph.json")
    monkeypatch.setattr(server, "_session_graph", graph)
    return graph


def test_records_local_edge_after_successful_cross_node_spawn(isolated_session_graph):
    payload = {"parent_session_id": "dispatcher-1", "engine": "claude"}
    proxied = {"ok": True, "session_id": "child-on-peer-1",
               "ref": "bbbbbbbb-0000-0000-0000-000000000002:child-on-peer-1",
               "node_id": "bbbbbbbb-0000-0000-0000-000000000002"}

    fleet._federation_record_cross_node_spawn_edge(payload, proxied)

    assert isolated_session_graph.parent_of(
        "bbbbbbbb-0000-0000-0000-000000000002:child-on-peer-1"
    ) == "dispatcher-1"


def test_falls_back_to_report_to_when_no_explicit_parent(isolated_session_graph):
    payload = {"report_to": "dispatcher-2", "engine": "codex"}
    proxied = {"ok": True, "session_id": "child-2",
               "ref": "bbbbbbbb-0000-0000-0000-000000000002:child-2"}

    fleet._federation_record_cross_node_spawn_edge(payload, proxied)

    assert isolated_session_graph.parent_of(
        "bbbbbbbb-0000-0000-0000-000000000002:child-2"
    ) == "dispatcher-2"


def test_no_edge_when_spawn_failed(isolated_session_graph):
    payload = {"parent_session_id": "dispatcher-1"}
    proxied = {"ok": False, "error": "unpaired_peer"}

    fleet._federation_record_cross_node_spawn_edge(payload, proxied)

    assert isolated_session_graph.stats()["edges"] == 0


def test_no_edge_when_no_parent_linkage(isolated_session_graph):
    payload = {"engine": "claude"}
    proxied = {"ok": True, "session_id": "child-3",
               "ref": "bbbbbbbb-0000-0000-0000-000000000002:child-3"}

    fleet._federation_record_cross_node_spawn_edge(payload, proxied)

    assert isolated_session_graph.stats()["edges"] == 0
