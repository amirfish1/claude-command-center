"""Static performance contracts for CCC's idle dashboard state."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
THROUGHPUT = (ROOT / "static" / "throughput.html").read_text(encoding="utf-8")


def _css_rule(selector):
    match = re.search(
        r"^\s*" + re.escape(selector) + r"\s*\{(?P<body>[^}]+)\}",
        APP_CSS,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match, f"missing CSS rule for {selector}"
    return match.group("body")


def test_update_pill_attention_animation_is_finite_and_compositor_only():
    dot_rule = _css_rule(".upd-pill .upd-pill-dot")
    assert "infinite" not in dot_rule
    assert "upd-arrive" in dot_rule

    keyframes = re.search(
        r"@keyframes\s+upd-arrive\s*\{(?P<body>.*?)\n\s*\}",
        APP_CSS,
        flags=re.DOTALL,
    )
    assert keyframes, "missing bounded update attention keyframes"
    body = keyframes.group("body")
    assert "box-shadow" not in body
    assert "transform" in body
    assert "opacity" in body


def test_visible_working_indicators_are_static_across_poll_rerenders():
    selectors = (
        ".conv-item .conv-live-tool",
        ".conv-item .conv-live-tool.in-flight",
        ".conv-item .conv-needs-you",
        ".conv-item .live-dot",
        ".is-working .session-activity-dot",
        ".conv-signal.activity-working",
    )
    for selector in selectors:
        rule = _css_rule(selector)
        assert re.search(r"animation:\s*none", rule), (
            f"{selector} restarts compositor work whenever its row rerenders"
        )


def test_live_dot_sonar_pulses_only_on_the_selected_row():
    """Every live sidebar row used to run an infinite scale ping. WKWebView
    still CPU-paints those even when the keyframes are transform/opacity,
    and N live rows kept the GPU process pegged. Only the open row pulses."""
    default_after = _css_rule(".conv-live-dot::after")
    assert re.search(r"animation:\s*none", default_after), default_after
    selected_after = _css_rule(".conv-item.list-selected .conv-live-dot::after")
    assert "ccc-live-dot-pulse" in selected_after
    assert "infinite" in selected_after


def test_completion_glow_does_not_animate_box_shadow():
    keyframes = re.search(
        r"@keyframes\s+conv-completion-glow\s*\{(?P<body>.*?)\n\s*\}",
        APP_CSS,
        flags=re.DOTALL,
    )
    assert keyframes, "missing completion glow keyframes"
    body = keyframes.group("body")
    assert "box-shadow" not in body
    assert "opacity" in body


def test_frame_monitor_pauses_css_animations_when_hidden_or_unfocused():
    block = APP_JS[
        APP_JS.index("function _initFrameMonitor()"):
        APP_JS.index("function _resumeForegroundPollers()")
    ]
    assert "ccc-anim-off" in block
    assert "visibilitychange" in block
    assert "hasFocus" in block
    assert "blur" in block


def test_collapsed_poller_strip_has_no_recurring_label_timer():
    block = APP_JS[
        APP_JS.index("function _initPollerStrip()"):
        APP_JS.index("function _initFrameMonitor()")
    ]
    assert "let _stripTickTimer = null;" in block
    assert "if (!_stripOpen || document.hidden) return;" in block
    assert "clearTimeout(_stripTickTimer);" in block
    assert "document.addEventListener('visibilitychange', _syncStripTicker);" in block
    assert "(function _tickLabels()" not in block


def test_ship_status_reuses_completed_responses_but_live_actions_force_refresh():
    block = APP_JS[
        APP_JS.index("const _shipPollTimers"):
        APP_JS.index("// ── Localhost / Next.js dev server pill")
    ]
    assert "const _SHIP_STATUS_CLIENT_TTL_MS = 15000;" in block
    assert "const _shipStatusCache = new Map();" in block
    assert "async function _refreshShipStatus(repo, box, force)" in block
    assert "Date.now() - cached.ts < _SHIP_STATUS_CLIENT_TTL_MS" in block
    assert "_shipStatusCache.set(repo, { ts: Date.now(), data: data });" in block
    assert block.count("_refreshShipStatus(repo, null, true)") >= 5
    render = block[block.index("function _renderShipStatus") : block.index("async function _openShipLog")]
    assert "statusEl.className !== cls" in render
    assert "statusEl.textContent !== txt" in render
    assert "statusEl.title !== title" in render


def test_ccc_owned_archive_callers_avoid_duplicate_compatibility_payloads():
    projected_url = "/api/conversations/list?window=all&stale_ok=1"
    assert projected_url in APP_JS
    assert "/api/conversations?all=1&compact=1&" in THROUGHPUT
