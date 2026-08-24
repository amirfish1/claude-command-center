"""Regression coverage for the per-session throughput metric cards."""

import json
import pathlib
import subprocess

import pytest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _session_plan_metrics(summary, weekly):
    html = (PROJECT_ROOT / "static" / "throughput.html").read_text(encoding="utf-8")
    plan_price_start = html.index("const MONTHLY_PLAN_USD =")
    start = html.index("function sessionPlanMetrics(")
    end = html.index("\n    function ", start + 1)
    function_source = html[plan_price_start:end]
    script = f"""
{function_source}
console.log(JSON.stringify(sessionPlanMetrics({json.dumps(summary)}, {json.dumps(weekly)})));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_session_metrics_price_lifetime_usage_at_the_monthly_plan_rate():
    metrics = _session_plan_metrics(
        {
            "total_raw_context_tokens": 9_500_000,
            "total_effective_input_tokens": 1_250_000,
            "total_output_tokens": 500_000,
        },
        {"pct_per_token": 0.000004},
    )

    assert metrics["quota_pct"] == pytest.approx(40.0)
    assert metrics["cache_adjusted_tokens"] == 1_750_000
    assert metrics["plan_cost_usd"] == pytest.approx(80.0)


def test_session_metrics_leave_quota_and_plan_cost_unavailable_without_calibration():
    metrics = _session_plan_metrics(
        {
            "total_raw_context_tokens": 9_500_000,
            "total_effective_input_tokens": 1_250_000,
            "total_output_tokens": 500_000,
        },
        {},
    )

    assert metrics["quota_pct"] is None
    assert metrics["plan_cost_usd"] is None
