# Telemetry Worker

Minimal Cloudflare Worker for CCC's anonymous open beacon (schema 1 and 2),
a legacy daily ping kept for old clients, a landing-page download-click
counter, and public aggregate stats. See
[`docs/telemetry.md`](../../docs/telemetry.md) for the full contract. The source
lives here so every persisted value is auditable.

## What it does

- Accepts `POST /v1/open` with `schema_version` 1 or 2. Schema 2 adds
  `first_this_week` / `first_this_month` (booleans, required when
  `schema_version` is 2); schema 1 rows store `NULL` for those columns.
  Both schemas accept an optional `dev` boolean.
- Reads coarse geo from Cloudflare's own `request.cf` at insert time — never
  from the request body — and persists only `country` (ISO-2) and `region`
  (first-level subdivision name) after format validation. Never city,
  postal code, lat/long, or ASN.
- Accepts `POST /v1/ping` for installs on builds from before 2026-09-28;
  current CCC builds no longer send it (see `docs/telemetry.md`).
- Accepts an empty `POST /v1/download`; it never receives the request object
  and writes only receive time, `ccc.dmg`, and `landing-hero`.
- Serves aggregate-only `GET /v1/stats`, additive-only: totals, 90-day daily
  buckets, weekly/monthly active-install counts derived from
  `first_this_week`/`first_this_month` bound to exact ISO-week/calendar-month
  boundaries (current and previous complete period, never a double-counting
  trailing window), and `geo_week` — one ISO week of country/region
  breakdown counted per install, buckets under 3 installs folded into
  `"other"` — plus site download clicks.
- `opens_versions_week` is `[{version, installs}, ...]` from `opens`,
  grouped by version over non-dev rows with `first_this_week = 1`.
  It uses the same week as `geo_week.week_start`: the previous complete
  ISO week if it has qualifying non-dev rows, otherwise the current week
  so far. Each count records the version at the install's first weekly
  beacon, so upgrades later that week are not reflected. Versions with
  fewer than 3 installs are folded into one `{version: "other", installs}`
  row using the geo suppression helper; that combined count may be below 3.
  Empty results are `[]`. This adds one grouped D1 query and shares the
  existing stats KV cache. Client payloads and collection are unchanged.
  The legacy `versions` and `versions_7d` fields still use opt-in pings
  from builds before v5.34.0; they do not describe current beacon installs.

- Drops any unknown fields silently. Rejects requests where the listed
  fields fail type validation.
- **Drops the source IP** before writing anywhere durable. The Worker
  hashes it with a daily-rotating secret and stores only the hash.
- Appends a row to a Cloudflare D1 table.
- Returns `204 No Content` on success, `400` on shape errors, `405`
  on wrong method. Never returns row counts or any other state to the
  caller.

That's the entire surface.

## Status

Deployed at `telemetry.claude-command-center.workers.dev`. The immutable source
revision and deployment date are recorded in
[`docs/telemetry-public.md`](../../docs/telemetry-public.md).

## Deploying

The Worker is intentionally tiny (~40 LOC) and uses zero npm
dependencies — `wrangler deploy` is the only step.

```bash
cd infra/telemetry-worker
npm install -g wrangler                # one-time
wrangler login                         # one-time, opens browser
wrangler d1 create ccc-telemetry       # one-time, capture DB id
wrangler d1 execute ccc-telemetry --remote --file migrations/0001-downloads.sql
wrangler d1 execute ccc-telemetry --remote --file migrations/0002-pings-is-dev.sql
wrangler d1 execute ccc-telemetry --remote --file migrations/0003-opens-weekly-monthly-geo.sql
wrangler kv namespace create STATS_CACHE   # one-time, bind it as STATS_CACHE
wrangler deploy
```

`/v1/stats` reads through the `STATS_CACHE` KV binding: one D1 scan an hour at
most. Without the binding every caller rescans, and D1's free plan stops all
reads for the day after 5M rows. Don't swap it for the Cache API; that has no
effect on `*.workers.dev`.

Migrations run in filename order and are additive (`ALTER TABLE ... ADD
COLUMN`) — safe to run against a live table. `0003` adds `first_this_week`,
`first_this_month`, `country`, and `region` to `opens`; existing rows read
back as `NULL` for all four, which the aggregate queries below treat as
schema-1 data.

The committed `wrangler.toml` binds the public D1 database. Database ids are
resource identifiers, not credentials; authentication remains in Wrangler's
local account configuration.

## Aggregating

The legacy ping table's aggregate query (data frozen at the 2026-09-28
retirement, kept for the "Opt-in pings" legacy tab):

```sql
SELECT
  substr(received_at, 1, 10) AS date,
  version,
  platform,
  COUNT(DISTINCT install_id) AS installs
FROM pings
WHERE received_at >= date('now', '-90 days')
GROUP BY date, version, platform
ORDER BY date DESC;
```

The open beacon carries no identifier, so its weekly/monthly "installs"
figures come from each client's own `first_this_week` / `first_this_month`
flag instead of a `COUNT(DISTINCT ...)`. The window is the exact ISO week
(Monday-Sunday UTC), not a trailing 7 days — `first_this_week` is set once
per ISO week, so a rolling window double-counts any install active in both
the tail of last week and the start of this one. SQLite's `'weekday 1'`
modifier gives the Monday of the ISO week containing a date (verified with
the `sqlite3` CLI against a Monday, Wednesday and Sunday "now" substitute:
`date('2026-09-28','-6 days','weekday 1')`,
`date('2026-09-30','-6 days','weekday 1')`, and
`date('2026-10-04','-6 days','weekday 1')` all resolve to `2026-09-28`, the
Monday of that ISO week):

```sql
SELECT COUNT(*) AS weekly_active_installs
FROM opens
WHERE first_this_week = 1
  AND received_at >= date('now', '-6 days', 'weekday 1');
```

Geo breakdowns count **installs**, not beacons: only `first_this_week = 1`
rows are included, over one ISO week (`geo_week.week_start` in the
response), grouped by the sanitized `country` + `region` columns together
(so the US state "Georgia" and the country "Georgia" never merge), and
suppressed below a minimum install count before they ever reach
`/v1/stats`:

```sql
SELECT country, region, COUNT(*) AS installs
FROM opens
WHERE first_this_week = 1
  AND received_at >= :week_start AND received_at < :week_end
  AND region IS NOT NULL
GROUP BY country, region
ORDER BY installs DESC;
```

No per-install or per-beacon rows are ever published; `/v1/stats` returns
aggregates only, with small geo buckets folded into a single row with every
key field set to `"other"` (`suppressSmallBuckets` in `index.js`).

## Why this lives in the same repo

The Worker's privacy guarantees are only worth what the source backs
up. Keeping the code beside `server.py` means anyone auditing the
client can audit the server in one `git clone`. If we ever split this
out, the split itself is a breaking change to the trust contract and
should be documented in `telemetry-public.md` first.
