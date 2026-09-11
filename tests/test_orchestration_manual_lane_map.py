"""Manual sidebar lineage must be reflected in the orchestration map."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_manual_subsession_children_are_collected_as_orchestration_lanes():
    """An explicit user link is valid lane-map lineage, not sidebar-only data."""
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    helper = app_js[
        app_js.index("  function orchAppendManualLanes(parentSid, lanes) {"):
        app_js.index("  function orchCollectLanes(sid) {")
    ]
    collect_lanes = app_js[
        app_js.index("  function orchCollectLanes(sid) {"):
        app_js.index("  // Two sources: /api/sessions/spawned", app_js.index("  function orchCollectLanes(sid) {"))
    ]

    assert "manualSubsessionParentId(id)" in helper
    assert "return orchAppendManualLanes(sid, treeLanes);" in collect_lanes
    assert "return orchAppendManualLanes(sid, lanes);" in collect_lanes
