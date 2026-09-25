"""User-initiated sidebar renders must bypass the pause gate.

`shouldPauseSidebarRender()` skips unforced renders while the New Session pane
is open (9ae27b1e) or a row is hovered (a62a67d4, CCC-1007). Both are normal
states at click/drop time, so an unforced render there saves the user's choice
but never repaints it: "by project" looked dead and drops (CCC-1135) looked
like they never stuck.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

UNFORCED = "renderArchiveList(document.getElementById('convSearch')?.value || '');"
FORCED = "renderArchiveList(document.getElementById('convSearch')?.value || '', { force: true });"


def _window(anchor, length=1500):
    at = APP_JS.index(anchor)
    return APP_JS[at:at + length]


def test_grouping_toggle_forces_render():
    block = _window("[data-role=\"archived-grouping-toggle\"]').forEach", 900)
    assert "ccc-archived-grouping" in block
    assert FORCED in block
    assert UNFORCED not in block


def test_object_drops_force_render():
    # Object header drops and object zone drops (one section), plus in-zone
    # row reorders, all repaint forced.
    start = APP_JS.index("Drop renders are forced (CCC-1135)")
    section = APP_JS[start:APP_JS.index("ccc-compact-rows", start)]
    assert "setDraftNodeParent(draggedNode, target)" in section
    assert section.count(FORCED) >= 10
    assert UNFORCED not in section
    reorder = _window("if (reorderObjectSessionRows(el, readConvIdsFromDrop(ev)", 300)
    assert FORCED in reorder and UNFORCED not in reorder
    for m in re.finditer(r"if \(reparentConversationIdsToObject\(target, convIds\)\) \{\s*\n\s*(.+)", APP_JS):
        assert m.group(1).strip() == FORCED


def test_child_rows_stay_slim_under_large_bright():
    # Large Bright pins title weight/colour with !important; child rows must
    # override both or they render bold (bcbb3cc9).
    rule = re.search(
        r"#convList \.conv-subagent-cluster \.conv-item\.is-current-child-row \.conv-title \{([^}]*)\}",
        APP_CSS,
    )
    assert rule, "child-row title rule missing"
    body = rule.group(1)
    assert "font-weight: 450 !important" in body
    assert "color: var(--text-secondary) !important" in body
