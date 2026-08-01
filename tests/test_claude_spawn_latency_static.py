"""Browser contracts for the optimistic Claude spawn fast path."""

from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"
BENCHMARK_JS = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "verify-claude-spawn-latency.js"
)


def _function_source(name, window=9000):
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index(f"function {name}(")
    return source[start : start + window]


def test_spawn_click_origin_is_sent_to_the_server_timeline():
    body = _function_source("spawnFromInlineInput", 10000)

    assert body.index("const spawnAskedAt = Date.now();") < body.index(
        "insertPendingSpawnCard("
    )
    assert "timeline_t0_epoch_ms: spawnAskedAt" in body
    assert "priority: 'high'" in body
    assert "fast_path: engine === 'claude'" in body
    assert body.index("abortBackgroundApiReadsForSpawn()") < body.index(
        "await fetch(endpoint"
    )
    assert body.index("if (engine === 'claude') abortBackgroundApiReadsForSpawn();") < body.index(
        "await fetch(endpoint"
    )


def test_claude_placeholder_attaches_to_replayed_spawn_stream():
    body = _function_source("spawnFromInlineInput", 10000)

    assert "startSpawnStream(data.session_id" in body
    assert "pendingConversationId: placeholder.id" in body
    assert "replay: true" in body
    assert "knownLog: !!data.log" in body
    assert body.index("stopConvStream(activePaneId())") < body.index(
        "startSpawnStream(data.session_id"
    )


def test_claude_placeholder_defers_the_full_sidebar_rebuild():
    body = _function_source("insertPendingSpawnCard", 5000)

    assert "card.fast_path" in body
    assert body.index("selectConversation(id)") < body.index("renderSidebar(")
    assert "requestIdleCallback" not in body

    adopt = _function_source("adoptPendingSpawnPid", 2400)
    assert "if (!placeholder.fast_path)" in adopt
    matcher = _function_source("pendingSpawnMatchesRow", 1800)
    assert "placeholder.fast_path && !placeholder.expected_session_id" in matcher
    assert "placeholder.fast_path && placeholder.expected_session_id" in matcher
    assert "String(row.session_id) === String(placeholder.expected_session_id)" in matcher


def test_spawn_stream_accepts_selected_pending_placeholder_and_replays():
    body = _function_source("startSpawnStream", 4500)

    assert "opts" in body.split("{", 1)[0]
    assert "pendingConversationId" in body
    assert "?replay=1" in body
    assert "opts.knownLog" in body
    assert "(!info.alive && !opts.replay)" in body
    assert "pane.currentSession && pane.currentSession.id === sid" in body
    assert "claudeSpawnAwaitingFirstPaint.has(sid)" in body
    assert "handleSpawnEvents(data.events, streamPaneId, pane.conversationId, sid)" in body
    assert body.index("if (!streamStillSelected(latestPane)) return;") < body.index("stopSpawnStream();")
    rebind = _function_source("rebindCurrentSelectionToRealCard", 2400)
    assert "claudeSpawnAwaitingFirstPaint.has(sid)" in rebind
    assert "{ replay: true }" in rebind
    source = APP_JS.read_text(encoding="utf-8")
    assert source.count("claudeSpawnAwaitingFirstPaint.has(") >= 2


def test_placeholder_rebind_keeps_the_owned_first_response_stream():
    rebind = _function_source("rebindCurrentSelectionToRealCard", 2800)
    source = APP_JS.read_text(encoding="utf-8")

    assert "const keepFirstResponseStream" in rebind
    assert "_spawnLiveSid === sid" in rebind
    assert "if (!keepFirstResponseStream) stopSpawnStream();" in rebind
    assert "if (!keepFirstResponseStream)" in rebind
    assert source.count("const keepFirstResponseStream") >= 2


def test_first_partial_text_paint_is_instrumented():
    body = _function_source("handleSpawnEvents", 8000)
    marker = _function_source("markClaudeFirstVisibleOutput", 800)

    assert "streamSid" in body.split("{", 1)[0]
    assert "markClaudeFirstVisibleOutput(streamSid)" in body
    assert "first_visible_output" in marker
    assert "requestAnimationFrame" in marker
    assert "clearStreamingBubble({ lingerMs:" in body
    assert "releaseClaudeSpawnPaintGate(sessionId)" in marker


def test_tool_first_output_marks_first_visible_paint_and_terminal_releases_gate():
    body = _function_source("handleSpawnEvents", 9000)

    assert "visibleContentAdded = true" in body
    assert "if (visibleContentAdded && streamSid)" in body
    assert "releaseClaudeSpawnPaintGate(streamSid)" in body


def test_sidebar_refresh_is_frozen_from_placeholder_until_first_paint():
    source = APP_JS.read_text(encoding="utf-8")
    refresh = _function_source("refreshConversationList", 1200)
    events = _function_source("handleSpawnEvents", 9000)

    assert "function claudeFastSpawnAwaitingPaint()" in source
    assert "claudeFastSpawnAwaitingPaint()" in refresh
    assert "fast_path_visible" in source
    assert "markClaudeFirstVisibleOutput(streamSid)" in events
    assert "setTimeout(() => releaseClaudeSpawnPaintGate(data.session_id), 30000)" in source
    load = _function_source("loadConversationList", 1800)
    assert "if (claudeFastSpawnAwaitingPaint()) return;" in load
    assert refresh.count("claudeFastSpawnAwaitingPaint()") >= 2


def test_late_boot_restore_cannot_replace_new_session_user_intent():
    restore = _function_source("restoreLastViewOrConversation", 1000)

    assert "currentConversation === '__new__'" in restore
    assert "startsWith('spawning-')" in restore
    assert "claudeFastSpawnAwaitingPaint()" in restore

    select = _function_source("selectConversation", 18000)
    fetch_at = select.index("await fetchConversationEvents(paneId)")
    guard_at = select.index("pane.conversationId !== id", fetch_at)
    spawn_stream_at = select.index("startSpawnStream(sid, paneId)", fetch_at)
    assert fetch_at < guard_at < spawn_stream_at


def test_spawn_stats_are_engine_aware():
    source = APP_JS.read_text(encoding="utf-8")

    assert "CLAUDE_SPAWN_STAT_ROWS" in source
    assert "claude_first_text_delta" in source
    assert "client_first_visible_output" in source
    assert "data.timeline.engine === 'claude'" in source


def test_critical_spawn_can_preempt_background_api_reads():
    source = APP_JS.read_text(encoding="utf-8")

    assert "function abortBackgroundApiReadsForSpawn()" in source
    assert "function backgroundApiFetch(" in source
    assert "new AbortController()" in source
    assert "window.fetch = function trackedApiFetch" not in source
    assert "backgroundApiFetch(" in _function_source("loadArchiveAll", 2600)
    assert "backgroundApiFetch(repoUrl('/api/sessions'" in _function_source(
        "loadConversationList", 1200,
    )
    pause = _function_source("shouldPausePeriodicUiWork", 900)
    assert "activeConversation === '__new__'" in pause
    assert "startsWith('spawning-')" in pause
    assert "claudeFastSpawnAwaitingPaint()" in pause
    enter = _function_source("enterNewSessionMode", 1800)
    assert "abortBackgroundApiReadsForSpawn()" in enter


def test_claude_composer_prewarms_and_claims_the_reserved_process():
    source = APP_JS.read_text(encoding="utf-8")
    spawn = _function_source("spawnFromInlineInput", 12000)

    assert "/api/sessions/prewarm-claude" in source
    assert "if (currentConversation === '__new__') scheduleClaudePrewarm();" in source
    assert "const prewarm = await claudePrewarmPromise;" in spawn
    assert "spawnBody.prewarm_id = prewarm.prewarm_id" in spawn
    assert "name" in _function_source("claudePrewarmSpec", 1000)
    assert "_claudePrewarmPromise = null" in _function_source("requestClaudePrewarm", 1800)


def test_latency_benchmark_reports_distribution_and_always_cleans_up():
    source = BENCHMARK_JS.read_text(encoding="utf-8")

    assert "p50Ms:" in source
    assert "p75Ms:" in source
    assert "finally {" in source
    assert "if (spawnedSessionId)" in source
    assert "/trash" in source
    assert "every((row) => row.prewarmed)" in source
    assert "localStorage.setItem('ccc-tour-done', '1')" in source
