import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"
APP_CSS = ROOT / "static" / "app.css"
HELPER_START = "// SESSION_ICON_PRESENTATION_START"
HELPER_END = "// SESSION_ICON_PRESENTATION_END"


def _helper_source():
    source = APP_JS.read_text(encoding="utf-8")
    assert HELPER_START in source, "session icon presentation helper block is missing"
    assert HELPER_END in source, "session icon presentation helper block terminator is missing"
    start = source.index(HELPER_START) + len(HELPER_START)
    end = source.index(HELPER_END, start)
    return source[start:end]


def _run_helpers(expression):
    script = f"""
{_helper_source()}
const result = {expression};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_cost_tier_matrix_executes_production_classifier():
    cases = [
        ["claude", "fable", "premium"],
        ["claude", "claude-fable-5", "premium"],
        ["claude", "opus", "high"],
        ["claude", "claude-opus-4-8[1m]", "high"],
        ["claude", "sonnet-5", "medium"],
        ["claude", "claude-haiku-4-5", "low"],
        ["codex", "sol", "premium"],
        ["codex", "gpt-5.6-sol", "premium"],
        ["codex", "gpt-5.6-terra", "medium"],
        ["codex", "gpt-5.6-luna", "low"],
        ["codex", "gpt-5.5", ""],
        ["codex", "claude-opus-4-8", ""],
        ["claude", "gpt-5.6-sol", ""],
        ["claude", "", ""],
        ["", "fable-5", ""],
    ]
    actual = _run_helpers(
        json.dumps(cases) + ".map(([engine, model]) => sessionCostTier(engine, model))"
    )
    assert actual == [case[2] for case in cases]


def test_activity_matrix_executes_production_classifier():
    cases = [
        [{"source": "claude", "state": "working"}, False, True],
        [{"source": "claude", "state": "idle", "is_live": True}, False, False],
        [{"source": "claude", "state": "waiting", "is_live": True}, False, False],
        [{"source": "claude", "state": "ended"}, False, False],
        [{"source": "codex", "codex_state": "working", "state": "idle"}, False, True],
        [{"source": "codex", "codex_state": "idle", "is_live": True}, False, False],
        [{"source": "codex", "codex_state": "waiting", "is_live": True}, False, False],
        [{"source": "codex", "codex_state": "stuck", "is_live": True}, False, False],
        [{"source": "codex", "codex_state": "offline"}, False, False],
        [{"source": "claude", "state": "idle", "pending_spawn": True}, False, True],
        [{"source": "claude", "state": "interactive"}, True, True],
        [{"source": "claude", "state": "waiting"}, True, False],
        [{"source": "codex", "codex_state": "idle"}, True, False],
        [None, False, False],
    ]
    actual = _run_helpers(
        json.dumps(cases)
        + ".map(([row, optimistic]) => sessionIsActivelyWorking(row, optimistic))"
    )
    assert actual == [case[2] for case in cases]


def test_presentation_tooltip_keeps_engine_cost_and_activity_independent():
    expression = """[
      sessionIconPresentation({source:'claude', model:'claude-fable-5', state:'idle'}, false),
      sessionIconPresentation({source:'codex', model:'gpt-5.6-sol', codex_state:'working'}, false),
      sessionIconPresentation({source:'codex', model:'gpt-5.5', codex_state:'idle'}, false)
    ]"""
    fable, sol, unknown = _run_helpers(expression)

    assert fable["engine"] == "claude"
    assert fable["tier"] == "premium"
    assert fable["working"] is False
    assert fable["title"] == "Claude · claude-fable-5 · Premium cost · Not working"

    assert sol["engine"] == "codex"
    assert sol["tier"] == "premium"
    assert sol["working"] is True
    assert sol["title"] == "Codex · gpt-5.6-sol · Premium cost · Working now"

    assert unknown["tier"] == ""
    assert unknown["title"] == "Codex · gpt-5.5 · Cost tier unknown · Not working"


def test_canonical_engine_field_wins_over_generic_source():
    presentation = _run_helpers(
        "sessionIconPresentation({source:'session', engine:'codex', model:'gpt-5.6-sol', codex_state:'working'}, false)"
    )

    assert presentation["engine"] == "codex"
    assert presentation["tier"] == "premium"
    assert presentation["working"] is True


def test_sidebar_and_pane_header_use_shared_session_icon_renderer():
    source = APP_JS.read_text(encoding="utf-8")

    assert "function sessionEngineIconHtml(row, options)" in source
    assert "sessionEngineIconHtml(c, { context: 'sidebar' })" in source
    assert "sessionEngineIconHtml(row, { context: 'pane' })" in source
    assert "sessionIconPresentation(row, optimistic)" in source
    assert "session-activity-dot" in source
    assert "tierDollarCounts = { premium: 3, high: 2, medium: 1, low: 0 }" in source
    assert "session-tier-cost" in source
    assert "session-tier-label" not in source
    assert "session-cost-orbit" not in source
    assert "session-cost-symbol" not in source
    assert "is-fable5" not in source


def test_unified_tier_color_css_replaces_orbits_and_process_liveness_animation():
    css = APP_CSS.read_text(encoding="utf-8")

    for tier in ("premium", "high", "medium", "low"):
        assert f".cost-{tier}" in css
    assert ".session-tier-cost" in css
    assert ".session-tier-label" not in css
    assert ".session-activity-dot" in css
    assert ".is-working .session-activity-dot" in css
    assert ".is-not-working .session-activity-dot" in css
    assert "@keyframes ccc-activity-dot-pulse" in css
    assert "prefers-reduced-motion: reduce" in css

    assert ".conv-session-icon.is-live" not in css
    assert ".conv-session-icon.is-dead" not in css
    assert "@keyframes ccc-icon-pulse" not in css
    assert ".is-fable5" not in css
    assert ".session-cost-orbit" not in css
    assert ".session-cost-symbol" not in css


def test_unified_tier_palette_is_engine_independent():
    css = APP_CSS.read_text(encoding="utf-8")
    start = css.index("/* Unified session cost palette */")
    end = css.index("/* End unified session cost palette */", start)
    palette = css[start:end]

    assert ".cost-premium .session-tier-cost" in palette and "color: #d6b94a;" in palette
    assert ".cost-high .session-tier-cost" in palette and "color: #c88f42;" in palette
    assert ".cost-medium .session-tier-cost" in palette and "color: #5f91b5;" in palette
    assert ".cost-low .session-tier-cost" not in palette
    assert ".cost-premium.claude" not in palette
    assert ".cost-premium.codex" not in palette
    assert "filter: brightness(1.35) saturate(1.15);" in palette
    assert "filter: brightness(1.1) saturate(1.05);" in palette
    assert "filter: brightness(0.95) saturate(0.9);" in palette
    assert "filter: brightness(0.8) saturate(0.72);" in palette


def test_cost_marker_has_readable_horizontal_space():
    css = APP_CSS.read_text(encoding="utf-8")

    assert "--conv-dot-col: 36px;" in css
    assert "font: 900 11px/1 ui-monospace" in css
    assert ".session-tier-cost i" in css
    assert "display: inline;" in css
