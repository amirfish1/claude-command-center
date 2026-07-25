# Telemetry Worker

Minimal Cloudflare Worker for CCC's bounded daily ping, anonymous boot beacon,
landing-page download-click counter, and public aggregate stats. See
[`docs/telemetry.md`](../../docs/telemetry.md) for the full contract. The source
lives here so every persisted value is auditable.

## What it does

- Accepts `POST /v1/ping` with a JSON body matching the documented
  5-field schema (plus `schema_version`).
- Accepts `POST /v1/open` with the documented anonymous boot payload.
- Accepts an empty `POST /v1/download`; it never receives the request object
  and writes only receive time, `ccc.dmg`, and `landing-hero`.
- Serves aggregate-only `GET /v1/stats`, including site download clicks.
- Drops any unknown fields silently. Rejects requests where the listed
  fields fail type validation.
- **Drops the source IP** before writing anywhere durable. The Worker
  reads `request.headers.get("CF-Connecting-IP")` only to ignore it.
- Appends a row to a Cloudflare D1 table (or KV, depending on what we
  end up provisioning at deploy time).
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
wrangler deploy
```

The committed `wrangler.toml` binds the public D1 database. Database ids are
resource identifiers, not credentials; authentication remains in Wrangler's
local account configuration.

## Aggregating

The aggregate query that drives the public page (target: quarterly):

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

No per-install rows are ever published. The Worker stores
`install_id` to support **deduplication only** (so a chatty client
can't inflate "installs"); aggregates always go through `COUNT
(DISTINCT install_id)`.

## Why this lives in the same repo

The Worker's privacy guarantees are only worth what the source backs
up. Keeping the code beside `server.py` means anyone auditing the
client can audit the server in one `git clone`. If we ever split this
out, the split itself is a breaking change to the trust contract and
should be documented in `telemetry-public.md` first.
