"""CCC-103 (split view): the status rail's Original-ask/Activity nodes come
from the ACTIVE pane's sticky header, not the first one in the document
(always the left pane's), and other panes' headers are not purged."""

from tests.dashboard_source import dashboard_source
from pathlib import Path

APP = dashboard_source(Path(__file__).resolve().parent.parent.joinpath("static", "app.js"))


def _body():
    start = APP.index("  function _applyStatusRailLayout(")
    return APP[start:APP.index("\n  }\n", start)]


def test_sticky_source_is_active_pane():
    body = _body()
    assert "getConvViewForPane(activePaneId())" in body
    assert body.index("getConvViewForPane(activePaneId())") < body.index("const stickyOrig")


def test_orphan_cleanup_spares_other_panes():
    assert "_isOtherPaneNode" in _body()
