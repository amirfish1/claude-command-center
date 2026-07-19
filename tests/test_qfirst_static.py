"""Static regression coverage for the Q-FIRST queue-first mode (W88).

Q-FIRST is a client-only surface: it reuses the queue tab's two cached
fetches and the existing /api/ux-fixes/* write endpoints. These checks pin
the contract without a browser: the mode boot hook, the main-view host, the
wt-ledger-respecting endpoints, and the WatchTower status palette.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _app_js():
    return (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _qf_block(app_js):
    # Anchor on the main implementation section, not the small boot block
    # near the popout constants (both carry the W88 marker comment).
    start = app_js.index("let _qfNav = ")
    end = app_js.index("end Q-FIRST (W88)", start)
    return app_js[start:end]


def test_index_has_qfirst_main_view_host():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="qfirstView"' in html
    # It must live in the main column (a takeover surface, not a sidebar pane).
    main_start = html.index('<div class="main">')
    assert html.index('id="qfirstView"') > main_start


def test_mode_boots_from_url_param_and_persisted_toggle():
    app_js = _app_js()
    assert "_bootUrlParams.get('ccc_mode') === 'queues'" in app_js
    assert "localStorage.getItem('ccc-q-first')" in app_js
    # The URL param must not persist (same rule as the flow popout).
    assert "localStorage.setItem('ccc-q-first', on ? '1' : '0')" in app_js


def test_url_mode_is_used_by_settings_rerenders_and_toggle():
    """A URL-only queue-first session must not fall back to localStorage."""
    app_js = _app_js()
    settings_start = app_js.index("function refreshAppearanceChecks()")
    settings_end = app_js.index("function refreshSpawnEngineValue()", settings_start)
    appearance = app_js[settings_start:settings_end]
    assert "const qf = qFirstEnabled();" in appearance

    handler_start = app_js.index("const qfirstToggle = e.target.closest('[data-qfirst-toggle]');")
    handler_end = app_js.index("const ffToggle", handler_start)
    qfirst_handler = app_js[handler_start:handler_end]
    assert "const on = qFirstEnabled();" in qfirst_handler


def test_qfirst_writes_go_through_wt_backed_endpoints_only():
    block = _qf_block(_app_js())
    # Every mutation is one of the existing wt-backed endpoints.
    for endpoint in (
        "/api/ux-fixes/edit",
        "/api/ux-fixes/close",
        "/api/ux-fixes/reopen",
        "/api/ux-fixes/comment",
        "/api/ux-fixes/answer",
        "/api/ux-fixes/enqueue",
        "/api/ux-fixes/run-once",
    ):
        assert endpoint in block, endpoint
    # Reads reuse the two cached queue-tab fetches plus the single-item GET -
    # no new list endpoints, no per-ticket fan-out.
    assert "_fetchUxqItems" in block
    assert "_fetchUxqHealth" in block
    assert "/api/ux-fixes/item?ref=" in block


def test_ticket_row_uses_hero_first_sentence_and_mono_ref():
    block = _qf_block(_app_js())
    assert "splitFirstSentence" in block
    assert 'class="qf-ttitle-first"' in block
    assert 'class="qf-tref"' in block
    css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
    assert ".qf-ttitle-first" in css
    assert ".qf-tref" in css
    assert "--qf-mono" in css


def test_session_bridge_and_spawn_on_ticket():
    block = _qf_block(_app_js())
    # A ticket with a worker session opens that CCC conversation.
    assert "claimed_session_id" in block
    assert "selectConversation(sid)" in block
    # Without one, the ticket offers a one-off WT-tracked worker spawn.
    assert "spawn-worker" in block
    assert "/api/ux-fixes/run-once" in block


def test_status_palette_matches_watchtower_site():
    css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
    qf = css[css.index("Q-FIRST (W88)"):]
    # WT site palette: green = draining/live, red = stuck, amber accent.
    assert "--qf-green: #46c06a" in qf
    assert "--qf-red: #f0576d" in qf
    assert "--qf-amber: #f6b545" in qf
    assert ".qf-state-pill.is-draining" in qf
    assert ".qf-state-pill.is-stuck" in qf


def test_no_em_dashes_in_qfirst_copy():
    block = _qf_block(_app_js())
    # User-facing strings avoid em-dashes by convention. The code-comment
    # section separators are allowed; check quoted strings only.
    for m in re.finditer(r"'([^'\\]*(?:\\.[^'\\]*)*)'", block):
        assert "—" not in m.group(1), m.group(1)


def test_qfirst_board_overlays_conv_split_via_css():
    css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
    qf = css[css.index("Q-FIRST (W88)"):]
    assert "body.qf-active #convSplit" in qf
    assert "body.qf-active .qfirst-view { display: flex; }" in qf
