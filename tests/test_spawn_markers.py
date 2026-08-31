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
