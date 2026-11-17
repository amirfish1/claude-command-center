"""CCC-1189 (split view): the new-session chrome follows the COMPOSING pane.

Opening New Session in the right pane used to leave the folder row, recent
folder chips and .is-new-session on p1's strip (the p1-singleton ids), so
half the New Session screen rendered in the left pane. Verified end-to-end
with puppeteer at fix time; this pins the wiring.
"""

from tests.dashboard_source import dashboard_source
from pathlib import Path

APP = dashboard_source(Path(__file__).resolve().parent.parent.joinpath("static", "app.js"))


def _body(name):
    start = APP.index("  function " + name + "(")
    return APP[start:APP.index("\n  }\n", start)]


def test_new_session_pane_is_found_by_conversation_not_focus():
    assert "p.conversationId === '__new__'" in _body("newSessionPaneId")


def test_chrome_mount_moves_singletons_and_owns_is_new_session():
    body = _body("mountNewSessionChrome")
    for needle in ("spawnCwdPicker", "spawnCwdQuickChips", "newSessionObjectContext",
                   "inlineWorktreeToggle", "is-new-session", "convModelPickerStrip"):
        assert needle in body, needle


def test_focus_change_and_enter_route_through_the_mount():
    assert "mountNewSessionChrome(paneId);" in _body("mountStatusRailForPaneId")
    assert "mountNewSessionChrome(paneId);" in _body("enterNewSessionMode")
