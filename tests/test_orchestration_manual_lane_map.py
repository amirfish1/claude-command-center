"""Manual sidebar lineage must be reflected in the orchestration map."""

from tests.dashboard_source import dashboard_source
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_manual_subsession_children_are_collected_as_orchestration_lanes():
    """An explicit user link is valid lane-map lineage, not sidebar-only data."""
    app_js = dashboard_source(PROJECT_ROOT / "static" / "app.js")
    helper = app_js[
        app_js.index("  function orchAppendManualLanes(parentSid, lanes) {"):
        app_js.index("  function orchCollectLanes(sid) {")
    ]
    collect_lanes = app_js[
        app_js.index("  function orchCollectLanes(sid) {"):
        app_js.index("  // Two sources: /api/sessions/spawned", app_js.index("  function orchCollectLanes(sid) {"))
    ]

    assert "manualSubsessionParentId(id)" in helper
    assert "const treeLanes = (_orchFamilyTree && _orchFamilyTreeSid === sid)" in collect_lanes
    assert "return orchAppendManualLanes(sid, lanes);" in collect_lanes


def test_family_tree_lanes_are_supplemented_by_current_direct_children():
    """A partial family response must not hide a newer spawned child."""
    app_js = dashboard_source(PROJECT_ROOT / "static" / "app.js")
    collect_lanes = app_js[
        app_js.index("  function orchCollectLanes(sid) {"):
        app_js.index("  // Two sources: /api/sessions/spawned", app_js.index("  function orchCollectLanes(sid) {"))
    ]

    assert "const lanes = treeLanes.slice();" in collect_lanes
    assert "if (treeLanes.length) return orchAppendManualLanes(sid, treeLanes);" not in collect_lanes


def test_family_tree_is_walked_from_the_map_root_not_the_top_ancestor():
    """CCC-1212: /api/sessions/family returns the family from its topmost
    ancestor. When the map is rooted lower (hidden parent in another repo),
    the orchestrator's siblings must not be drawn as its lanes."""
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    app_js = dashboard_source(PROJECT_ROOT / "static" / "app.js")
    fn = app_js[
        app_js.index("  function orchFlattenTree(tree, rootSid) {"):
        app_js.index("  // Manual sidebar links are explicit user assertions")
    ]
    tree = {
        "session_id": "top", "children": [
            {"session_id": "sibling-a", "source": "ccc-spawn", "children": []},
            {"session_id": "orch", "source": "ccc-spawn", "children": [
                {"session_id": "lane-1", "source": "ccc-spawn", "children": [
                    {"session_id": "lane-1-kid", "source": "ccc-spawn"},
                ]},
            ]},
            {"session_id": "sibling-b", "source": "ccc-spawn"},
        ],
    }
    script = (
        "const conversationsData = []; const _orchSpawnedRegistry = [];\n"
        "const orchLaneMetaGet = () => ({}); const orchParseSpawnedAt = () => 0;\n"
        "const orchLane = (row, spawn) => ({ id: spawn.session_id, mtime: 0 });\n"
        + fn
        + "\nconst t = " + json.dumps(tree) + ";\n"
        "console.log(JSON.stringify({\n"
        "  orch: orchFlattenTree(t, 'orch').map(l => [l.id, l.depth]),\n"
        "  top: orchFlattenTree(t, 'top').map(l => l.id),\n"
        "  missing: orchFlattenTree(t, 'nope').length,\n"
        "}));\n"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout
    got = json.loads(out)
    assert got["orch"] == [["lane-1", 0], ["lane-1-kid", 1]]
    assert set(got["top"]) == {"sibling-a", "orch", "sibling-b", "lane-1", "lane-1-kid"}
    assert got["missing"] == 0
