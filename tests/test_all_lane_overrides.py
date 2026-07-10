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


def test_session_lane_override_persists_row_aliases(monkeypatch, tmp_path):
    lane_file = tmp_path / "session-lane-overrides.json"
    monkeypatch.setattr(server, "SESSION_LANE_OVERRIDES_FILE", lane_file)

    lane, ids = server._set_session_lane_overrides(["sid-1", "row-1", "sid-1"], "coding")

    assert lane == "coding"
    assert ids == ["sid-1", "row-1"]
    assert server._load_session_lane_overrides() == {"sid-1": "coding", "row-1": "coding"}

    lane, ids = server._set_session_lane_overrides(["sid-1", "row-1"], "")

    assert lane == ""
    assert ids == ["sid-1", "row-1"]
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
    assert "/api/conversations/all-lane" in app_js
    assert 'all_lane_override' in app_js
    assert 'path == "/api/conversations/all-lane"' in server_py
    assert 're.match(r"^/api/conversations/[^/]+/all-lane$"' in server_py


def test_all_lane_drop_updates_render_state_immediately():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert app_js.count("all_lane_override: c.all_lane_override || ''") >= 2
    drop_handler = app_js[
        app_js.index("const assignAllLaneFromDrop = async")
        : app_js.index("$allHermesTabs.addEventListener('click'")
    ]
    assert "setLocalAllLaneOverride(sid, lane, row.id || '')" in drop_handler
    assert "_convListRenderSig = null;" in drop_handler


def test_all_lane_override_keeps_terminal_rows_in_lanes():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "const _trashConvs = _archivedConvs.filter(c => !c.pinned && !_allTabLaneOverride(c));" in app_js
    assert "const _pinnedArchived = _archivedConvs.filter(c => c.pinned || _allTabLaneOverride(c));" in app_js
    assert "expandAllLaneDestinationGroup(row);" in app_js
