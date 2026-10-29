# Anonymous telemetry

CCC ships one **anonymous, always-on** open beacon, plus an anonymous
landing-page download-click counter and cookieless website analytics
(see "Website analytics"). This file is the trust artifact. It
describes every payload, the kill switch, and the server-side contract. If
anything in the source diverges from this file, the source is buggy — open
an issue.

**The opt-in daily ping was retired on 2026-09-28.** Earlier versions of
CCC also sent a richer, consent-gated daily ping (`POST /v1/ping`) carrying
a random `install_id`. It required clicking through a consent banner for a
number that turned out not to be worth the friction, and its
`active_seconds_today` field was broken by construction (the ping fired
just after UTC midnight, so it usually measured under an hour of the day).
This build no longer sends it, generates no new `install_id`, and shows no
consent banner. The Worker still *accepts* `/v1/ping` so installs on older
builds that haven't updated don't start failing; see
[Legacy: the opt-in daily ping](#legacy-the-opt-in-daily-ping) below.

## TL;DR

- **Anonymous open beacon: on by default, no identifier.** Fires at most
  once per UTC day. `CCC_TELEMETRY_DISABLED=1` is the only switch, and it
  kills every wire byte from this process — there's no consent step to
  bypass because the payload carries no identity.
- **Two locally-derived booleans, still no identifier.** `first_this_week`
  / `first_this_month` say "have I already beaconed this ISO week / this
  UTC month" — computed from nothing but the beacon's own last-run date.
- **Coarse geo, computed at the edge.** The Worker reads Cloudflare's
  `request.cf` and stores only an ISO-2 country code and a first-level
  region name (e.g. "California") — never city, postal code, lat/long,
  ASN, or the raw IP.
- **Maintainer self-exclusion needs no env var on every launch.** Either
  `CCC_TELEMETRY_DEV_MODE=1` or `"dev": true` in
  `~/.config/claude-command-center/telemetry.json` marks a row
  not-a-real-user.
- **Inspectable locally.** Every piece of state lives in plain text
  under `~/.config/claude-command-center/`. Read it any time.
- **Landing download click:** unrelated, unauthenticated, no request body
  or identifier; see [below](#landing-page-download-clicks).

## What is sent

The complete schema-v2 payload, in JSON, posted at most once per UTC day
to a single HTTPS endpoint:

```json
{
  "schema_version": 2,
  "version": "5.24.0",
  "platform": "darwin",
  "first_this_week": true,
  "first_this_month": false
}
```

| field               | type   | example   | source                                                                                     |
| ------------------- | ------ | --------- | -------------------------------------------------------------------------------------------- |
| `schema_version`    | int    | `2`       | constant in `ccc_server/fleet_jobs.py`; the Worker still accepts `1` (no week/month fields) |
| `version`           | semver | `5.24.0`  | `__version__` from `server.py`                                                              |
| `platform`          | string | `darwin`  | `sys.platform`                                                                              |
| `first_this_week`   | bool   | `true`    | true iff the locally stored last-beacon date falls in an earlier ISO week (UTC) than today |
| `first_this_month`  | bool   | `false`   | true iff the locally stored last-beacon date falls in an earlier UTC calendar month         |

One optional field may also be present:

| field | type | example | notes                                                                                          |
|-------|------|---------|--------------------------------------------------------------------------------------------------|
| `dev` | bool | `true`  | set when `CCC_TELEMETRY_DEV_MODE=1` **or** `telemetry.json` has `"dev": true`; marks the row "not-a-real-user" |

The HTTP request also carries:
- `User-Agent: claude-command-center/<version> (telemetry-open)`.
- `Content-Type: application/json`.

There is **no `install_id` and no field of any kind that could dedupe two
beacons from the same machine**. `first_this_week` / `first_this_month` are
computed entirely from a local date file (`telemetry-last-open`, see
[State files](#state-files)) that never leaves the host — the wire payload
is just two booleans, not the date itself.

## Why daily, and why the week/month flags

Until 2026-08-12 the open beacon fired once per server boot. That measured
restarts, not usage: an install left running under launchd for a week sent
zero beacons, while one restart-heavy machine sent dozens. A once-per-UTC-day
gate (the `telemetry-last-open` date file) makes the count mean "installs
that ran today."

Daily counts alone can't answer "how many installs were active this week"
without an identifier to dedupe repeated days — summing daily beacon counts
over 7 days over-counts anyone who ran on more than one of those days.
`first_this_week` and `first_this_month` fix that without adding an
identifier: each is true on **exactly one** beacon per install per period
(the first one), computed purely from the install's own prior beacon date.
Summing the `true` values the Worker receives in a given week or month
therefore equals the number of distinct installs active in that period —
the same guarantee an install id would give, without ever sending one.

## Coarse geo

The Worker reads Cloudflare's `request.cf` object at insert time — never
anything the client sends — and persists exactly two fields:

| field     | type          | example        | source                                              |
|-----------|---------------|----------------|------------------------------------------------------|
| `country` | ISO-2 string  | `"US"`         | `request.cf.country`, upper-cased and format-checked |
| `region`  | string, ≤64 chars | `"California"` | `request.cf.region`, format-checked                |

`request.cf` also exposes `city`, `postalCode`, `latitude`, `longitude`,
and `asn`. **None of those are read.** The sanitizer
(`sanitizeGeo` in `infra/telemetry-worker/index.js`) only ever touches
`country` and `region`, and drops either one if it doesn't match a plain
code / name shape. This is computed entirely at Cloudflare's edge from the
request's IP at the moment it arrives — the raw IP is never stored (see
[the IP hash](#the-ip-hash) below), and geo is derived from it only
transiently, in the same request, by Cloudflare's infrastructure rather
than ours.

The public stats page shows country and region breakdowns from this data,
one ISO week at a time, counted per install (not per beacon) with small
buckets suppressed (see
[`/v1/stats` aggregation](#v1stats-aggregation)) so an individual install
from a rare country or state is never singled out.

## The IP hash

The Worker computes `SHA-256(utc_date || daily_secret || source_ip)` and
stores **only** that fixed-length hash. The raw IP is never written to
disk. Because the secret rotates every UTC day, the same IP on two
different days produces two different hashes — so we (the maintainer)
**cannot link the same machine across days even with our own salt**. What
we *can* do is `COUNT(DISTINCT ip_hash)` per UTC day to answer "did today's
beacons come from 18 machines, or from 1 machine behind a changing
address."

## Maintainer dev-mode flag

Either of two independent signals marks a beacon `dev: true`, which the
Worker persists as `is_dev=1` and the public stats page reports every count
both with and without:

1. **Env var.** `CCC_TELEMETRY_DEV_MODE=1` at launch.
2. **State file.** `"dev": true` in
   `~/.config/claude-command-center/telemetry.json` (the legacy opt-in
   state file — this build only ever reads the `dev` key from it, never
   `opt_in`/`asked_at`/`endpoint`). This exists because an env var has to
   be set on *every* launch path — a LaunchAgent plist, a Dock
   double-click, a `.app` bundle — while a file written once keeps working
   across all of them.

The flag adds no identity — it only says "not-a-real-user, keep me out of
the user totals."

## Kill switch

One switch, checked at every fire: env var `CCC_TELEMETRY_DISABLED` (also
accepts `true`, `yes`, `on`, case-insensitive) before launching the server.
With this set, the beacon code path never runs — no last-open-date read, no
background thread doing anything but sleeping. This is the right knob for
corporate fleets and CI runs. There is no second, consent-based switch
because the beacon carries no identity to withdraw consent for.

## `/v1/stats` aggregation

`GET /v1/stats` is aggregate-only and additive-only — new fields are added,
existing ones are never renamed or removed. Beyond the existing totals and
30/90-day daily buckets, it now includes:

- `totals.weekly_active_installs` / `totals.weekly_active_installs_all` —
  count of beacons with `first_this_week = 1` since the current ISO week's
  Monday (UTC), without and with the maintainer's own machine.
  `totals.weekly_active_installs_prev` / `_all` is the same count for the
  last *complete* ISO week (last Monday up to, not including, this Monday).
  The boundary is exact — a trailing 7-day window would double-count any
  install that beacons both in the tail of last week and the start of this
  one, since `first_this_week` is set once per ISO week, not once per
  rolling 7 days.
- `totals.monthly_active_installs` / `_all` — same idea, scoped to the
  current UTC calendar month exactly; `totals.monthly_active_installs_prev`
  / `_all` is the previous complete calendar month.
- `geo_week` — `{week_start, countries, regions, us, intl}`, one full ISO
  week of coarse geo, counting **installs**, not beacons: only rows with
  `first_this_week = 1` are included, so an install beaconing every day
  from the same place counts once, not up to 7 times. `week_start` is the
  Monday (UTC, `YYYY-MM-DD`) of the week the data covers — the previous
  complete ISO week if it already has any qualifying rows, otherwise the
  current in-progress week (so the card isn't empty on day one, and then
  becomes stable once a full week has passed). `countries` is
  `[{country, installs}, ...]`; `regions` is
  `[{country, region, installs}, ...]` (keyed by both fields, so e.g. the
  US state of Georgia and the country of Georgia are never merged); `us`
  and `intl` are beacon-country totals over the same week. Dev rows are
  excluded from all four.

**Minimum-count suppression.** In `geo_week.countries` and
`geo_week.regions`, any bucket with fewer than 3 installs in the week is
folded into a single row with every key field set to `"other"` (so a
suppressed region can never be paired back up with a real country) instead
of being named. An install from a rare country or a small state can never
be read off the public page as "this one person is here" — and because
suppression counts distinct installs rather than beacon volume, an install
that beacons daily from a rare state can't out-beacon its way past the
threshold either.

These fields fill in gradually as the fleet updates to this build — a
v1-only beacon (pre-2026-09-28) stores `NULL` for `first_this_week` /
`first_this_month` / `country` / `region`, and is correctly excluded from
all of the above.

## What is **never** sent

This is the trust anchor. The list is closed; expanding it is a major
version bump and a documented breaking change.

- An install id or any other identifier.
- Prompt content, transcripts, conversation events, tool calls, tool
  results, file contents.
- Usage volume, message counts, per-session timing, token counts, model
  names, costs, session counts.
- Repo paths, repo names, branch names, file paths, cwd, project slug.
- User identity: name, email, hostname, username, login, IP address,
  git config, system locale.
- Errors, exception traces, stack traces, server log lines.
- City, postal code, latitude/longitude, ASN, or any geo finer than
  country + first-level region.
- Anything from the installed dashboard UI: clicks, keystrokes, searches,
  navigation, feature usage. The public landing-page click counter is the
  separate bounded surface documented below.

The server-side endpoint additionally drops the source IP **before**
logging the request. That drop happens in
[`infra/telemetry-worker/`](../infra/telemetry-worker/) — the source
ships with the rest of the repo so the guarantee is auditable. Deployment
details and the pinned source revision live in
[`docs/telemetry-public.md`](telemetry-public.md).

## Legacy: the opt-in daily ping

Retired 2026-09-28. `POST /v1/ping` is still accepted by the Worker (see
`infra/telemetry-worker/index.js`) so installs on older builds that haven't
updated yet don't start getting errors, but no CCC version built after this
date sends it, and no new `install_id` is ever generated. The stats page's
"Opt-in pings" tab is kept as a legacy view of data from before the
retirement; see [`docs/stats/index.html`](stats/index.html).

If you have an old `~/.config/claude-command-center/install-id` or
`telemetry.json` file from before the retirement, this build leaves it on
disk untouched — it is never read for anything but the still-supported
`dev` key, and it is never deleted automatically. Delete it yourself if you
want it gone; nothing depends on its presence.

## Landing-page download clicks

Clicking the public landing page's `DOWNLOAD CCC` link starts one best-effort
empty `POST /v1/download` request. The link itself points directly to GitHub's
stable DMG asset. The page never waits for telemetry, redirects through it, or
cancels native link navigation, so a blocked or unavailable Worker cannot stop
the download.

The Worker handler does not receive the request object. It therefore cannot
read the request body, source IP, User-Agent, Referer, cookies, or other request
headers. It writes exactly three bounded values:

| field | value | source |
| --- | --- | --- |
| `received_at` | UTC ISO timestamp | generated by the Worker |
| `artifact` | `ccc.dmg` | fixed Worker constant |
| `source` | `landing-hero` | fixed Worker constant |

There is no cookie, install id, identity, fingerprint, or per-browser state.
Repeated clicks count repeatedly, including automation. Public reporting calls
this metric **site download clicks**; it is not unique people, completed file
transfers, successful installations, or active users.

This event comes from the public website, not the installed CCC process, so the
app's `CCC_TELEMETRY_DISABLED` environment variable does not control it.
JavaScript disabled in the browser or a blocked Worker prevents the count while
leaving the direct DMG link functional.

## Website analytics

The public website (ccc.amirfish.ai, served from `docs/`) loads
[`docs/analytics.js`](analytics.js), which sends pageviews to a dedicated
PostHog project so we can tell which links and campaigns bring visitors.
It is configured to keep as little as possible:

- **Cookieless.** `persistence: "memory"`: no cookies, no localStorage, nothing
  survives a page load. The same person on two pages counts twice.
- **Only two events.** A `$pageview` (URL including any `utm_*` tags, referrer,
  browser/OS as PostHog derives them) and a `cta_click` with `cta_kind: "download"`
  for direct installer links or `"install_options"` for installation options,
  plus `cta_placement` (`"nav"`, `"hero"`, or `"install"`). No autocapture,
  no session replay, no surveys, no person profiles.
- **Do Not Track is honored** (`respect_dnt: true`): with DNT on, nothing is sent.
- A blocked script changes nothing about the site; every link works without it.

This is the website only. The installed CCC app never loads PostHog, and
`CCC_TELEMETRY_DISABLED` does not affect it.

## State files

All under `~/.config/claude-command-center/` (mode `0700`):

- `telemetry-last-open` — single line, the UTC date of the last
  successful beacon (YYYY-MM-DD). Mode `0600`. The daily cadence and the
  `first_this_week` / `first_this_month` flags are both derived from this
  one file; nothing about its contents ever goes on the wire beyond the
  two booleans it produces.
- `telemetry.json` — legacy state from the retired opt-in ping. Only the
  `dev` key is still read (see [Maintainer dev-mode flag](#maintainer-dev-mode-flag)).
  `opt_in` / `asked_at` / `endpoint` are read-only for
  `/api/telemetry/status`'s back-compat fields and are never written by
  this build.
- `install-id` — legacy file from the retired opt-in ping, if one exists
  from before 2026-09-28. Never written or deleted by this build; only its
  presence is reported (`install_id_present`) by `/api/telemetry/status`.

## Cadence

- Background thread starts 30s after server boot (so the dashboard
  paints first), then checks every hour.
- Sends at most once per UTC day.
- Network: 15s total timeout, 10s connect timeout, **no retries**.
  Offline / DNS-fail / non-200 → silent skip; the next hourly check
  retries because the last-open-date file wasn't updated.
- No retries on the same day. If the Worker is unreachable for 24h,
  that day's signal is simply lost — by design.

## Endpoints

- Anonymous open beacon (at most once per UTC day, no identity): `POST https://telemetry.claude-command-center.workers.dev/v1/open`.
- Legacy opt-in ping (accepted for old clients only, not sent by this build): `POST https://telemetry.claude-command-center.workers.dev/v1/ping`.
- Landing-page download click (empty body, no identity): `POST https://telemetry.claude-command-center.workers.dev/v1/download`.
- Public aggregate stats: `GET https://telemetry.claude-command-center.workers.dev/v1/stats`.
- Override: set `CCC_TELEMETRY_ENDPOINT=<url>`. Useful for staging,
  forking, or proxying through a fleet-managed collector. A value ending in
  the legacy `/v1/ping` suffix is swapped to `/v1/open`; anything else has
  `/v1/open` appended.
- The Worker source is at
  [`infra/telemetry-worker/`](../infra/telemetry-worker/).

## Implementation notes

- `server.py` is stdlib-only. Telemetry uses `urllib`, `json`, `re`,
  `pathlib`, `datetime` — nothing else. No pip dependencies at
  runtime.
- All telemetry log lines from `server.py` are tagged `[telemetry]`
  so you can grep them out:
  ```bash
  tail -f ~/Library/Logs/ccc.log | grep '\[telemetry\]'
  ```

## Reporting concerns

If you find a leak — a field being sent that's not on the list above,
a kill switch that doesn't honor its contract, or a log line that
carries identifying data — open an issue (or email per `SECURITY.md`
for anything sensitive). This file is what we promise; deviations are
bugs, not features.
