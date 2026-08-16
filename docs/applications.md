# Applications rail

The column on the left edge of every CCC page is the Applications rail. It
replaced the old Queues/Sessions toggle: each surface is an app, and your own
apps sit next to the built-in ones.

Built-ins are Sessions (`/`) and Queues (`/q2.html`). Everything else is yours.

## Adding an app

The quickest way is the **+** at the bottom of the rail. It opens the
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
