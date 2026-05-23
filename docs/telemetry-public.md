# Public telemetry aggregates

This page is the planned home for the quarterly aggregate report —
install counts by version, platform mix, engine mix, and active-day
distribution. The Worker is live; the first aggregate publishes at
the next quarterly boundary once there is enough signal to be worth
reading.

## Status: collection live

| | |
| --- | --- |
| Endpoint | `https://telemetry.claude-command-center.workers.dev/v1/ping` |
| Worker source SHA at deploy | [`064eb11`](https://github.com/amirfish1/claude-command-center/tree/064eb11/infra/telemetry-worker) |
| Worker deployed | 2026-05-22 |
| Collection started | 2026-05-22 |
| Storage | Cloudflare D1 (`ccc-telemetry`), columns exactly as documented in [`telemetry.md`](telemetry.md) — no IP, no User-Agent, no derived fields |

The IP-drop guarantee is in
[`infra/telemetry-worker/index.js`](../infra/telemetry-worker/index.js)
at the pinned SHA above: the Worker reads `CF-Connecting-IP` only to
discard it, and the `pings` D1 table has no column for it. Re-check
any time:

```bash
git show 064eb11:infra/telemetry-worker/index.js | grep -n CF-Connecting-IP
```

If you opted in, your daily ping now lands here. If you change your
mind, the three kill switches in
[`telemetry.md`](telemetry.md#kill-switches) all still work.

## First aggregate

Not yet published. The aggregate query in
[`infra/telemetry-worker/README.md`](
../infra/telemetry-worker/README.md) runs on a 90-day window and
collapses every per-install row through `COUNT(DISTINCT install_id)`,
so the page will show install counts, never individual rows.

Target cadence: quarterly. The first publish will include:

- Install counts by date / version / platform.
- A link to the raw aggregate JSON.
- The exact SQL that produced it (copy-pasteable, so anyone can
  verify the shape matches what's promised here).

## When this changes

Any change to what the Worker stores, or to the endpoint URL the
client posts to, is a contract change and lands here first — with a
new deploy SHA pinned in the table above.
