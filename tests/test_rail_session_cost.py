"""Behavior coverage for the status-rail Claude cost presentation."""

import json
import pathlib
import subprocess

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"
HELPER_START = "// RAIL_SESSION_COST_PRESENTATION_START"
HELPER_END = "// RAIL_SESSION_COST_PRESENTATION_END"


def _helper_source():
    source = APP_JS.read_text(encoding="utf-8")
    assert HELPER_START in source, "rail session-cost helper block is missing"
    assert HELPER_END in source, "rail session-cost helper terminator is missing"
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


def test_every_claude_session_gets_a_numeric_cost():
    ranked, unranked, loading, failed, noPlan, normalized, codex = _run_helper("""[
      railSessionCostPresentation(
        {engine:'claude', cost_usd:12.5}, 'ranked', 200,
        {totalTokens:1000, bySession:{ranked:250}, rankingsAvailable:true}
      ),
      railSessionCostPresentation(
        {engine:'claude', cost_usd:9.25}, 'unranked', 200,
        {totalTokens:1000, bySession:{ranked:250}, rankingsAvailable:true}
      ),
      railSessionCostPresentation(
        {engine:'claude', cost_usd:9.25}, 'loading', 200, null
      ),
      railSessionCostPresentation(
        {engine:'claude', cost_usd:8.75}, 'failed', 200,
        {totalTokens:1000, bySession:{}, rankingsAvailable:false}
      ),
      railSessionCostPresentation(
        {engine:'claude', cost_usd:7.5}, 'no-plan', 0,
        {totalTokens:1000, bySession:{}, rankingsAvailable:true}
      ),
      railSessionCostPresentation(
        {engine:' Claude ', cost_usd:6.5}, 'normalized', 200,
        {totalTokens:1000, bySession:{normalized:100}, rankingsAvailable:true}
      ),
      railSessionCostPresentation(
        {engine:'codex', cost_usd:9.25}, 'codex', 200,
        {totalTokens:1000, bySession:{codex:250}, rankingsAvailable:true}
      )
    ]""")

    assert ranked["basis"] == "subscription"
    assert ranked["cost"] == pytest.approx(200 * 12 / 52 * 0.25)
    assert unranked["basis"] == "subscription"
    assert unranked["cost"] == 0
    assert loading == {
        "cost": 9.25,
        "basis": "api",
        "label": "API list-price equivalent",
        "share": 0,
        "sessionTokens": 0,
        "weeklyTokens": 0,
    }
    assert failed["basis"] == "api"
    assert failed["cost"] == 8.75
    assert noPlan["basis"] == "api"
    assert noPlan["cost"] == 7.5
    assert normalized["basis"] == "subscription"
    assert normalized["cost"] == pytest.approx(200 * 12 / 52 * 0.1)
    assert codex is None


def test_visible_cost_text_and_tooltip_execute_production_formatter():
    subscription, api = _run_helper("""[
      railSessionCostText(railSessionCostPresentation(
        {engine:'claude', cost_usd:9.25}, 'unranked', 200,
        {totalTokens:1000, bySession:{ranked:250}, rankingsAvailable:true}
      )),
      railSessionCostText(railSessionCostPresentation(
        {engine:'claude', cost_usd:0.2911}, 'loading', 200, null
      ))
    ]""")

    assert subscription == {
        "cost": "$0.00",
        "label": "$0.00 subscription cost this week",
        "tooltip": "0.0% of 1,000 Claude tokens this week",
    }
    assert api == {
        "cost": "$0.2911",
        "label": "$0.2911 API list-price equivalent",
        "tooltip": "API list-price equivalent: $0.2911",
    }


def test_status_rail_renders_the_selected_cost_basis():
    source = APP_JS.read_text(encoding="utf-8")

    assert "const costText = railSessionCostText(presentation);" in source
    assert "costText.label" in source
    assert "costText.tooltip" in source
    assert "/api/throughput/week-rankings?fresh=1" in source
