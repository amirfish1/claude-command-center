import json

import server


def test_typed_spawn_marker_stamps_an_other_lane(monkeypatch, tmp_path):
    marker_dir = tmp_path / "spawn-markers"
    marker_dir.mkdir()
    session_id = "11111111-2222-3333-4444-555555555555"
    (marker_dir / f"{session_id}.json").write_text(json.dumps({
        "kind": "assistant",
        "lane": "other",
        "spawned_via": "ccc-ask",
    }))
    monkeypatch.setattr(server, "SPAWN_MARKERS_DIR", marker_dir)

    markers = server._load_spawn_markers()
    rows = [{"session_id": session_id}]
    server._apply_spawn_markers(rows, markers)

    assert markers == {
        session_id: {
            "kind": "assistant",
            "lane": "other",
            "spawned_via": "ccc-ask",
        },
    }
    assert rows == [{
        "session_id": session_id,
        "spawned_lane": "other",
        "spawned_kind": "assistant",
        "spawned_via": "ccc-ask",
    }]


def test_legacy_spawn_marker_remains_a_worker_marker(monkeypatch, tmp_path):
    marker_dir = tmp_path / "spawn-markers"
    marker_dir.mkdir()
    session_id = "22222222-2222-3333-4444-555555555555"
    (marker_dir / f"{session_id}.json").write_text(
        json.dumps({"spawned_via": "external-tool"}))
    monkeypatch.setattr(server, "SPAWN_MARKERS_DIR", marker_dir)

    rows = [{"session_id": session_id}]
    server._apply_spawn_markers(rows)

    assert rows == [{
        "session_id": session_id,
        "spawned_lane": "workers",
        "spawned_via": "external-tool",
    }]


def test_write_spawn_marker_records_typed_metadata(monkeypatch, tmp_path):
    marker_dir = tmp_path / "spawn-markers"
    monkeypatch.setattr(server, "SPAWN_MARKERS_DIR", marker_dir)
    session_id = "33333333-2222-3333-4444-555555555555"

    server._write_spawn_marker(
        session_id, lane="other", kind="assistant", spawned_via="ccc-ask")

    assert json.loads((marker_dir / f"{session_id}.json").read_text()) == {
        "kind": "assistant",
        "lane": "other",
        "spawned_via": "ccc-ask",
    }


def test_infer_session_spawned_via_rules():
    assert server._infer_session_spawned_via({}) == "terminal"
    assert server._infer_session_spawned_via({"spawned_via": ""}) == "terminal"
    assert server._infer_session_spawned_via({"spawned_via": "-"}) == "terminal"
    assert server._infer_session_spawned_via({"spawned_via": "not available"}) == "terminal"
    assert server._infer_session_spawned_via(None) == "terminal"

    assert server._infer_session_spawned_via({"spawned_via": "api"}) == "api"
    assert server._infer_session_spawned_via({"spawned_via": "ui"}) == "ui"
    assert server._infer_session_spawned_via({"spawned_via": "cli"}) == "cli"

    assert server._infer_session_spawned_via({"parent_session_id": "p-123"}) == "subagent"
    assert server._infer_session_spawned_via({"spawned_via": "-", "parent_session_id": "p-123"}) == "subagent"

    assert server._infer_session_spawned_via({"_worker_id": "w-1"}) == "watchtower"
    assert server._infer_session_spawned_via({"name": "lane-w-job"}) == "watchtower"
    assert server._infer_session_spawned_via({"name": "fix bug [watchtower]"}) == "watchtower"

    assert server._infer_session_spawned_via({"continued_from_session_id": "c-123"}) == "resumed"
    assert server._infer_session_spawned_via({"name": "resume-session-abc"}) == "resumed"

    assert server._infer_session_spawned_via({"session_id": "sid-1"}, sid="sid-1", spawn_registry_by_sid={"sid-1": {}}) == "ui"
    assert server._infer_session_spawned_via({"session_id": "sid-1"}, sid="sid-1", spawn_registry_by_sid={"sid-1": {"spawned_via": "cli"}}) == "cli"


def test_archive_overlay_acp_sessions_infers_spawned_via(monkeypatch, tmp_path):
    """Live ACP overlay must not NameError on a leftover `card` variable.

    /api/conversations/list merges in-memory ACP sessions after the cached
    snapshot. A copy-paste `card` (from the pending-spawn path) crashed every
    list poll once any Kimi/GLM/Grok session was attached, leaving the
    sidebar on "Loading archive…" and retry-storming the dashboard.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    sid = "acp-overlay-sid-1"
    monkeypatch.setattr(server, "_ACP_HARNESSES", {"kimi": {"label": "Kimi"}})
    monkeypatch.setattr(server, "_acp_harness_enabled", lambda harness: harness == "kimi")
    monkeypatch.setattr(server, "_ACP_SESSION_STATE", {
        "kimi": {
            sid: {
                "attached": True,
                "status": "active",
                "cwd": str(cwd),
                "updated_at": 1_700_000_000,
                "title": "ACP overlay session",
                "model": "kimi-k2",
            }
        }
    })
    monkeypatch.setattr(server, "_load_spawn_markers", lambda: {
        sid: {"spawned_via": "ui"},
    })
    monkeypatch.setattr(server, "_load_session_name_overrides", lambda: {})
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lambda: (set(), set()))
    monkeypatch.setattr(server, "_load_pinned_conversations", lambda: [])
    monkeypatch.setattr(server, "_load_verified_conversations", lambda: [])
    monkeypatch.setattr(server, "_acp_transcript_path", lambda harness, session_id: tmp_path / "missing.jsonl")
    monkeypatch.setattr(server, "_acp_transcript_first_prompt", lambda harness, session_id: "")
    monkeypatch.setattr(server, "_token_optimizer_quality_for_session", lambda session_id: {})

    rows = server._archive_overlay_acp_sessions([])
    assert len(rows) == 1
    assert rows[0]["session_id"] == sid
    assert rows[0]["spawned_via"] == "ui"
    assert rows[0]["engine"] == "kimi"


def test_archive_overlay_skips_devin_acp_sessions(monkeypatch, tmp_path):
    """Devin's ACP registry keys sessions by the raw slug while the durable
    archive row is ``devincli-<slug>`` — the two ids never match, so an ACP
    overlay row duplicated every attached devin session in the sidebar
    (CCC-1176). The devin harness opts out via ``archive_overlay: False``;
    the sessions.db overlay covers the spawn-to-snapshot gap instead.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    sid = "raw-devin-slug"
    monkeypatch.setattr(server, "_ACP_HARNESSES", {
        "devin": {"label": "Devin", "archive_overlay": False},
    })
    monkeypatch.setattr(server, "_acp_harness_enabled", lambda harness: True)
    monkeypatch.setattr(server, "_ACP_SESSION_STATE", {
        "devin": {
            sid: {
                "attached": True,
                "status": "active",
                "cwd": str(cwd),
                "updated_at": 1_700_000_000,
            }
        }
    })
    monkeypatch.setattr(server, "_load_spawn_markers", lambda: {})
    monkeypatch.setattr(server, "_load_session_name_overrides", lambda: {})
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lambda: (set(), set()))
    monkeypatch.setattr(server, "_load_pinned_conversations", lambda: [])

    assert server._archive_overlay_acp_sessions([]) == []



def test_spawn_markers_are_decoded_once_per_file_version(monkeypatch, tmp_path):
    # _load_spawn_markers read and json-decoded every SPAWN_MARKERS_DIR file
    # on every call. Measured 2026-09-12 with 715 markers: 283 to 427 ms per
    # call, and the lightweight /api/conversations/list path called it on
    # every poll from every tab (through the ACP overlay) before the body
    # cache replay. Markers are immutable once written, so decode each file
    # once per (mtime_ns, size) and rebuild the map from the memo.
    import os

    marker_dir = tmp_path / "spawn-markers"
    marker_dir.mkdir()
    monkeypatch.setattr(server, "SPAWN_MARKERS_DIR", marker_dir)
    (marker_dir / "aaaa.json").write_text(json.dumps({"spawned_via": "wt", "lane": "workers"}))
    (marker_dir / "bbbb.json").write_text(json.dumps({"lane": "other", "kind": "assistant"}))
    (marker_dir / "notes.txt").write_text("ignored")

    decoded = []
    real = server._decode_spawn_marker_file

    def counting(path):
        decoded.append(path.name)
        return real(path)

    monkeypatch.setattr(server, "_decode_spawn_marker_file", counting)

    first = server._load_spawn_markers()
    assert first == {
        "aaaa": {"lane": "workers", "spawned_via": "wt"},
        "bbbb": {"lane": "other", "kind": "assistant"},
    }
    assert sorted(decoded) == ["aaaa.json", "bbbb.json"]

    second = server._load_spawn_markers()
    assert second == first
    assert len(decoded) == 2, "unchanged files must not be decoded again"
    second["aaaa"]["lane"] = "mutated"
    assert server._load_spawn_markers()["aaaa"]["lane"] == "workers"

    target = marker_dir / "bbbb.json"
    target.write_text(json.dumps({"lane": "workers"}))
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
    third = server._load_spawn_markers()
    assert third["bbbb"] == {"lane": "workers"}
    assert len(decoded) == 3

    (marker_dir / "aaaa.json").unlink()
    assert "aaaa" not in server._load_spawn_markers()
    assert len(decoded) == 3
