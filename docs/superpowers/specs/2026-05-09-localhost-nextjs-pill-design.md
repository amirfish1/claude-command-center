# Localhost / Next.js dev server pill — design

## Problem

The Vercel pill in the topbar (`#deployPill`) gives a one-glance view of the
remote deployment for the currently-selected repo. There's no equivalent for
local development. When a Claude Code session is working on a Next.js app the
user often wants to:

1. Know whether a `next dev` server is already running for this repo.
2. Start one if not (without opening a terminal).
3. Jump to the running URL in a browser tab.

## Solution

Add a sibling pill (`#localhostPill`) immediately after the Vercel pill that
encapsulates detect → start → open for Next.js dev servers, scoped per repo.

## States

| State                      | Label                | Dot     | Click action                                |
| -------------------------- | -------------------- | ------- | ------------------------------------------- |
| Repo not selected          | `localhost`          | none    | tooltip nudges to pick a repo               |
| Not a Next.js project      | `No Next.js`         | gray    | tooltip explains detection                  |
| Detected, not running      | `▶ Start localhost`  | gray    | POST `/api/nextjs/start`                    |
| Starting                   | `Starting…`          | yellow  | nothing (busy)                              |
| Running                    | `localhost:<port>`   | green   | opens `http://localhost:<port>` in new tab  |
| Failed to start            | `Start failed`       | red     | tooltip shows tail of log; click retries    |

A right-click on the pill in the running state offers "Stop dev server" —
hits `POST /api/nextjs/stop`. Right-click rather than a separate button to
keep the topbar tight.

## Backend (server.py)

### Detection

```python
def _detect_nextjs(repo_path: Path) -> bool:
    # truthy if package.json has "next" in deps/devDeps OR
    # next.config.{js,mjs,ts,cjs} exists at repo root.
```

### Process tracking

Module-level: `_NEXTJS_PROCS: dict[str, dict] = {}` keyed by canonical
`repo_path`. Value: `{"pid", "port", "started_at", "log_path", "cmd"}`.

A small lock guards reads/writes (multiple HTTP threads).

### Endpoints

- `GET /api/nextjs/status?repo_path=...`
  - Returns `{detected, running, pid, port, log_path, started_at}`.
  - If a tracked pid is no longer alive, evicts the entry and reports
    `running: false`.

- `POST /api/nextjs/start` (same-origin enforced via `_check_same_origin`)
  - 409 if a server is already running for this repo.
  - Picks the package manager: `pnpm-lock.yaml` → `pnpm dev`,
    `yarn.lock` → `yarn dev`, else `npm run dev`.
  - Spawns via `subprocess.Popen(cmd, cwd=repo_path, stdout=log, stderr=log,
    start_new_session=True)`.
  - Spawns a daemon thread that tails the log for `Local:\s+http://[^:]+:(\d+)`
    or `ready - started server on .* port (\d+)`. When matched, stores port.
  - Returns immediately with `{ok, pid, log_path}`. Client polls
    `/api/nextjs/status` to learn the port.

- `POST /api/nextjs/stop`
  - SIGTERM the process group, wait up to 3s, then SIGKILL.
  - Evicts the entry.

### Shutdown hook

Register an `atexit` handler that SIGTERMs every tracked Popen so the
processes don't survive a `Ctrl-C` of `run.sh`.

## Frontend

### Markup (static/index.html, after line 420)

```html
<a class="topbar-btn sh-btn-deploy" id="localhostPill" target="_blank"
   rel="noopener" title="localhost — loading…" aria-label="localhost dev server">
  <span class="deploy-dot" id="localhostDot"></span>
  <span class="deploy-label" id="localhostLabel">localhost</span>
</a>
```

### Polling (static/app.js)

`pollLocalhost()` invoked from the same boot path as `pollVercelDeploy`:
on initial load, on repo switch, every 15s. Updates pill label/title/href
based on the status response. Reuses existing `.deploy-dot` color classes
(`ready`, `building`, `error`).

Click handler:
- If state is "Detected, not running" → POST `/api/nextjs/start`, show
  "Starting…", then resume polling (faster cadence: 1s for the next 30s).
- If state is "Running" → default `<a>` behavior opens the URL.
- Right-click in running state → tiny menu with "Stop dev server".

## Security

- All write endpoints (`/api/nextjs/start`, `/api/nextjs/stop`) require
  same-origin POST via existing `_check_same_origin`.
- `repo_path` resolved through existing `resolve_repo_path` (clamps to
  known repos).
- Subprocess started with no shell, fixed argv (`["npm","run","dev"]` etc.) —
  no command injection vector.

## Out of scope

- Multi-port / multi-server per repo (only one tracked).
- Non–Next.js dev servers (Vite, Astro, plain `node server.js`). The
  detection + start logic could later be generalized; for now, pill is
  Next.js–specific so behavior is predictable.
- HTTPS via `--experimental-https`. Default is HTTP (matches `npm run dev`).

## Testing

- `tests/test_smoke.py` already imports `server.py`; adding endpoints
  shouldn't break it. No new tests required by repo bar.
- Manual: open the UI on a Next.js repo, confirm pill cycles through
  Start → Starting → localhost:3000; click opens in browser; right-click
  stops; pill returns to Start.

## Files

- `server.py` — `_detect_nextjs`, `_NEXTJS_PROCS`, three endpoints, atexit hook
- `static/index.html` — pill markup
- `static/app.js` — `pollLocalhost`, click + contextmenu handlers
- `static/app.css` — only if existing dot states need a new color
- `changelog.d/added-localhost-nextjs-pill-2026-05-09.md`
