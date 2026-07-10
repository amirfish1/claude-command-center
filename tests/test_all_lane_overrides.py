import pathlib

import server


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_session_lane_override_roundtrip(monkeypatch, tmp_path):
    lane_file = tmp_path / "session-lane-overrides.json"
    monkeypatch.setattr(server, "SESSION_LANE_OVERRIDES_FILE", lane_file)

    assert server._load_session_lane_overrides() == {}
    assert server._set_session_lane_override("sid-1", "workers") == "workers"
    assert server._load_session_lane_overrides() == {"sid-1": "workers"}
    assert server._get_session_lane_override("sid-1") == "workers"

    assert server._set_session_lane_override("sid-1", "") == ""
    assert server._load_session_lane_overrides() == {}


def test_apply_session_lane_overrides_clears_cached_stale_fields(monkeypatch, tmp_path):
    lane_file = tmp_path / "session-lane-overrides.json"
    lane_file.write_text('{"sid-1": "messages", "sid-2": "invalid"}')
    monkeypatch.setattr(server, "SESSION_LANE_OVERRIDES_FILE", lane_file)

    rows = [
        {"session_id": "sid-1", "all_lane_override": "coding"},
        {"session_id": "sid-2", "all_lane_override": "workers"},
        {"id": "sid-3"},
    ]

    server._apply_session_lane_overrides(rows)

    assert rows[0]["all_lane_override"] == "messages"
    assert rows[1]["all_lane_override"] == ""
    assert rows[2]["all_lane_override"] == ""


def test_all_lane_drop_wiring_posts_to_persistent_endpoint():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    server_py = (ROOT / "server.py").read_text(encoding="utf-8")

    assert "assignAllLaneFromDrop" in app_js
    assert "data-all-hermes-tab" in app_js
    assert "/all-lane" in app_js
    assert 'all_lane_override' in app_js
    assert 're.match(r"^/api/conversations/[^/]+/all-lane$"' in server_py
