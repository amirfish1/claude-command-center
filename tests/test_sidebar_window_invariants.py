"""Guard against the "silent window hides my projects" bug class.

CCC-165/168 were the same shape: a persisted sidebar *window* filter
(1d / 7d / All) that silently hid the bulk of the user's projects, with two
extra traps that made it hard to see and hard to fix:

  1. A TIGHT DEFAULT. `_archiveWindow()` defaulted to '1d', so a fresh load
     capped the whole sidebar to the last 24h — a user with 90+ repos saw only
     the ~5 touched today and read it as "where are all my projects?".

  2. A DUAL-KEY TRAP. The window toggle the user actually sees on the Active
     tab wrote `ccc-inprogress-window`, but the data feed (renderArchiveList)
     caps everything by a DIFFERENT key, `ccc-archive-window`. So clicking the
     visible toggle to "All" did nothing — the real (upstream) window stayed
     stuck — and the toggle dishonestly showed "All" while data was capped.

These are static source invariants (no DOM / browser harness needed — same
spirit as tests/test_perf_budget.py's call-count guards). If one fails, the
silent-hide bug class is creeping back. Don't relax the assertion — keep the
window honest: one key, defaulting to 'all'.
"""
import os
import re

import pytest

APP_JS = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")


@pytest.fixture(scope="module")
def app_js():
    with open(APP_JS, "r", encoding="utf-8") as fh:
        return fh.read()


def test_archive_window_defaults_to_all(app_js):
    """The sidebar window must default to 'all' — never a tight default that
    hides projects on first load (CCC-168)."""
    # Isolate the _archiveWindow() body and assert its fallback return is 'all'.
    m = re.search(r"function _archiveWindow\(\)\s*\{(.*?)\n  \}", app_js, re.S)
    assert m, "could not locate _archiveWindow() — did it move/rename?"
    body = m.group(1)
    # The only literal return at the end of the function is the default.
    returns = re.findall(r"return\s+'(1d|7d|all)'", body)
    assert returns, "no window literal returned by _archiveWindow()"
    assert returns[-1] == "all", (
        "_archiveWindow() default must be 'all' so no projects are hidden on a "
        "fresh load (CCC-168). Found default: %r" % returns[-1]
    )


def test_single_window_key_no_dual_control(app_js):
    """The visible In-Progress window toggle must drive the SAME key the data
    feed reads. The dead `ccc-inprogress-window` key must not be written
    anywhere — re-introducing it resurrects the dual-key trap (CCC-168)."""
    assert "localStorage.setItem('ccc-inprogress-window'" not in app_js and \
           'localStorage.setItem("ccc-inprogress-window"' not in app_js, (
        "Found a writer for the divergent 'ccc-inprogress-window' key. The "
        "Active-tab window toggle must write the unified ARCHIVE_WINDOW_KEY "
        "(ccc-archive-window) — the same key renderArchiveList caps data by — "
        "or the visible toggle silently controls nothing (CCC-168)."
    )


def test_inprogress_window_derives_from_archive_window(app_js):
    """`_ipWindow` (the In-Progress section's effective window) must derive from
    `_archiveWindow()` so the toggle the user sees reflects the real data
    window, not a separate stuck value (CCC-168)."""
    assert re.search(r"_ipWindow\s*=\s*_hasFolderChips\s*\?\s*_archiveWindow\(\)", app_js), (
        "_ipWindow must read _archiveWindow() (the single source of truth). If "
        "it reads a separate localStorage key again, the visible toggle and the "
        "actual data window can diverge — the CCC-168 dishonest-toggle bug."
    )
