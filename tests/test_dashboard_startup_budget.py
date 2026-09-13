from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _function(name, next_marker, *, start_at=0):
    start = SOURCE.index(name, start_at)
    return SOURCE[start:SOURCE.index(next_marker, start)]


def test_background_reads_are_scheduled_through_four_slots():
    source = _function(
        "const BACKGROUND_API_READ_LIMIT", "window.__cccBackgroundApiFetch"
    )

    assert "const BACKGROUND_API_READ_LIMIT = 4" in source
    assert "const _backgroundApiBaseFetch = window.fetch.bind(window)" in source
    assert "const _backgroundApiReadQueue = []" in source
    assert "_backgroundApiReadActive < BACKGROUND_API_READ_LIMIT" in source
    assert "_backgroundApiReadQueue.push" in source
    assert "_drainBackgroundApiReads()" in source
    assert "_bufferBackgroundApiResponse" in source
    assert "await response.arrayBuffer()" in source


def test_user_fetches_are_not_globally_wrapped_or_queued():
    source = _function(
        "const BACKGROUND_API_READ_LIMIT", "window.__cccBackgroundApiFetch"
    )

    assert "window.fetch =" not in source
    assert "function backgroundApiFetch(" in source
    assert "_backgroundApiBaseFetch(input, options)" in source


def test_archive_base_refresh_does_not_eagerly_request_pr_enrichment():
    source = _function(
        "async function refreshArchiveData", "async function refreshGhIssuesSection"
    )

    assert "_hydrateArchiveSideData();" in source
    assert "_hydrateArchivePrData();" not in source
    assert "includePrs: true" not in source
    assert "await refreshLiveSessionsActivity()" not in source
    assert "refreshLiveSessionsActivity().then" in source


def test_simple_home_reuses_archive_rows_instead_of_requesting_full_sessions():
    source = _function(
        "async function _simpleHomeRefresh()", "// R4: stuck helper-queue alerts"
    )

    assert "'/api/sessions?all=1'" not in source
    assert "const sessionRows = convRows;" in source


def test_engine_availability_waits_for_first_sessions_and_idle_time():
    start = SOURCE.index("async function refreshEngineAvailability()")
    source = SOURCE[start:SOURCE.index("// Hide-descriptions toggle", start)]

    assert "Promise.all([_firstSessionsLoaded, _archiveFirstLoaded]).then" in source
    assert "requestIdleCallback" in source
    assert "spawnDefaultsReady.finally(refreshCodexAvailability);" not in source


def test_archive_network_request_starts_before_optional_boot_probes():
    bootstrap_at = SOURCE.index("const _archiveBootstrapFetchPromise")
    controller_at = SOURCE.index("const _archiveBootstrapController")
    telemetry_at = SOURCE.index("/api/telemetry/status")

    assert bootstrap_at < telemetry_at
    bootstrap = SOURCE[controller_at:SOURCE.index("// Pause periodic", bootstrap_at)]
    assert "backgroundApiFetch(" in bootstrap
    assert "_archiveBootstrapUrl, { signal: _archiveBootstrapController.signal }" in bootstrap
    assert "const _archiveBootstrapController = new AbortController()" in bootstrap
    assert "_archiveBootstrapController.abort()" in bootstrap
    assert "clearTimeout(_archiveBootstrapTimeout)" in bootstrap


def test_optional_startup_reads_wait_for_archive_then_use_the_four_slot_pool():
    start = SOURCE.index("function _startupCriticalApiRead")
    end = SOURCE.index("function abortBackgroundApiReadsForSpawn", start)
    source = SOURCE[start:end]

    assert "const _startupDeferredApiReads = []" in SOURCE
    assert "function _releaseStartupApiReads()" in source
    assert "_startupDeferredApiReads.push" in source
    assert "backgroundApiFetch(task.input, task.init)" in source
    assert "'/api/conversations/list'" in source
    assert "window.fetch = startupBudgetedFetch" in source
    assert "addEventListener('pointerdown', _releaseStartupApiReads" in source
    assert "addEventListener('keydown', _releaseStartupApiReads" in source


def test_background_pool_reads_honor_the_startup_deferral():
    # backgroundApiFetch binds the ORIGINAL window.fetch (before the
    # startupBudgetedFetch patch), so every status probe routed through the
    # four-slot pool used to bypass the "hold optional reads until rows
    # paint" gate entirely. Measured 2026-09-12: ~12 probes (healthcheck,
    # system/services, queue/list 2MB, attention 2.5s, ...) landed on the
    # server in the same 400ms as the archive bootstrap fetch, and the
    # archive request took 1.4-3s instead of ~0.15s. The pool entry point
    # must consult the same startup gate as window.fetch.
    start = SOURCE.index("function backgroundApiFetch(")
    end = SOURCE.index("window.__cccBackgroundApiFetch", start)
    source = SOURCE[start:end]

    assert "_startupApiReadsReleased" in source
    assert "_startupCriticalApiRead(input, init)" in source
    assert "_startupDeferredApiReads.push" in source
    # The startup gate state must be declared before the archive bootstrap
    # fetch runs, or the first backgroundApiFetch call hits the TDZ.
    assert SOURCE.index("let _startupApiReadsReleased") < SOURCE.index(
        "const _archiveBootstrapFetchPromise"
    )
    critical = _function("function _startupCriticalApiRead", "function startupBudgetedFetch")
    assert "'/api/archive/loading-status'" in critical


def test_archive_boot_no_longer_waits_for_selected_repo_sessions():
    start = SOURCE.index("(function wireArchiveMode()")
    end = SOURCE.index("// Periodic archive refresh.", start)
    source = SOURCE[start:end]

    assert "queueMicrotask(() => setArchiveMode())" in source
    assert "_firstSessionsLoaded.then" not in source


def test_load_archive_consumes_the_early_response():
    source = _function("async function loadArchiveAll", "// Cross-repo open GH issues")

    assert "url === _archiveBootstrapUrl" in source
    assert "await _archiveBootstrapFetchPromise" in source


def test_archive_readiness_settles_even_for_empty_or_failed_loads():
    source = _function("async function refreshArchiveData", "async function refreshGhIssuesSection")

    finally_block = source[source.index("} finally {"):]
    assert "_markArchiveFirstLoaded();" in finally_block
    marker_start = SOURCE.index("function _markArchiveFirstLoaded()")
    marker_end = SOURCE.index("window.__cccThroughputActivityRows", marker_start)
    assert "_releaseStartupApiReads();" in SOURCE[marker_start:marker_end]


def test_merging_spawn_defaults_does_not_refetch_spawn_defaults():
    # mergeSpawnDefaults() called refreshSpawnEngineValue(), which re-fetches
    # /api/spawn-defaults and calls mergeSpawnDefaults() again: an unbounded
    # fetch loop for the life of every open tab. Measured 2026-09-12 with a
    # single idle headless tab: 599 /api/spawn-defaults requests in 15s
    # (about 40/s), competing with the archive bootstrap for the GIL-bound
    # server. Merging may re-render the Settings summary, never re-fetch.
    merge = _function("function mergeSpawnDefaults(", "// setSpawnDefaultModel/setSpawnEngine only ever mutate")
    code = "\n".join(
        line for line in merge.splitlines() if not line.lstrip().startswith("//")
    )
    assert "refreshSpawnEngineValue(" not in code
    assert "loadSpawnDefaults(" not in code
    assert "renderSpawnDefaultsInline(" in code


def test_conv_tab_bar_height_is_measured_after_the_render_settles():
    # renderSidebar swapped #convList innerHTML, then immediately measured
    # the tab bar (getBoundingClientRect) to set --conv-tab-bar-h, forcing
    # a synchronous layout of the whole list. The very next statements
    # (_mountSharedQueuePanel, lane activity fills) mutate the DOM again, so
    # the browser laid the list out a second time before paint. CPU profile
    # 2026-09-12 at 684 rows: 236ms self time in that forced layout. Measure
    # in requestAnimationFrame (still before paint) and only touch the
    # custom property when the height actually changed.
    source = _function("function _updateConvTabBarHeightVar", "function updateLiveStripOffset")
    assert "requestAnimationFrame(" in source
    assert "getBoundingClientRect" in source
    assert "_convTabBarHeightLast" in source


def test_archive_scroll_restore_skips_layout_reads_for_a_pristine_capture():
    # _restoreArchiveListScroll ran restore() synchronously right after the
    # innerHTML swap. restore() reads scrollHeight/clientHeight (and sets
    # scrollTop), which forces a full layout of the freshly built list before
    # the browser would have done it anyway. CPU profile 2026-09-12 at 206
    # rows: 126ms self time in _archiveScrollTopWithinBounds, the largest
    # remaining JS chunk between list arrival and rows painted. On first boot
    # the capture is pristine (placeholder at scrollTop 0, no anchor row), so
    # there is nothing to restore and no reason to touch layout.
    restore = _function("function _restoreArchiveListScroll", "// Dedupe concurrent /api/conversations/all")
    assert "function _archiveScrollStateIsPristine(" in SOURCE
    assert restore.index("_archiveScrollStateIsPristine(state)") < restore.index("restore();")
    pristine = _function("function _archiveScrollStateIsPristine(", "function _restoreArchiveListScroll")
    assert "state.top" in pristine
    assert "anchorValue" in pristine


def test_archive_scroll_capture_skips_layout_reads_when_the_list_has_no_rows():
    # _captureArchiveListScroll read $list.scrollTop and getBoundingClientRect
    # before every render, including the first one, when #convList still holds
    # only the "Loading archive" placeholder. Those reads force a layout of the
    # whole dirty document right before the innerHTML swap that dirties it
    # again. CPU profile 2026-09-12 at 193 rows: 93 to 146 ms self time in the
    # capture, the largest JS chunk left between list arrival and rows painted.
    # With no anchor candidates in the list there is nothing to capture.
    capture = _function("function _captureArchiveListScroll", "function _archiveScrollTopWithinBounds")
    assert "function _archiveListHasScrollAnchors(" in SOURCE
    guard = capture.index("_archiveListHasScrollAnchors($list)")
    assert guard < capture.index("$list.scrollTop")
    assert guard < capture.index("getBoundingClientRect")
    helper = _function("function _archiveListHasScrollAnchors(", "function _captureArchiveListScroll")
    assert "querySelector(" in helper
    assert "getBoundingClientRect" not in helper
    assert "scrollTop" not in helper


def test_boot_skips_the_second_full_render_when_recovery_already_painted_the_rows():
    # On every boot the loading-status poll runs _recoverArchiveRenderIfStuck
    # while the first list fetch is still in flight; it piggybacks on that
    # fetch and renders the rows (force). setArchiveMode continues off the
    # same promise and rendered the same archiveData with the same query
    # again, in the same task, before the browser could paint: the scroll
    # capture forced a layout over the fresh rows, the HTML was rebuilt, and
    # the structural signature then discarded it. Render tracer 2026-09-12 at
    # 189 rows: 255 to 314 ms between the first write and first paint. When
    # the recovery render already covers this data and query, skip it.
    mode = _function("async function setArchiveMode()", "(function wireArchiveMode()")
    assert "_archiveRecoveryRenderCovers(" in mode
    assert mode.index("_archiveRecoveryRenderCovers(") < mode.rindex("renderArchiveList(")
    recovery = _function("function _recoverArchiveRenderIfStuck()", "function _scheduleArchiveStaleRetry()")
    forced = "renderArchiveList(_archiveQuery(), { force: true });"
    stamp = "_archiveRecoveryRendered = "
    assert recovery.count(forced) == 2
    assert recovery.count(stamp) == 2
    assert recovery.rindex(forced) < recovery.rindex(stamp)
    covers = _function("function _archiveRecoveryRenderCovers(", "function _recoverArchiveRenderIfStuck()")
    assert "rec.data === archiveData" in covers
    assert "_convListRenderSig" in covers
    assert "_lastArchiveRenderFilter" in covers
