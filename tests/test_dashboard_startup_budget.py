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
    assert "const _backgroundApiReadQueue = []" in source
    assert "_backgroundApiReadActive < BACKGROUND_API_READ_LIMIT" in source
    assert "_backgroundApiReadQueue.push" in source
    assert "_drainBackgroundApiReads()" in source


def test_user_fetches_are_not_globally_wrapped_or_queued():
    source = _function(
        "const BACKGROUND_API_READ_LIMIT", "window.__cccBackgroundApiFetch"
    )

    assert "window.fetch =" not in source
    assert "function backgroundApiFetch(" in source
    assert "fetch(input, options)" in source


def test_archive_base_refresh_does_not_eagerly_request_pr_enrichment():
    source = _function(
        "async function refreshArchiveData", "async function refreshGhIssuesSection"
    )

    assert "_hydrateArchiveSideData();" in source
    assert "_hydrateArchivePrData();" not in source
    assert "includePrs: true" not in source


def test_engine_availability_waits_for_first_sessions_and_idle_time():
    start = SOURCE.index("async function refreshEngineAvailability()")
    source = SOURCE[start:SOURCE.index("// Hide-descriptions toggle", start)]

    assert "_firstSessionsLoaded.then" in source
    assert "requestIdleCallback" in source
    assert "spawnDefaultsReady.finally(refreshCodexAvailability);" not in source
