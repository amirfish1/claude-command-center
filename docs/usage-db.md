# Throughput usage database

A local SQLite database with **one row per session** across Claude Code, Codex
and Kimi: tokens (fresh input, cache read, cache write, output), model,
message counts, timestamps, plus a versioned price table and cost views. It
answers "what did each provider actually cost me, and is a second subscription
worth it?" from data instead of memory.

Source session stores are **only read**. Conversation text is never copied into
the database; content is parsed in memory to count turns and discarded.

```bash
scripts/throughput ingest            # or: python3 -m ccc_server.usage_db ingest
scripts/throughput summary --by month
scripts/throughput sessions --engine codex --order "cost_usd DESC" --limit 10
scripts/throughput runrate
scripts/throughput breakeven --fee 200
```

The database lives at `~/.claude/command-center/usage/throughput.sqlite3`
(override with `--db` or `CCC_THROUGHPUT_DB`). It is a cache of the source
stores: `ingest --full-rebuild` recreates every row.

## Commands

| Command | What it does |
|---|---|
| `ingest [--engine E] [--dry-run] [--full-rebuild] [--claude-root P] [--codex-root P] [--kimi-root P] [--json] [-v]` | Read the stores into the DB. Incremental (file size + mtime), idempotent, one corrupt file or line never stops the run. Prints discovered / inserted / updated / unchanged / skipped / failed sessions per engine. `--dry-run` writes nothing. A cold run over ~9,500 sessions takes about a minute; a rerun takes seconds. |
| `sessions` | Session list with tokens and cost. Filters: `--engine --provider --model --project --since --until --subagents exclude\|only\|include`. |
| `summary --by day\|week\|month\|engine [--engine E] [--model M[,M2]] [--split-model] [--by-family] [--since D] [--as-of T]` | Tokens, list-price cost and real cost roll-ups (UTC buckets). `--split-model` gives one row per model version per period; `--by-family` merges Claude versions into Opus/Sonnet/Fable/Haiku; `--model a,b` compares names side by side (one row per name, all matching versions summed: `--model sonnet,fable`). Model views add `% of list $` (share of the engine's list cost) and `list $/MTok`, and show no REAL columns. `--by engine` shows each engine's total row, then its models. |
| `activity` | First and last billed call per engine — read "Kimi stopped on date X" from the data. |
| `runrate` | Trailing-30-day and month-to-date list-price cost, real cost and multiplier per engine, plus a month projection (list price). |
| `breakeven --fee N` | Second-subscription comparison; the candidate fee is an input, never assumed. Also reports your current real $/MTok and list:real. |
| `plans add --name --engine --fee [--since D] [--until D]` / `plans` | Record subscription fees you actually pay. Same `--name` updates the plan in place (how you set its dates). `--since` inclusive, `--until` exclusive, `YYYY-MM-DD`; USD only. |
| `rates [load --file F]` | Show or load the price table. |
| `sql "SELECT ..."` | Read-only SQL. |

## Verified store formats

Everything the parsers rely on was checked against real stores, including old
and new versions, sub-agent runs, moved sessions and compaction.

### Claude Code — `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`
- One file per session; sub-agents are `<sessionId>/subagents/agent-*.jsonl` and
  become their own rows (`is_subagent = 1`, `parent_session_id` set).
- Each API response is written on several lines (one per content block) with the
  same `message.id` and identical `usage`. Usage is counted once per `message.id`.
- `usage.input_tokens` excludes the cache buckets. Cache creation is
  `cache_creation_input_tokens`, split 5m/1h under `usage.cache_creation`.
- `model: "<synthetic>"` lines are client-generated and billed nothing; ignored.
- A session id can exist in more than one project directory (a moved session
  leaves a small stub, or an identical copy). All files' events are claimed once
  by `message.id`; the largest file supplies the row's metadata.
- `system` / `compact_boundary` lines give `compaction_count`. `cost-state`
  records hold Claude Code's own cost estimate and are kept in
  `raw_metadata_json` for reconciliation.

### Codex — `~/.codex/sessions/**/rollout-*.jsonl` and `~/.codex/archived_sessions`
- One file per thread; `session_meta.payload.id` is the id. Sub-agent threads
  carry `parent_thread_id`.
- Usage is `event_msg`/`token_count` events. `last_token_usage` is that call's
  usage; `total_token_usage` is a running total that **is not reliable** (it can
  drop mid-file). Usage is the sum of `last_token_usage` over distinct events.
- `input_tokens` **includes** `cached_input_tokens`; `reasoning_output_tokens` is
  a subset of `output_tokens`. Fresh input is `input - cached`.
- The model is per turn (`turn_context.model`) and can change mid-session; each
  call takes the model of the latest turn.
- `compacted` records give `compaction_count`.

### Kimi — `~/.kimi-code/sessions/<wd>/session_<id>/agents/<agent>/wire.jsonl`
- `main` is the top-level session; `agent-N` are sub-agents (own rows).
- `usage.record` events carry `{inputOther, output, inputCacheRead,
  inputCacheCreation}` and the model. `step.end` repeats the same usage and is
  not counted. `token_counting.*` events measure context size, not billing.
- A rare `usageScope: "session"` record is a separate summary call at compaction
  (not a cumulative total) and is added as its own event (`scope = 'session'`).
- `state.json.updatedAt` is rewritten by housekeeping and is ignored;
  `last_activity_at` comes from the wire events.

## Schema

`sessions` — one row per logical session. Token buckets are disjoint:
`total_tokens = input_tokens + cache_read_input_tokens +
cache_creation_input_tokens + output_tokens` (`input_tokens` is fresh, uncached
input on every engine; `reasoning_tokens` is a subset of `output_tokens`).
`conversation_length` is stored as both `message_count` (user + assistant
messages) and `duration_seconds`. Quality columns: `usage_complete`,
`model_known`, `is_subagent`, `warning`. `model_id` is the dominant model;
`model_count > 1` means a mixed session, priced per call from `usage_events`.

`usage_events` — one row per billed model call (`engine + event_key` unique,
tokens, model, timestamp, source file). Sessions are aggregates of it; per-model
pricing, mid-session model changes and day/week/month buckets use it.

`price_rates`, `subscription_plans`, `ingest_files`, `meta`, and the views
`event_costs`, `session_costs`, `cost_by_day`, `cost_by_month`,
`cost_by_engine_model`.

Sub-agent runs are separate rows because they are separate, billed calls, not a
subset of their parent. Lists default to top-level sessions; sums over all
`sessions` rows include sub-agents.

## Cost layer

Rates are **data** in `price_rates` (USD per 1M tokens, effective-dated), never
inline in queries. `event_costs` joins the rate in force on each call's date.

- A missing rate, or a missing component the call actually uses (e.g. a cache
  read with no cache-read rate), makes that call's cost **unknown (NULL)**, never
  zero. `session_costs.cost_usd` is NULL unless every call is priced;
  `cost_usd_priced` + `unpriced_event_count` show what is known.
- All costs are **API list-price equivalents**. Subscription fees are a separate
  concept (`subscription_plans`, supplied by you) and are never blended into a
  per-token price. `breakeven` reports the API-equivalent run rate next to the
  fees and the share of that run rate an extra subscription would need to absorb.
- Cache savings = cache-read tokens × (input rate − cache-read rate).

### Real cost (what you actually pay)
Your plan fee is a flat monthly charge, so it is reported only where it means
something: **per engine over a period** (`summary` by day/week/month/engine,
`runrate`, `breakeven`). It is never split across models or sessions — any split
would give every row the same ratio — so `--split-model`, `--model` and the
`sessions` list show list price only and leave the real columns blank.

| Column | Meaning |
|---|---|
| `real_cost_usd` | Fee accrued over the period: `monthly_fee / days in that month` per day, for each day a plan is active. A full calendar month = the fee. The running period accrues through today. |
| `real_usd_per_mtok` (**REAL $/MTok**) | `real_cost_usd` ÷ all tokens (fresh + cache read + cache write + output), per 1M. Dominated by cache reads (~97% of tokens), so it is small and depends on how much context is re-read. |
| `real_usd_per_mtok_noncache` | Same, over fresh input + cache write + output only. |
| `list_to_real` (**LIST:REAL**) | List-price cost of the same tokens ÷ `real_cost_usd`: list-price dollars each dollar you pay buys. Independent of model mix and caching; the best cross-engine comparison. A lower bound when `unpriced_calls` > 0. |

Set each plan's `--since` (and `--until` if it ended). A plan with no start date
is assumed to have always existed, so its fee lands on every period shown —
including months before you subscribed — and the commands warn about it. A
period with no active plan shows `-`, never zero.

### Updating prices
1. Edit `ccc_server/usage_db/rates.json` (or a copy) — one object per
   `(pricing_key, effective_from)`. To change a price going forward, add a new
   row with a later `effective_from` instead of editing the old one; set
   `effective_to` on the old row if it should stop applying.
2. `scripts/throughput rates load [--file F]` (idempotent upsert).
3. Set `verified_at` only when you checked the rate against an independent
   source, and say which in `source_note`.

`pricing_key` is the model id lower-cased with any `vendor/` prefix, `[1m]`
suffix and `-YYYYMMDD` date removed and dots turned into dashes
(`kimi-code/k3` → `k3`, `claude-haiku-4-5-20251001` → `claude-haiku-4-5`).

The shipped rates were carried over from the list prices already recorded in
`ccc_server/morning_launch.py`, with the Anthropic 1-hour cache-write rate added
(2× input). The Claude rates for Haiku 4.5, Sonnet 5 and Opus 5 were checked by
fitting them to Claude Code's own `cost-state` totals (session cost within 1%
in aggregate). That is Claude Code's client-side estimate, not an invoice.

## Known gaps
- **Codex running totals.** `total_token_usage` drops mid-file in about 18% of
  sessions; those rows have `usage_complete = 0` and a warning. In a handful of
  sessions with a monotonic total the per-call sum differs from it; the warning
  states both numbers. The per-call sum is what is stored.
- **Codex events with no model** (usage before the first `turn_context`) and
  models with no rate (`gpt-5.3-codex-spark`, `gpt-reserve`, ...) are unpriced.
- **Effective dates.** The shipped rates apply from 1970-01-01 because the
  source table recorded no effective dates; past price changes are not modelled.
- **Buckets are UTC.** A local evening can land on the next UTC day.
- **Sessions that never billed** (no `token_count` / `usage.record` events) are
  kept when they have messages, flagged `usage_complete = 0`, and never counted
  as free.
- **No explicit end time** is recorded by any engine, so `ended_at` is NULL and
  `status` is `archived`/`active` only where the store says so.
- **Rotated or deleted source files** keep their existing rows; `ingest` reports
  how many are gone.
- **Kimi status** only refreshes when the wire file or `state.json` changes.

## Useful SQL

```sql
-- Cost by engine and model, with what is unpriced
SELECT engine, model, calls, total_tokens, cost_usd_priced, unpriced_calls
FROM cost_by_engine_model ORDER BY cost_usd_priced DESC;

-- Most expensive fully priced sessions this month
SELECT engine, source_session_id, model_label, project_name, total_tokens, cost_usd
FROM session_costs WHERE cost_complete = 1 AND is_subagent = 0
  AND started_at >= strftime('%Y-%m-01', 'now') ORDER BY cost_usd DESC LIMIT 20;

-- Cache savings by month
SELECT month, engine, SUM(cache_read_savings_usd) AS saved FROM cost_by_month GROUP BY 1, 2;

-- Sessions that switched models mid-way
SELECT engine, source_session_id, model_count, warning FROM sessions WHERE model_count > 1;

-- Sessions whose usage could not be reconciled
SELECT engine, source_session_id, warning FROM sessions WHERE usage_complete = 0 LIMIT 20;
```
