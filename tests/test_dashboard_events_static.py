from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _dashboard_event_source():
    start = SOURCE.index("// ── Unified dashboard events")
    end = SOURCE.index("// ── End unified dashboard events", start)
    return SOURCE[start:end]


def test_dashboard_uses_one_replayable_event_stream():
    source = _dashboard_event_source()

    assert "function connectDashboardEvents()" in source
    assert "new EventSource('/api/events?' + params.toString())" in source
    assert "boot_id" in source
    assert "since" in source
    assert "ccc.dashboard.events.cursor" in source


def test_dashboard_event_consumer_rejects_stale_sequences_and_entity_versions():
    source = _dashboard_event_source()

    assert "event.seq <= _dashboardEventState.seq" in source
    assert "event.entity_version <= knownVersion" in source
    assert "_dashboardEventState.entityVersions" in source
    assert "event.topic === 'resync.required'" in source


def test_dashboard_event_consumer_patches_conversations_without_refetch():
    source = _dashboard_event_source()

    assert "function _applyDashboardConversationPatch(event)" in source
    assert "Object.assign({}, row, event.patch)" in source
    assert "pendingConversationPatches: new Map()" in source
    assert "_dashboardEventState.pendingConversationPatches.set" in source
    assert "function _applyPendingDashboardConversationPatches(rows)" in source
    assert "expiresAt" in source
    assert "pendingConversationPatches.clear()" in source


def test_session_patches_update_the_live_overlay_and_clear_ended_state():
    source = _dashboard_event_source()

    session_patch = source[source.index("function _applyDashboardSessionPatch(event)"):]
    session_patch = session_patch[:session_patch.index("function scheduleDashboardInvalidation")]
    assert "_rememberLiveOverlay(id, normalizedPatch)" in session_patch
    assert "_sessionLiveOverlay.delete(id)" in session_patch
    assert "is_live" in session_patch
    assert "scheduleDashboardInvalidation('archive')" not in source.split(
        "function _applyDashboardConversationPatch(event)", 1
    )[1].split("function ", 1)[0]


def test_dashboard_invalidations_are_coalesced_by_resource_and_id():
    source = _dashboard_event_source()

    assert "function scheduleDashboardInvalidation(resource, id)" in source
    assert "_dashboardEventState.invalidations.has(key)" in source
    assert "queueMicrotask(_flushDashboardInvalidations)" in source
    assert "_uxqItemsVersion += 1" in source
    assert "_archiveRefreshAfterInflight = true" in source
    assert "_uxqHealthAppliedSeq = ++_uxqHealthReqSeq" in source
    assert "_wtWorkersVersion += 1" in source
    assert "_fetchUxqHealth(false, true)" in source
    assert "_fetchWtWorkers()" in source


def test_legacy_queue_stream_is_only_a_unified_stream_fallback():
    start = SOURCE.index("(function _uxqEventStream()")
    end = SOURCE.index("// Set up the In Group Chat polling", start)
    source = SOURCE[start:end]

    assert "if (_dashboardEventStreamHealthy) return;" in source
    assert "setTimeout(connect, 2000)" in source
