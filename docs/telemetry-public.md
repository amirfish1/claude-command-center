# Public telemetry aggregates

CCC publishes live aggregate counts at
[`ccc.amirfish.ai/stats`](https://ccc.amirfish.ai/stats). The page shows opt-in
activity, anonymous boot beacons, and landing-page download clicks without
exposing event rows.

## Status: collection live

| | |
| --- | --- |
| App endpoints | `/v1/ping`, `/v1/open` |
| Landing endpoint | `/v1/download` |
| Public aggregates | `/v1/stats` |
| Worker source SHA at deploy | [`9cd0b3d`](https://github.com/amirfish1/claude-command-center/tree/9cd0b3d/infra/telemetry-worker) |
| Worker deployed | 2026-07-15 (download counter); initially 2026-05-22 |
| Collection started | 2026-05-22 |
| Storage | Cloudflare D1 (`ccc-telemetry`), with bounded `pings`, `opens`, and `downloads` tables documented in [`telemetry.md`](telemetry.md) |

The persistence guarantees are in
[`infra/telemetry-worker/index.js`](../infra/telemetry-worker/index.js)
at the pinned SHA above. The download handler never receives the request object,
so it cannot read IP, headers, cookies, or body. Its D1 table has only
`received_at`, `artifact`, and `source` payload columns. The boot beacon hashes
IP with a daily secret and persists only the rotating hash; the daily opt-in
ping does not persist IP. Re-check any time:

```bash
git show 9cd0b3d:infra/telemetry-worker/index.js | grep -n 'handleDownload\|CF-Connecting-IP'
```

If you opted in, your daily ping now lands here. If you change your
mind, the three kill switches in
[`telemetry.md`](telemetry.md#kill-switches) all still work.

## Live aggregate

`GET /v1/stats` returns totals and 30-day daily buckets. Opt-in install counts
use `COUNT(DISTINCT install_id)`. Download clicks use simple counts because no
identifier is collected; repeated clicks count again. The endpoint never
returns raw timestamps, request metadata, or individual event rows.

## When this changes

Any change to what the Worker stores, or to the endpoint URL the
client posts to, is a contract change and lands here first — with a
new deploy SHA pinned in the table above.
