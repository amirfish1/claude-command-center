"""Guard the shared Active/All sidebar window.

CCC-165/168 exposed a dual-key trap: the window toggle the user sees on Active
could write one preference while the data feed read another. That made the
toggle dishonest because it did not control what the user saw.

The shared key may use a product-chosen fresh-install default, but saved values
must take precedence and the two views must continue to use the same key.

The former DUAL-KEY TRAP: the window toggle the user actually sees on the Active
   tab wrote `ccc-inprogress-window`, but the data feed (renderArchiveList)
   caps everything by a DIFFERENT key, `ccc-archive-window`. So clicking the
   visible toggle to "All" did nothing — the real (upstream) window stayed
   stuck — and the toggle dishonestly showed "All" while data was capped.

These are static source invariants (no DOM / browser harness needed — same
spirit as tests/test_perf_budget.py's call-count guards). If one fails, the
dual-control bug class is creeping back. Keep the window honest: one key.
"""
import os
import re
import inspect

import pytest
import server

APP_JS = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")


@pytest.fixture(scope="module")
def app_js():
    with open(APP_JS, "r", encoding="utf-8") as fh:
        return fh.read()


def test_archive_window_defaults_to_seven_days(app_js):
    """The sidebar window defaults to seven days on a fresh install."""
    # Isolate the _archiveWindow() body and assert its fallback return is '7d'.
    m = re.search(r"function _archiveWindow\(\)\s*\{(.*?)\n  \}", app_js, re.S)
    assert m, "could not locate _archiveWindow() — did it move/rename?"
    body = m.group(1)
    # The only literal return at the end of the function is the default.
    returns = re.findall(r"return\s+'(1d|7d|all)'", body)
    assert returns, "no window literal returned by _archiveWindow()"
    assert returns[-1] == "7d", (
        "_archiveWindow() default must be '7d' for a focused fresh install. "
        "Found default: %r" % returns[-1]
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


def test_all_tab_cross_repo_ready_to_merge_respects_window(app_js):
    """The All tab must not reintroduce old rows from full archiveData after
    renderArchiveList already applied the 1d/7d window."""
    m = re.search(
        r"for \(const r of archiveData\) \{(.*?)_crossRepoRtm = Array\.from",
        app_js,
        re.S,
    )
    assert m, "could not locate the cross-repo Ready-to-merge archiveData pass"
    assert "_archiveWindowAllowsRow(r, _ipWindowCutoff)" in m.group(1), (
        "The cross-repo Ready-to-merge rebuild reads full archiveData. It must "
        "re-check _archiveWindowAllowsRow(), or old PR rows leak into the All "
        "tab when the user selects 1d/7d."
    )


def test_cross_repo_ready_to_merge_preserves_unknown_local_pr_rows(app_js):
    """A transient unknown GitHub PR state must not make a local session vanish.

    The normal partition has already classified a session with a recorded PR
    into ``_readyToMergeConvs``.  The cross-repo enrichment only knows how to
    add rows whose state is confirmed OPEN; it must merge those additions,
    never clear the local bucket when its status lookup has not completed.
    """
    start = app_js.index("if (Array.isArray(archiveData) && archiveData.length) {")
    end = app_js.index("// Ready to merge section:", start)
    block = app_js[start:end]

    assert "_readyToMergeConvs.push(..._crossRepoRtm);" in block
    assert "_readyToMergeConvs.length = 0;" not in block


def test_all_tab_archived_group_chats_respect_window(app_js):
    """Archived group-chat trash rows are not part of archiveRows, so they need
    their own 1d/7d window filter before rendering in the All tab."""
    m = re.search(
        r"const _archivedGroupChatsForRender = _hideGroupChatsForSearch(.*?);\n    const _arcGrouping",
        app_js,
        re.S,
    )
    assert m, "could not locate _archivedGroupChatsForRender"
    assert "_archiveWindowAllowsRow(gc, _ipWindowCutoff)" in m.group(1), (
        "Archived group chats bypass archiveRows. They must be filtered with "
        "_archiveWindowAllowsRow() so the All tab's 1d/7d/All control is honest."
    )


def test_sidebar_uses_additive_list_endpoint_and_search_widens_history(app_js):
    source = inspect.getsource(server.CommandCenterHandler._do_GET)

    assert 'path == "/api/conversations/list"' in source
    assert 'path == "/api/conversations/all"' in source
    assert "_archive_list_source_rows_cached(" in source
    assert "force_refresh=not stale_ok" in source
    list_source = inspect.getsource(server._archive_list_source_rows_cached)
    assert "copy_rows=False" in list_source
    assert "force_refresh=force_refresh" in list_source
    assert "'/api/conversations/list'" in app_js
    assert "let archiveDataWindow = null;" in app_js
    assert "function _refreshArchiveWindow(value)" in app_js
    assert "refreshArchiveData({ staleOk: true, window: 'all' })" in app_js


def _src_between(app_js, start_needle, end_needle):
    start = app_js.index(start_needle)
    end = app_js.index(end_needle, start + 1)
    return app_js[start:end]


def test_failed_conversations_list_fetch_does_not_return_empty_array(app_js):
    """Abort/timeout/HTTP failure of /api/conversations/list must not look
    like a successful empty archive.

    Observed in service.out.log: AbortError → loadArchiveAll returned [] →
    refreshArchiveData merged 0 rows → sidebar flashed "No conversations in
    the last day" every few minutes. A failed poll has to return null so
    the last good snapshot stays on screen.
    """
    src = _src_between(app_js, "async function loadArchiveAll", "async function loadCrossRepoIssues")
    not_ok = re.search(r"if \(!r\.ok\) \{(?P<body>.*?)return (?P<ret>[^;]+);", src, re.S)
    assert not_ok, "could not locate loadArchiveAll() HTTP not-ok return"
    assert not_ok.group("ret").strip() == "null", (
        "HTTP not-ok must return null (keep previous sidebar), not %s"
        % not_ok.group("ret").strip()
    )
    caught = re.search(r"catch \(e\) \{(?P<body>.*?)return (?P<ret>[^;]+);", src, re.S)
    assert caught, "could not locate loadArchiveAll() catch return"
    assert caught.group("ret").strip() == "null", (
        "fetch abort/throw must return null (keep previous sidebar), not %s"
        % caught.group("ret").strip()
    )


def test_refresh_archive_data_keeps_previous_rows_on_failed_fetch(app_js):
    """refreshArchiveData must not assign archiveData from a failed/empty
    poll when a previous snapshot exists. That assignment is what paints
    the empty-window placeholder over a live sidebar.
    """
    src = _src_between(app_js, "async function refreshArchiveData", "async function refreshGhIssuesSection")
    assert "if (!Array.isArray(convs))" in src, (
        "refreshArchiveData must bail out when loadArchiveAll fails (null)"
    )
    assert "keeping previous" in src, (
        "refreshArchiveData must log that it kept the previous snapshot"
    )
    assert re.search(
        r"if \(!convs\.length && Array\.isArray\(archiveData\) && archiveData\.length\)",
        src,
    ), "refreshArchiveData must refuse to replace a populated list with []"
