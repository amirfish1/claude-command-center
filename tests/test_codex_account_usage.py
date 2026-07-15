import json
from pathlib import Path

import server


NOW = 1_784_092_800


def _account_rate_limit_response():
    return {
        "result": {
            "rateLimits": {
                "limitId": "codex_model",
                "limitName": "Model scoped",
                "primary": {
                    "usedPercent": 91,
                    "resetsAt": NOW + 3600,
                    "windowDurationMins": 300,
                },
            },
            "rateLimitsByLimitId": {
                "codex_model": {
                    "limitId": "codex_model",
                    "limitName": "Model scoped",
                    "primary": {
                        "usedPercent": 91,
                        "resetsAt": NOW + 3600,
                        "windowDurationMins": 300,
                    },
                },
                "codex": {
                    "limitId": "codex",
                    "planType": "prolite",
                    "primary": {
                        "usedPercent": 37,
                        "resetsAt": NOW + 6 * 86400,
                        "windowDurationMins": 10080,
                    },
                    "secondary": {
                        "usedPercent": 12,
                        "resetsAt": NOW + 7200,
                        "windowDurationMins": 300,
                    },
                },
            },
        }
    }


def test_account_rate_limits_use_base_bucket_and_classify_by_duration():
    usage = server._codex_usage_from_account_rate_limits(
        _account_rate_limit_response(), now_epoch=NOW
    )

    assert usage["weekly"]["pct"] == 37
    assert usage["weekly"]["window_minutes"] == 10080
    assert usage["session"]["pct"] == 12
    assert usage["session"]["window_minutes"] == 300
    assert usage["plan_type"] == "prolite"
    assert usage["snapshot_ts"] == server._usage_snapshot_iso(NOW)


def test_account_rate_limits_accept_weekly_primary_without_secondary():
    response = _account_rate_limit_response()
    response["result"]["rateLimitsByLimitId"]["codex"]["secondary"] = None

    usage = server._codex_usage_from_account_rate_limits(response, now_epoch=NOW)

    assert usage["weekly"]["pct"] == 37
    assert usage["session"] is None


def test_account_rate_limits_do_not_treat_timed_secondary_session_as_weekly():
    response = _account_rate_limit_response()
    base = response["result"]["rateLimitsByLimitId"]["codex"]
    base["primary"] = None
    base["secondary"] = {
        "usedPercent": 12,
        "resetsAt": NOW + 7200,
        "windowDurationMins": 300,
    }

    assert server._codex_usage_from_account_rate_limits(response, now_epoch=NOW) is None


def test_account_rate_limits_reject_nonfinite_or_out_of_range_percentages():
    for invalid_pct in ("NaN", "Infinity", -1, 101):
        response = _account_rate_limit_response()
        response["result"]["rateLimitsByLimitId"]["codex"]["primary"][
            "usedPercent"
        ] = invalid_pct

        assert server._codex_usage_from_account_rate_limits(
            response, now_epoch=NOW
        ) is None


def test_read_codex_usage_prefers_live_account_snapshot(monkeypatch):
    monkeypatch.setattr(server.time, "time", lambda: NOW)
    monkeypatch.setattr(
        server,
        "_codex_app_server_request",
        lambda method, params, timeout: _account_rate_limit_response(),
    )
    monkeypatch.setattr(
        server,
        "_iter_recent_codex_rollouts",
        lambda now_epoch=None: (_ for _ in ()).throw(AssertionError("fallback used")),
    )

    usage = server._read_codex_usage()

    assert usage["weekly"]["pct"] == 37


def test_rollout_fallback_ignores_newer_model_scoped_bucket(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    records = [
        {
            "timestamp": "2026-07-15T01:00:00Z",
            "payload": {
                "rate_limits": {
                    "limit_id": "codex",
                    "plan_type": "prolite",
                    "primary": {
                        "used_percent": 36,
                        "resets_at": NOW + 6 * 86400,
                        "window_minutes": 10080,
                    },
                    "secondary": None,
                }
            },
        },
        {
            "timestamp": "2026-07-15T01:01:00Z",
            "payload": {
                "rate_limits": {
                    "limit_id": "codex_model",
                    "limit_name": "Model scoped",
                    "primary": {
                        "used_percent": 88,
                        "resets_at": NOW + 3600,
                        "window_minutes": 300,
                    },
                }
            },
        },
    ]
    rollout.write_text("".join(json.dumps(row) + "\n" for row in records))
    server._codex_usage_file_cache.clear()

    snapshot = server._codex_file_latest_rate_limits(rollout)

    assert snapshot["rate_limits"]["limit_id"] == "codex"


def test_account_usage_response_is_sanitized_and_sorted():
    response = {
        "result": {
            "summary": {
                "lifetimeTokens": 5000,
                "peakDailyTokens": 900,
                "longestRunningTurnSec": 120,
                "currentStreakDays": 4,
                "longestStreakDays": 8,
                "privateField": "do not expose",
            },
            "dailyUsageBuckets": [
                {"startDate": "not-a-date", "tokens": 999},
                {"startDate": "2026-07-14", "tokens": 900},
                {"startDate": "2026-07-13", "tokens": 700},
                {"startDate": "2026-07-12", "tokens": -1},
                {"startDate": "2026-07-11", "tokens": 1.5},
            ],
        }
    }

    usage = server._codex_account_usage_from_response(response, now_epoch=NOW)

    assert usage == {
        "source": "codex_app_server",
        "fetched_at": server._usage_snapshot_iso(NOW),
        "summary": {
            "lifetime_tokens": 5000,
            "peak_daily_tokens": 900,
            "longest_running_turn_sec": 120,
            "current_streak_days": 4,
            "longest_streak_days": 8,
        },
        "daily": [
            {"day": "2026-07-13", "tokens": 700},
            {"day": "2026-07-14", "tokens": 900},
        ],
    }


def test_attach_account_usage_preserves_throughput_summary():
    payload = {"ok": True, "summary": {"total_turns": 3, "daily": []}}
    account_usage = {"source": "codex_app_server", "daily": []}

    assert server._throughput_attach_account_usage(payload, account_usage) is True
    assert payload["summary"]["total_turns"] == 3
    assert payload["summary"]["account_usage"] == account_usage


def test_throughput_page_renders_account_reconciliation_strip():
    html = Path("static/throughput.html").read_text()

    assert "account_usage" in html
    assert "renderAccountUsageStrip" in html
    assert "unattributed" in html
    assert "codex.account_usage" in html
    assert "summary.components.codex" in html
