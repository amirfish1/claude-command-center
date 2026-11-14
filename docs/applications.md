# Applications rail

The column on the left edge of every CCC page is the Applications rail. It
replaced the old Queues/Sessions toggle: each surface is an app, and your own
apps sit next to the built-in ones.

Built-ins are Sessions (`/`) and Queues (`/q2.html`). Everything else is yours.

## Adding an app

The quickest way is the **+** just below the last app in the rail. It opens the
Applications panel over whatever you were looking at — name it, pick an icon,
say whether it is a web page, something running on a port, or an empty app to
build into. Drag rows to reorder, toggle to hide, `×` to remove.

Everything below is what that panel writes for you, for when you would rather
do it by hand or have an agent do it.

An app is a label, an icon, and a URL. There are two ways to add one, and you
can mix them.

### Drop in a folder (discovered automatically)

```
~/.claude/command-center/apps/
└── my-app/
    ├── app.json
    └── index.html
```

`app.json`:

```json
{ "label": "My App", "icon": "🛠" }
```

That is the whole contract. CCC serves `index.html` at `/view/my-app` and the
rail picks it up on the next page load. Assets in the folder are served from
`/app-static/my-app/<file>` (`.css`, `.js`, `.svg`, `.json`, and common image
types).

Apps live under `~/.claude/command-center/` rather than in the repo so a
reinstall, a `git pull`, or a `git clean` never touches them.

| `app.json` field | Required | Notes |
|---|---|---|
| `label` | yes | Shown under the icon. Trimmed to 24 characters. |
| `icon` | no | An emoji or a short string. Defaults to a blue circle. |
| `url` | no | Defaults to `/view/<folder>`. Set it to point the rail somewhere else. |

The folder name is the app id and must match `[a-z0-9_-]{1,40}`.

### Declare it in `custom-links.json`

For an app that is only a URL — a dashboard you already run, or a remote page —
skip the folder and add an entry to
`~/.claude/command-center/custom-links.json`:

```json
{
  "apps": [
    { "id": "hub", "label": "Hub", "icon": "🏠", "url": "https://example.com" }
  ]
}
```

Entries here also override a discovered app with the same `id`, which is how
you rename, re-icon, or reorder an installed app without editing its folder.

Only same-origin paths (`/…`) and `http(s)` URLs are accepted.

Apps always open **inside the CCC window**. An absolute URL is framed by
`/app/<id>`, which shows the host and an "Open in browser" link in case the
site refuses to be embedded (many sites set `X-Frame-Options`); if the frame
has not loaded after a few seconds, that link becomes the whole page.

## What an app can do

An app served from `/view/<id>` is **same-origin**, so it can call any `/api/*`
route with no extra setup. That is how the Queues page works — it is a separate
page that calls `/api/queue/*`, `/api/ux-fixes/*`, and `/api/wt/*`.

This is also why CCC has no app store. A third-party app installed this way
would run with your full CCC authority: your sessions, your queues, your repo
paths, and the ability to spawn agents. Only install apps you wrote or have
read.

## An app with an agent and routines

Add one line to the app's `index.html` and it gets the standard app shell:

```html
<main data-ccc-content> ...your page... </main>
<script src="/static/app-kit.js" data-app="my-app" defer></script>
```

The kit adds two things:

- **One agent, on the right.** A resizable pane holding the app's builder
  agent: a normal session started in the app's folder the first time you press
  "Start the agent". Ask it to change the page or add a routine.
- **Routines.** One card per routine with its schedule, its last result, an
  on/off switch and "Run now", plus a box that asks the agent to build a new
  one. Click a card to see its last 10 runs and edit its name, icon and
  schedule or delete it. Put `data-ccc-routines` on the element that should hold the cards;
  otherwise they go at the end of the page.

A routine is a named, scheduled job declared in `app.json`:

```json
{
  "label": "Trips",
  "icon": "🧳",
  "tz": "America/Los_Angeles",
  "routines": [
    {"id": "booking-reminder", "name": "Booking Reminder", "icon": "🛎",
     "does": "Warns before a booking stops being refundable.",
     "schedule": "daily 08:00", "kind": "script",
     "run": ["python3", "booking_reminder.py"]},
    {"id": "price-watch", "name": "Price Watch", "icon": "🔎",
     "does": "Checks whether prices dropped.",
     "schedule": "every 12h", "kind": "agent",
     "prompt": "Look up today's prices, compare with prices.json, update it."}
  ]
}
```

- `id`: lowercase letters, digits, `-` and `_`.
- `schedule`: `daily HH:MM` (in the app's `tz`, else the machine's local time)
  or `every N` followed by `m`, `h` or `d` (five minutes at the least). A daily
  slot missed while CCC was not running runs once when it comes back. Leave it
  out for a routine that only runs from "Run now".
- `kind: "script"`: `run` is a command as a list of arguments (never a shell
  string), started in the app's folder. The last line it prints may be JSON:
  `{"summary": "one sentence", "alert": true, "notify": {"title": "...", "body": "..."}}`.
  `alert` turns the card red and sends a notification; `notify` alone sends one
  without the alert. A plain last line becomes the summary. A non-zero exit
  shows as failed and notifies. Scripts stop after five minutes.
- `kind: "agent"`: each run starts a fresh session in the app's folder with
  `prompt`. CCC tells it how to report its one-sentence result, and the card
  shows "See its work" to open that session.

Pick a script when the check is mechanical (dates, thresholds, a known API)
and an agent when the run needs judgement or looking things up. CCC keeps what
it learns (on/off, last result, the app's agent) in `ccc-state.json` in the
app's folder; `app.json` stays yours.

Endpoints: `GET /api/apps/routines?app=<id>` (omit `app` for a per-app
summary), `POST /api/apps/routines/run` and `/toggle` with `{app, id}`,
`POST /api/apps/routines/report` with `{app, id, summary, alert}`,
`POST /api/apps/routines/update` with `{app, id, name?, icon?, schedule?}`
(rewrites only that routine in `app.json`; the schedule must parse),
`POST /api/apps/routines/delete` with `{app, id}`, and
`POST /api/apps/agent` with `{app}`.

### Redrawing when a file changes

The agent and the routines change the app's files while its page is open.
`app-kit.js` fires `ccc:app-changed` on `window` within about ten seconds, so
the page can redraw itself instead of offering a refresh button:

```js
window.addEventListener('ccc:app-changed', e => {
  if (e.detail.page) location.reload();   // index.html itself changed
  else loadData();                        // another top-level file changed
});
```

Only files directly in the app folder count. CCC's `ccc-state.json` and the
page's own saves under `data/` do not fire it.

### Load time: one second to fully ready

An app page is **fully ready** when its own content is drawn and, if it has an
agent, the agent pane is showing the conversation. The budget is one second
from the start of the page's navigation. `app-kit.js` measures every load and
reports it, so a slow app shows up without anyone timing it by hand.

- **Read it.** `ccc app speed` prints loads, typical, slow (p95) and worst
  times per app for the last 24 hours against the budget (`ccc app speed <id>`
  for one app, `--hours N` for a longer window, `--check` to exit 2 when any
  load went over). The same numbers are in `GET /api/perf/summary` under
  `apps` and `kinds.app_ready`.
- **Change the budget.** `CCC_PERF_APP_READY_MS` (default `1000`).
- **Freezes are recorded too.** While an app page is in front, the kit notices
  any stretch of a second or more in which the page could not respond and
  reports it. `ccc app speed` counts the ones of two seconds or more
  (`CCC_PERF_APP_FREEZE_MS`) as freezes and shows the longest.
- **Tell the kit when your content is drawn.** By default content counts as
  drawn at the first frame after `DOMContentLoaded`. A page that fetches its
  data first should say so itself, or its number is flattering:

  ```html
  <script src="/static/app-kit.js" data-app="trips" data-ready="event"></script>
  ```
  ```js
  // once the data is on screen; cached = drawn from a saved copy
  window.cccAppContentReady = {ms: performance.now(), cached: false};
  window.dispatchEvent(new Event('ccc:app-ready'));
  ```

  When both halves are in, the kit sets `window.cccAppReady`
  (`{app, ms, content_ms, agent_ms, cached, framed}`) and fires
  `ccc:app-fully-ready` on `window`. Loads that happened in a hidden tab are
  not reported.

What keeps a page inside the budget:

- **Draw from a saved copy first.** Keep the last data you drew in
  `localStorage`, draw it immediately, then fetch and redraw only if something
  changed. This is what takes a table from seconds to under 100 ms.
- **Fetch each file once.** Do not let several sections read the same JSON.
- **Draw what is on screen.** Build folded or below-the-fold sections when
  they are opened.

CCC does its part: the agent pane loads a trimmed conversation view
(`embed=app`) that skips the dashboard's sidebar, settings and status reads;
the kit reopens the agent it showed last time instead of waiting to be told
which one it is; the dashboard keeps the app you used last loaded in the
background, so opening it or switching back to it is instant; and a page
opened straight on an app starts loading the app before the dashboard behind
it. `ccc:app-shown` fires on `window` when a kept-alive page is shown again.

### Alerts outside the dashboard

Routine notifications can also go to your phone. Create
`~/.claude/command-center/app-channels.json`; nothing is on until you turn it on:

```json
{ "whatsapp": { "enabled": true, "dry_run": true,
                "chat_id": "15551234567@s.whatsapp.net" } }
```

- `whatsapp` messages **only your own chat** through a local WhatsApp bridge
  (`bridge`, default `http://127.0.0.1:3000`). Groups and broadcasts are
  refused. `dry_run` defaults to on: nothing is sent until you set it to `false`.
- A channel that fails is skipped; the routine and the dashboard
  notification are never affected.

### A findings feed for research bots

For an app whose routines look things up, add a feed: a timeline of cards,
newest first, with an image when there is one. It refreshes on its own.

```html
<div data-ccc-feed></div>
<script src="/static/app-feed.js" data-app="my-app" defer></script>
```

Routines append a card with `POST /api/apps/feed`:

```bash
curl -s -X POST http://127.0.0.1:8090/api/apps/feed -H 'Content-Type: application/json' \
  -d '{"app":"my-app","title":"Price dropped","body":"Now $212.","url":"https://example.com/x","image":"https://example.com/x.jpg","routine":"price-watch"}'
```

Only `app` and `title` are required. `url` and `image` must be `http(s)` (an
image may also be a `/app-static/...` path). The cards live in `feed.json` in
the app's folder: the newest 200 are kept, writes are atomic. `GET
/api/apps/feed?app=<id>` returns them.

### Start from a template

Four ready-made apps, each with its own agent and a routine:

| Template | What it does |
|---|---|
| `tracker` | Dates and deadlines, closest first. A script routine warns you each morning before one arrives. |
| `research` | An agent routine researches the question in `question.json` on a schedule and appends findings to `feed.json`. |
| `watch` | An agent routine checks the prices or pages in `watches.json` and alerts you when one changes. |
| `blank` | An empty page and a sample routine to build on. |

`GET /api/apps/templates` lists them. `POST /api/apps/create` with
`{"template": "tracker", "label": "Taxes", "icon": "💸", "id": "taxes"}` copies
one into your apps folder and returns `{"ok": true, "id": "taxes"}`. Only
`template` is required; `id` defaults to a slug of the label (a taken id gets
`-2`). The new app lands at the end of the rail. Templates live in
`app_templates/`; add a folder there with an `app.json` and it shows up.
`{{label}}`, `{{icon}}` and `{{app_id}}` in a template's HTML are filled in.

## Sharing an app

An app is a folder, so sharing one means handing over the folder: put it in a
git repository (the folder itself, or one folder inside the repository) and
the other person installs it with one command.

```bash
ccc app install owner/repo            # GitHub shorthand, a git URL, or a local folder
ccc app install owner/repo --focus    # and make it the only thing on screen
```

The app is copied into `~/.claude/command-center/apps/<id>`; the id is the
folder name, or the repository name without a leading `ccc-` (`--id` picks
another). Another machine's state (`ccc-state.json`, `.venv`, `.git`,
`.claude`) is never copied. An app that is already installed is never
overwritten: once installed it belongs to its owner, who reshapes it with the
agent.

For someone who has neither CCC nor the app, the installer takes the app too
and opens CCC locked to it:

```bash
curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | CCC_APP=owner/repo bash
```

The warning above still applies: a shared app runs with the installer's full
CCC authority, and its setup routine runs a command on their machine. Share
apps with people who trust you, and install only from people you trust.

Three `app.json` fields make a shared app easy for someone who has never seen
CCC:

- **A setup routine.** Add `"setup": true` to a script routine and leave out
  its schedule. CCC runs it once, the first time it sees the app, then only
  from "Run now". Use it to install what the app needs into the app's own
  folder. If it fails, the card shows the last line it printed.
- **Suggestions.** `"suggestions": [{"label": "Record a booking", "prompt": "I
  booked a place. Record it: "}]` shows "Try asking" chips above the agent.
  Clicking one puts the prompt in the message box for the person to finish and
  send. Up to eight; a plain string is both label and prompt.
- **Instructions for the agent.** A `CLAUDE.md` (or `AGENTS.md`) in the app
  folder tells a fresh agent what each file is and how to make the common
  changes. The agent on the other machine has none of your session history.

### Focus: one app on screen

`ccc app focus <id>` locks the rail to one app: the rail shows that app and a
"More" button, and opening CCC lands on the app instead of Sessions. "More"
asks once, then shows everything; a "Just <app>" button in the rail goes back.
`ccc app unlock` does the same from a terminal (`--forget` also removes the
button).

Focus hides nothing behind a permission. It decides what the rail shows and
where a bare `/` lands; every page still answers at its own URL. It is stored
as `focus_app` and `focus_home` in `custom-links.json`. `GET /api/apps` returns
it as `focus`, and `POST /api/apps/focus` with `{"app": "<id>"}` sets it
(`{"app": ""}` unlocks, adding `"forget": true` drops the button too).

## Embedding a dashboard that runs on another port

A browser iframe pointed at another localhost port renders blank inside the
CCC Mac app, which cancels navigation off the dashboard port. Route it through
CCC's proxy instead. In `custom-links.json`:

```json
{ "proxies": { "mytool": 9137 } }
```

Then iframe `/proxy/mytool/<path>` from your app's `index.html`. The proxy
forwards to `127.0.0.1:9137` and rewrites root-relative URLs so the embedded
dashboard stays inside the prefix.

## Notes

- The rail hides below 700px wide — phones and the installed PWA get the full
  width instead.
- `GET /api/apps` returns the resolved, ordered list if you want to inspect it.
- The rail renders its built-ins before that call returns and keeps them if it
  fails, so navigation survives a backend hiccup.

### Allowances

The **Allowances** app tracks separate accounts, automatic Codex usage windows,
and manually reported research or cloud-session balances. It recommends the
usable allowance with the earliest reset or expiration. See
[Allowances](allowances.md) for connecting a second Codex account.
