"""Behavior coverage for low-work, stable session-usage refreshes."""

import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"
HELPER_START = "// SESSION_USAGE_REFRESH_POLICY_START"
HELPER_END = "// SESSION_USAGE_REFRESH_POLICY_END"


def _helper_source():
    source = APP_JS.read_text(encoding="utf-8")
    assert HELPER_START in source, "session usage refresh policy is missing"
    assert HELPER_END in source, "session usage refresh policy terminator is missing"
    start = source.index(HELPER_START) + len(HELPER_START)
    end = source.index(HELPER_END, start)
    return source[start:end]


def _run_helper(expression):
    program = f"""
{_helper_source()}
const result = {expression};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", program],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_usage_refresh_policy_avoids_hidden_duplicate_and_too_frequent_work():
    visible_same, hidden_pane, hidden_page, duplicate, recent = _run_helper("""[
      sessionUsageRefreshDecision('sid-a', 'sid-a', true, true, ''),
      sessionUsageRefreshDecision('sid-a', 'sid-a', false, true, ''),
      sessionUsageRefreshDecision('sid-a', 'sid-a', true, false, ''),
      sessionUsageRefreshDecision('sid-a', 'sid-a', true, true, 'sid-a'),
      sessionUsageRefreshDecision('sid-a', 'sid-a', true, true, '', 'sid-a')
    ]""")

    assert visible_same == {
        "shouldFetch": True,
        "shouldClear": False,
        "shouldCoalesce": False,
    }
    assert hidden_pane == {
        "shouldFetch": False,
        "shouldClear": False,
        "shouldCoalesce": False,
    }
    assert hidden_page == hidden_pane
    assert duplicate == {
        "shouldFetch": False,
        "shouldClear": False,
        "shouldCoalesce": True,
    }
    assert recent == hidden_pane


def test_usage_refresh_policy_clears_only_visible_switch_or_explicit_reset():
    visible_switch, hidden_switch, reset = _run_helper("""[
      sessionUsageRefreshDecision('sid-a', 'sid-b', true, true, ''),
      sessionUsageRefreshDecision('sid-a', 'sid-b', false, true, ''),
      sessionUsageRefreshDecision('sid-a', null, false, false, 'sid-a')
    ]""")

    assert visible_switch == {
        "shouldFetch": True,
        "shouldClear": True,
        "shouldCoalesce": False,
    }
    assert hidden_switch == {
        "shouldFetch": False,
        "shouldClear": False,
        "shouldCoalesce": False,
    }
    assert reset == {
        "shouldFetch": False,
        "shouldClear": True,
        "shouldCoalesce": False,
    }


def test_usage_refresh_rechecks_visibility_before_rendering():
    source = APP_JS.read_text(encoding="utf-8")

    assert "function _sessionUsagePaneIsVisible" in source
    assert "_usageRequestByPane" in source
    assert "_usageRequestGenerationByPane" in source
    assert "SESSION_USAGE_REFRESH_MIN_MS" in source
    assert "if (!_sessionUsagePaneIsVisible(pid, sid)) return;" in source

    rail = source[source.index("function _renderRailTokens"):]
    rail = rail[: rail.index("function _contextRingSvg")]
    assert "if (document.hidden) return;" in rail

    weekly = source[source.index("function _refreshWeeklyClaudeUsage"):]
    weekly = weekly[: weekly.index("// RAIL_SESSION_COST_PRESENTATION_START")]
    assert "if (document.hidden) return Promise.resolve(null);" in weekly
