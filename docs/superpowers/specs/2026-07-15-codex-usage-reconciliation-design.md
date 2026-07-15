# Codex Usage Reconciliation Design

## Goal

Bring CCC's Codex usage display in line with the current Codex app-server
contracts without losing the session, model, and three-hour detail available
only in local rollout files.

## Data sources

CCC will treat the two Codex account methods as separate authorities:

- `account/rateLimits/read` is authoritative for quota percentages and reset
  times. CCC selects the base `codex` bucket from `rateLimitsByLimitId` and
  classifies windows by `windowDurationMins`, not by whether they happen to be
  named `primary` or `secondary`.
- `account/usage/read` is authoritative for account-wide daily and lifetime
  token totals.
- Rollout `token_count` events remain authoritative for local session, model,
  cache, output, and sub-day attribution.

If app-server account data is unavailable, quota reads fall back to recent
rollout events and the graph continues to render local detail without an
account reconciliation layer.

## Server contract

The existing `/api/usage/current` response shape remains compatible. Its Codex
`session` and `weekly` blocks are populated from normalized app-server rate
limits when available.

Codex aggregate throughput bootstraps gain `summary.account_usage`:

```json
{
  "source": "codex_app_server",
  "fetched_at": "2026-07-15T00:00:00Z",
  "summary": {
    "lifetime_tokens": 1000,
    "peak_daily_tokens": 500,
    "current_streak_days": 2,
    "longest_streak_days": 3,
    "longest_running_turn_sec": 60
  },
  "daily": [
    {"day": "2026-07-14", "tokens": 500}
  ]
}
```

Only validated non-negative integers and ISO calendar dates are exposed.
Unavailable or malformed responses produce no `account_usage` field rather
than failing the throughput refresh.

## Graph behavior

The existing three-hour cache-adjusted bars, quota projection, and model table
remain unchanged. In Codex and Combined aggregate views, authoritative daily
account totals render as a slim day-aligned strip inside the graph. Hovering a
day shows:

- authoritative account tokens;
- locally attributed raw tokens for that day; and
- the non-negative difference labelled `unattributed`.

The strip is an annotation, not another y-axis series. It therefore does not
pretend daily totals have hourly timestamps or compare raw account tokens to
cache-adjusted three-hour bars on the same scale. If account data is absent,
the strip and its legend text are omitted.

## Verification

Tests cover camelCase app-server rate limits, a weekly window appearing in
`primary`, model-scoped bucket exclusion, rollout fallback, account-usage
sanitization, bootstrap propagation, and the graph's reconciliation strip.
The existing smoke and performance suites must remain green, followed by a
Puppeteer snapshot of the Codex aggregate graph.
