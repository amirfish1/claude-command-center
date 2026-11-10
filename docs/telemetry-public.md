# Public telemetry aggregates

CCC publishes live aggregate counts at
[`ccc.amirfish.ai/stats`](https://ccc.amirfish.ai/stats). The page shows the
anonymous open beacon (daily, weekly, monthly, and coarse geo), legacy
opt-in ping activity from before the retirement below, and landing-page
download clicks — without exposing event rows.

## Status: collection live

| | |
| --- | --- |
| App endpoints | `/v1/open` (active); `/v1/ping` (accepted for old clients, no longer sent by current builds — retired 2026-09-28) |
| Landing endpoint | `/v1/download` |
| Public aggregates | `/v1/stats` |
| Worker source SHA at deploy | [`1037b37d`](https://github.com/amirfish1/claude-command-center/tree/1037b37d/infra/telemetry-worker) |
| D1 migration | `infra/telemetry-worker/migrations/0003-opens-weekly-monthly-geo.sql` adds `first_this_week`, `first_this_month`, `country`, `region` to `opens` |
| Worker deployed | 2026-11-06 (hourly stats cache in KV; nothing new is collected); 2026-09-28 (beacon v2: weekly/monthly flags + country/region); 2026-07-15 (download counter); initially 2026-05-22 |
| Collection started | 2026-05-22 |
| Storage | Cloudflare D1 (`ccc-telemetry`), with bounded `pings`, `opens`, and `downloads` tables documented in [`telemetry.md`](telemetry.md). Cloudflare KV holds one copy of the public aggregates only |

The persistence guarantees are in
[`infra/telemetry-worker/index.js`](../infra/telemetry-worker/index.js)
at the pinned SHA above. The download handler never receives the request object,
so it cannot read IP, headers, cookies, or body. Its D1 table has only
`received_at`, `artifact`, and `source` payload columns. The open beacon hashes
IP with a daily-rotating secret and persists only that hash, plus a country
code and region name read from Cloudflare's edge geolocation (`request.cf`) —
never city, postal code, lat/long, ASN, or the raw IP. The legacy opt-in ping
did not persist IP either. Re-check any time:

```bash
git show fb1c0e37:infra/telemetry-worker/index.js | grep -n 'handleDownload\|CF-Connecting-IP'
```

The opt-in daily ping (and its consent banner) is retired as of 2026-09-28;
see [`telemetry.md`](telemetry.md#legacy-the-opt-in-daily-ping). If you opted
in on an older build, that data is preserved read-only under the stats
page's "Opt-in pings" tab. The current build sends only the anonymous open
beacon, which needs no opt-in. The kill switch in
[`telemetry.md`](telemetry.md#kill-switch) still works for it.

## Live aggregate

`GET /v1/stats` returns totals and 90-day daily buckets, and is additive-only
— new fields are added over time, existing ones are never renamed or
removed. As of the migration above it also returns:

- Weekly and monthly active-install counts (`weekly_active_installs`,
  `monthly_active_installs`, `_prev` variants for the last complete
  period, and `_all` variants including the maintainer's own machine),
  derived from the `first_this_week` / `first_this_month` flags each
  beacon computes locally — no identifier involved. The weekly figures are
  bound to the exact ISO week (Monday-Sunday UTC), not a trailing 7-day
  window, so an install active across a week boundary is never counted
  twice.
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

- `geo_week` — one ISO week of country/region breakdown, counted per
  **install** (only `first_this_week = 1` rows), with any country or
  region under 3 installs folded into `"other"`. `week_start` says which
  Monday the numbers cover.

Legacy opt-in install counts use `COUNT(DISTINCT install_id)` and only cover
pings from older builds, which can still report after retirement. Download clicks use simple counts
because no identifier is collected; repeated clicks count again. Beacon and download results are aggregates, without individual event rows.
The legacy `sessions_today_per_install` field still includes truncated
install IDs and last-seen timestamps from opt-in pings.

## When this changes

Any change to what the Worker stores, or to the endpoint URL the
client posts to, is a contract change and lands here first — with a
new deploy SHA pinned in the table above.
