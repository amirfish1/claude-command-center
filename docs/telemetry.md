# Anonymous telemetry

CCC ships an **anonymous, opt-in, off-by-default** daily ping, a smaller
anonymous server-boot beacon, and an anonymous landing-page download-click
counter. This file is the trust artifact. It describes every payload, the kill
switches, the consent flow, and the server-side contract. If anything in the
source diverges from this file, the source is buggy — open an issue.

## TL;DR

- **Daily usage ping: OFF by default.** It sends nothing unless you click
  **Enable** on the dashboard banner (or set the opt-in flag yourself).
- **Boot beacon:** three bounded fields, once per server boot, with no install
  id. `CCC_TELEMETRY_DISABLED=1` disables both app-originated surfaces.
- **Landing download click:** no request body or identifier; the Worker stores
  only receive time plus fixed `ccc.dmg` and `landing-hero` constants.
- **Inspectable locally.** Every piece of state lives in plain text
  under `~/.config/claude-command-center/`. Read it any time.
- **App-telemetry kill switches:** env var, JSON file, or delete the install-id.
  The public-site click is best-effort browser JavaScript and can be blocked
  without affecting the direct download.

## What is sent

The complete schema-v3 payload, in JSON, posted once per UTC day to a
single HTTPS endpoint:

```json
{
  "schema_version": 3,
  "install_id": "00000000-0000-4000-8000-000000000000",
  "version": "4.9.0",
  "platform": "darwin",
  "engines": "claude,codex",
  "last_active_date": "2026-06-07",
  "sessions_today": 4,
  "active_seconds_today": 5430,
  "total_sessions_managed": 287
}
```

| field                    | type   | example                            | source                                                                                          |
| ------------------------ | ------ | ---------------------------------- | ----------------------------------------------------------------------------------------------- |
| `schema_version`         | int    | `3`                                | constant in `server.py`; v1 + v2 are still accepted server-side with missing fields stored NULL |
| `install_id`             | uuidv4 | random                             | generated locally on first opt-in; never derived from machine identity                          |
| `version`                | semver | `4.9.0`                            | `__version__` from `server.py`                                                                  |
| `platform`               | string | `darwin` / `linux`                 | `sys.platform`                                                                                  |
| `engines`                | string | `claude,codex,cursor,antigravity`  | which of {claude, codex, gemini, cursor, antigravity} binaries are on PATH                      |
| `last_active_date`       | string | `2026-06-07` (or `""`)             | newest `~/.claude/projects/**/*.jsonl` mtime, **date only**                                     |
| `sessions_today`         | int    | `4`                                | count of `*.jsonl` files with mtime in the last 24h; capped at 100000                           |
| `active_seconds_today`   | int    | `5430`                             | sum of dashboard-tab-visible time today (rounded to 30s ticks); capped at 86400                 |
| `total_sessions_managed` | int    | `287`                              | lifetime count of `*.jsonl` files ever seen under `~/.claude/projects/`; capped at 10000000     |

The HTTP request also carries:
- `User-Agent: claude-command-center/<version> (telemetry)`.
- `Content-Type: application/json`.

That's it for the opt-in daily ping. See the next section for the
separate, smaller, anonymous open beacon.

## Anonymous open beacon

Schema v2 introduced one additional endpoint — `POST /v1/open` — that
fires **once per server boot**, with the following 3-field body and
**nothing else**:

```json
{
  "schema_version": 1,
  "version": "4.9.0",
  "platform": "darwin"
}
```

This beacon is **not** gated on the opt-in switch because it carries
**no `install_id`, no identifier of any kind**, and no engine list.
The aggregate it produces is "how many distinct CCC server boots
happened on a given UTC day"; an individual boot cannot be linked back
to anything else the same machine sends or to any prior boot from the
same machine.

It is still gated on the `CCC_TELEMETRY_DISABLED` env var — that single
switch is the user's guarantee that no bytes leave the host from this
process, regardless of opt-in state.

The Worker computes `SHA-256(utc_date || daily_secret || source_ip)`
and stores **only** that fixed-length hash. The raw IP is never written
to disk. Because the secret rotates every UTC day, the same IP on two
different days produces two different hashes — so we (the maintainer)
**cannot link the same machine across days even with our own salt**.
What we *can* do is `COUNT(DISTINCT ip_hash)` per UTC day to answer
"is today's boot count from 1 machine restarting 18 times, or 18
machines restarting once each." That's the only signal the hash
provides; everything else is still aggregate.

If you are uneasy about the hashed IP despite it being daily-rotated
and un-reversible without the server secret, the same env var still
kills the beacon entirely.

**Maintainer dev-mode flag.** If `CCC_TELEMETRY_DEV_MODE=1` is set,
the beacon adds a `dev: true` field that the worker persists as an
`is_dev=1` marker on the row. Public stats then filter these rows
out so the maintainer's own frequent restarts do not inflate the
boot / distinct-IP counts on the public page. The flag adds no
identity — it only says "not-a-real-user, exclude from the totals."

If you are uneasy about the beacon despite it carrying no identity,
set `CCC_TELEMETRY_DISABLED=1` before launching `server.py` / the
`.app` / `./run.sh`; it kills both the daily ping and the boot beacon.

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

## What is **never** sent

This is the trust anchor. The list is closed; expanding it is a major
version bump and a documented breaking change.

- Prompt content, transcripts, conversation events, tool calls, tool
  results, file contents.
- Usage volume, message counts, per-session timing, token counts, model
  names, costs. (Schema v2 added a single `sessions_today` integer — a
  count of `*.jsonl` files modified in the last 24h. Schema v3 added
  `active_seconds_today` — a coarse 30s-granularity sum of how long the
  dashboard tab was visible today — and `total_sessions_managed`, the
  lifetime count of JSONL files ever seen on disk. Those four numbers
  are the only usage-shaped fields; everything else in this row remains
  off-limits.)
- Repo paths, repo names, branch names, file paths, cwd, project slug.
- User identity: name, email, hostname, username, login, IP address,
  git config, system locale.
- Errors, exception traces, stack traces, server log lines.
- Anything from the installed dashboard UI: clicks, keystrokes, searches,
  navigation, feature usage. The public landing-page click counter is the
  separate bounded surface documented above.

The server-side endpoint additionally drops the source IP **before**
logging the request. That drop happens in
[`infra/telemetry-worker/`](../infra/telemetry-worker/) — the source
ships with the rest of the repo so the guarantee is auditable. Deployment
details and the pinned source revision live in
[`docs/telemetry-public.md`](telemetry-public.md).

## Kill switches

Three independent layers, in order of precedence. Any one wins.

1. **Env var.** Set `CCC_TELEMETRY_DISABLED=1` (also accepts `true`,
   `yes`, `on`, case-insensitive) before launching the server. With this
   set, the telemetry code path never runs — no install-id read, no
   dashboard bar, no background thread. This is the right knob for
   corporate fleets and CI runs.

2. **JSON file.** Open `~/.config/claude-command-center/telemetry.json`
   and set `"opt_in": false`. This is what the **Skip forever** button
   writes. The background thread reads this on every check (default once
   per hour).

3. **Delete the install-id.** Remove
   `~/.config/claude-command-center/install-id`. Without an id the
   payload can't be assembled and the ping is skipped; the dashboard
   bar will also re-appear on the next reload so you can confirm a
   fresh decision.

## State files

All under `~/.config/claude-command-center/` (mode `0700`):

- `install-id` — single line, a random UUIDv4 plus a newline. Mode `0600`.
  Generated only when the user opts in (or when this file is missing and
  a fresh opt-in is recorded). Cannot be reconstructed from machine
  identity.
- `telemetry.json` — opt-in state. Mode `0600`.
  ```json
  {
    "opt_in": true,
    "asked_at": "2026-05-19T14:23:01+00:00",
    "endpoint": "https://telemetry.claude-command-center.workers.dev/v1/ping"
  }
  ```
  `opt_in` is one of `null` (never asked), `true`, or `false`.
- `telemetry-last-ping` — single line, the UTC date of the last
  successful ping (YYYY-MM-DD). Mode `0600`. The daily cadence is
  enforced strictly from this file: if today's date is not strictly
  greater than the recorded date, no ping is sent.

## Cadence

- Background thread starts 30s after server boot (so the dashboard
  paints first), then checks every hour.
- Sends at most once per UTC day.
- Network: 15s total timeout, 10s connect timeout, **no retries**.
  Offline / DNS-fail / non-200 → silent skip; the next hourly check
  retries because the last-ping file wasn't updated.
- No retries on the same day. If the Worker is unreachable for 24h,
  that day's signal is simply lost — by design.

## Endpoints

- Daily opt-in ping: `POST https://telemetry.claude-command-center.workers.dev/v1/ping`.
- Anonymous open beacon (once per boot, no identity): `POST https://telemetry.claude-command-center.workers.dev/v1/open`.
- Landing-page download click (empty body, no identity): `POST https://telemetry.claude-command-center.workers.dev/v1/download`.
- Override: set `CCC_TELEMETRY_ENDPOINT=<url>`. The override is applied
  to **both** endpoints (`/v1/ping` is replaced with `/v1/open` for the
  beacon URL). Useful for staging, forking, or proxying through a
  fleet-managed collector.
- The Worker source is at
  [`infra/telemetry-worker/`](../infra/telemetry-worker/).

## Consent UX

The first time you open the dashboard with the server installed, a
small horizontal banner appears above the toolbar:

> _Help the maintainer know CCC is being used? Anonymous daily ping, 5
> fields, off by default._
>
> [Enable] [Skip forever] [What gets sent?]

- **Enable** → writes `opt_in: true` and generates the install-id.
  The first ping fires within the hour.
- **Skip forever** → writes `opt_in: false`. The bar never appears
  again (you can still flip the switch from a future Settings menu
  entry).
- **What gets sent?** → opens this file in a new tab.

Any of the three buttons dismisses the bar. The bar is `null`-state
only — once you've made a choice it never reappears unless you delete
the install-id.

## Implementation notes

- `server.py` is stdlib-only. Telemetry uses `urllib`, `json`, `uuid`,
  `pathlib`, `datetime` — nothing else. No pip dependencies at
  runtime.
- All telemetry log lines from `server.py` are tagged `[telemetry]`
  so you can grep them out:
  ```bash
  tail -f ~/Library/Logs/ccc.log | grep '\[telemetry\]'
  ```
- The endpoint URL the server will use is recorded in `telemetry.json`
  at opt-in time so a code-side endpoint change is visible in plain
  text on disk.

## Reporting concerns

If you find a leak — a field being sent that's not on the list above,
a kill switch that doesn't honor its contract, or a log line that
carries identifying data — open an issue (or email per `SECURITY.md`
for anything sensitive). This file is what we promise; deviations are
bugs, not features.
