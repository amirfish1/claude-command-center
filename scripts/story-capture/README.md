# story-capture — screenshot & video harness for the product story

Repeatable captures of the real CCC UI running on the seeded, privacy-safe
demo fixtures (`docs/demo/api/*.json` — 16 fake sessions, 3 fake repos, fake
issues). Produces crisp 2x stills and cursor-led H.264 MP4 clips with posters.

## Serve the app first

Serve the **repo root** (not `docs/`) so both targets are reachable:

```bash
python3 -m http.server 8877 --directory /path/to/claude-command-center
```

Two capture targets:

| Target | URL path | What it is |
|---|---|---|
| **Current UI + demo fixtures** (default for story assets) | `/static/index.html?demo=1` + `--fixture-base /docs/demo/api` | Today's `static/` bundle; `installDemoMode()` in app.js serves every `/api/*` GET from the fixture JSONs and stubs POSTs with `{ok:true, demo:true}`. |
| **Published demo bundle** | `/docs/demo/` | The frozen GH-Pages snapshot (older app.js copy). Its index.html/app.js are version-skewed: board/flow views don't render there. Use for parity checks only. |

Base URL defaults to `http://127.0.0.1:8877` (override with `CAP_BASE` or `--base`).

## Screenshots — `shot.js`

```bash
node scripts/story-capture/shot.js \
  --flow scripts/story-capture/flows/overview.js \
  --out docs/product-story/assets/shots/S-OVR.png --scale 2
```

Defaults: viewport `1440x900`, `deviceScaleFactor 2` (=> 2880x1800 px).
Options: `--url <path>`, `--fixture-base <path>`, `--ls <seed.json>`
(localStorage seeded before app scripts run), `--flow <module>` (run a flow
before capture), `--actions '<json>'` (inline steps: `wait`, `waitFor`,
`click`, `move`, `drag`, `scroll`, `type`), `--viewport WxH`, `--scale N`,
`--settle ms`, `--wait ms`, `--full-page`, `--keep-banner`, `--cursor`.

## Videos — `record.js`

```bash
node scripts/story-capture/record.js \
  --flow scripts/story-capture/flows/kanban-drag.js \
  --out docs/product-story/assets/video/V-06-kanban-drag.mp4 \
  --poster docs/product-story/assets/video/posters/V-06.png
```

Records `page.screencast()` (WebM/VP9) then transcodes with ffmpeg to MP4
(H.264, yuv420p, `+faststart`, no audio) and extracts a poster PNG.
**ffmpeg is required** — both for the transcode and by puppeteer's screencast
encoder itself. Video is 1440x900 @1x, 30 fps (stills use 2x; 2x video is
needlessly heavy). Options: `--viewport`, `--fps`, `--crf`, `--lead ms`,
`--tail ms`, `--poster-at s`, `--keep-webm`, `--ls`, `--fixture-base`.

A synthetic ~20px pointer (dark, soft shadow) is injected and animated with
easing in sync with real `page.mouse` events; clicks show a ripple; drags
show a grab/press state.

## Flows

A flow is a small JS module in `flows/`:

```js
module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  viewport: '1440x900',            // optional
  localStorage: { ... },           // seeded before app scripts run
  lead: 1500, tail: 1700,          // establish / settle ms (record.js)
  async run(ctx) { ... },
};
```

`ctx` helpers: `move(selOrPt, {duration})`, `click(selOrPt)`,
`dragHTML5(fromSel, toSel, {duration, hold, via})` (synthetic HTML5 DnD —
puppeteer's mouse can't start native drags; `via` routes through a waypoint
so drag-revealed drop zones appear), `pressDrag(from, to)` (pointer-event
drags: Flow nodes, resizers), `scrollEl(sel, dy, {dx})`, `type(sel, text)`,
`findByText(scopeSel, text)`, `waitFor(sel)`, `pause(ms)`, `eval(fn)`,
`suppressBanner()`, `allowBanner()`, and
`reloadWith(lsEntries)` — a fade-out scene cut that reloads with different
localStorage (used where the UI has no click affordance for a persisted
preference, e.g. list -> board view).

## Seeds (`seeds/*.json`, mirrored in `flows/_seeds.js`)

- `clean.json` — suppress first-run chrome: What's New modal
  (`ccc-last-seen-version: "demo"` matches the version fixture), PWA install
  card, telemetry bar.
- `board.json` — clean + kanban board view (`ccc-session-view: board`),
  sidebar widened to 1020px, right utilities rail collapsed.

Useful extra keys: `ccc-sidebar-width`, `ccc-status-rail-collapsed`,
`ccc-flow-zoom`, `ccc-column-overrides`.

## Gotchas (hard-won)

- CCC polls forever: never wait on `networkidle2`; the harness uses
  `waitForNetworkIdle({idleTime, timeout})` with a bounded timeout.
- localStorage must be seeded via `evaluateOnNewDocument` (before app
  scripts). Those scripts re-run on every navigation — `reloadWith`
  registers its overrides as a *second* on-new-document script so they win.
- Kanban card moves are client-side in demo mode (`columnOverrides` +
  localStorage) — drags genuinely work and persist.
- The search box shows a one-time "Build a history index?" popover when the
  history fixture reports no index; pre-set
  `window._historyIndexStatus = {exists: true}` before typing.
- Chrome for Testing v149 crashes on screenshot on macOS ARM; the harness
  prefers installed Chrome/Chrome Beta (same as snapshot.js, OPS-4).
