---
name: testing-ccc-dashboard
description: How to bring up and runtime-test the CCC dashboard (server.py) on a Linux box — ports, duplicate-instance bypass, fresh-install simulation, forcing best-effort/[ccc:swallowed] failures, and hook stdin tests.
---

# Runtime-testing CCC (`claude-command-center`)

Stdlib-only; no venv or npm install needed to run the server.

## Bringing up instances

```bash
PORT=8090 CCC_BIND_HOST=127.0.0.1 CCC_EPHEMERAL=1 python3 server.py
```

- `main()` refuses a second instance of the **same** repo (matched by git common-dir, identical across
  worktrees). Set both `CCC_EPHEMERAL=1` **and** `CCC_ALLOW_DUPLICATE_REPO=1` when running a
  before/after pair (e.g. branch on `:8090`, `git worktree add /tmp/ccc-main main` on `:8091`).
- Startup takes ~15–25 s before the port answers. Poll with curl instead of a fixed sleep.
- If a launch seems to have no effect, check `ss -ltnp | grep 80` — an earlier crashed-looking
  process may still own the port and the new one silently fails to bind. `kill -9` it first.
- Redirect stderr to its own file (`2>/tmp/x.err`): that is where `[ccc:swallowed]` lines and
  socketserver tracebacks land.

## Browser verification

Repo convention is `node snapshot.js` (puppeteer, browser in `~/.cache/puppeteer`) — **never
Playwright**. But puppeteer's browser may not be downloaded on a fresh box; a real Chrome
(`google-chrome`) driven by computer-use works and is better for recordings. Maximize with
`DISPLAY=:0 wmctrl -i -r <winid> -b add,maximized_vert,maximized_horz` (the display is usually `:0`;
check `ls /tmp/.X11-unix`).

First load shows two dismissable overlays: the telemetry-ping modal (**Continue**/**Skip**) then the
"FIRST FLIGHT" tour (**Skip for now**), plus an "Install Command Center" toast.

The repo list from `/api/repo/list` surfaces in the UI via **+ New session** → the PRODUCTION repo
chips and the **FOLDER** input's ▾ dropdown (`#spawnCwdPicker`). If that endpoint fails, the picker
degrades to a bogus single fallback entry — a good visual signal. The bottom-left health bar's `err N`
counter reflects server-side 5xx/handler errors and is a cheap in-UI assertion.

Expect these **pre-existing** console errors on any run (not regressions): `:8765/ccc-plugin.js
ERR_CONNECTION_REFUSED` and 404s for `/static/coo-notes.json`.

## Fresh-install simulation

A box with no `~/.claude/projects` already *is* the fresh-install case for repo/usage-signal code
paths. Otherwise run with `HOME` pointed at an empty temp dir — but note the server needs `HOME`
**writable at import** (it creates `~/.claude/command-center` and a sqlite productivity store), so a
read-only HOME crashes at startup rather than exercising best-effort handlers.

## Forcing a `[ccc:swallowed]` line

`ccc_server/errlog.py` rate-limits per `(context, exception type)` with a 60 s cooldown;
`CCC_QUIET_ERRORS=1` mutes, `CCC_DEBUG=1` adds tracebacks.

Reliable recipe: import `server` with a fresh writable `HOME`, then chmod the state dir read-only
in-process and call a best-effort writer in a loop:

```python
import os, sys; sys.path.insert(0, '<repo>')
import server
f = server.LOG_VIEWER_STATE_DIR / "last-interactions.json"
if f.exists(): f.unlink()          # an existing writable file makes the write succeed
os.chmod(server.LOG_VIEWER_STATE_DIR, 0o500)
for i in range(500): server._record_interaction("sid-%d" % (i % 3))
```

Gotchas: chmod-ing the directory does not stop rewrites of files that already exist; and many POST
endpoints (e.g. `/api/session/rename`) return early for unknown session ids and never reach the
write. To exercise the cooldown-rollover message without sleeping 60 s, reach into
`ccc_server.errlog._SEEN` and subtract `COOLDOWN_S` from each entry's timestamp.

## Adversarial `send_json` test

Sabotage a **copy** of the tree (never the checkout) to put a non-serializable value into a response
payload *after* any sanitising helper — e.g. add `r["sabotage"] = set(["x"])` at the
`/api/repo/list` assembly loop. Sabotaging inside the sanitiser itself just crashes earlier in
arithmetic and does not reach `json.dumps`. Expected: HTTP 500 with
`{"error": "response serialization failed: ..."}` instead of curl code `000`.

## Hooks

```bash
echo '{bad json' | python3 hooks/stop.py; echo $?   # expect 0 + one [ccc:swallowed] line
```
Same for `notification.py`, `post-tool-use.py`, `pre-tool-use.py`.

## Devin Secrets Needed

None — everything runs locally against `127.0.0.1`.
