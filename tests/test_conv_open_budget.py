"""Source invariants on the click -> conversation-open path in static/app.js.

Same shape as tests/test_dashboard_startup_budget.py: slice a function out of
the app source and assert the guard that keeps the open path cheap is present.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _function(name, next_marker, *, start_at=0):
    start = SOURCE.index(name, start_at)
    return SOURCE[start:SOURCE.index(next_marker, start)]


def test_files_pill_fetch_dedupes_in_flight_requests():
    # Every SSE tick invalidates the Files-pill cache and re-renders, so two
    # ticks in flight produced two identical /files requests at once
    # (service log 2026-09-12: pairs of 8-15 s Devin walks one second apart).
    source = _function("async function ffcFetch(", "function ffcInvalidate(")

    assert "_ffcInFlight" in source
    assert "_ffcInFlight.get(convId)" in source
    assert "_ffcInFlight.set(convId," in source
    assert "_ffcInFlight.delete(convId)" in source


def _index_of(source, needle):
    assert needle in source, needle
    return source.index(needle)


def test_switching_conversation_frees_connection_slots_before_the_tail_fetch():
    # Chrome allows six connections per host. A click used to leave the old
    # row's stream, spawn-stream and up to seven polls in flight, so the new
    # row's tail fetch queued behind them (672 ms stall measured 2026-09-12
    # with the harness in ~/dev/scratch/ccc-loadtime, curl saw 2-42 ms).
    source = _function("async function selectConversation(", "stopCodexLogPoller();")
    prefetch = _index_of(source, "_prefetchConversationTail(id)")
    assert _index_of(source, "abortConvScopedRequests()") < prefetch
    assert _index_of(source, "stopConvStream(paneId)") < prefetch
    assert _index_of(source, "spawnEventSource.close()") < prefetch


def test_conversation_scoped_requests_join_the_abort_scope():
    # Every request that only matters while one conversation is open carries
    # the scope signal, so the switch can free its slot immediately.
    sites = (
        "fetch('/api/session-status?' + params.toString(), { signal: convScopeSignal() })",
        "encodeURIComponent(sid) + '/token-sitter-checkpoint'",
        "encodeURIComponent(sid) + '/transcript-path'",
        "encodeURIComponent(sid) + '/spawn-info'",
        "fetch('/api/sessions/children?parent='",
        "fetch('/api/sessions/family?sid='",
        "encodeURIComponent(convId) + '/files'",
        "fetch('/api/session/spawn-timeline?session_id='",
    )
    for site in sites:
        at = _index_of(SOURCE, site)
        assert "convScopeSignal()" in SOURCE[at:at + 220], site
    confirm = _function("async function _confirmSessionNotLiveThenRenderOutcomeBanner(", "if (fresh === true) return;")
    assert "convScopeSignal()" in confirm


def test_aborted_scoped_fetches_do_not_poison_their_caches():
    files = _function("async function ffcFetch(", "function ffcInvalidate(")
    assert "AbortError" in files
    assert files.index("AbortError") < files.index("// Network / parse failure")
    sitter = _function("async function f2TokenSitterCheckpointExists(", "function f2PaintTokenSitterBadge(")
    assert "AbortError" in sitter
    assert sitter.index("AbortError") < sitter.index("_tokenSitterCheckpointCache.set(sid, exists)")
