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
