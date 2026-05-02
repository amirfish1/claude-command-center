# In-UI Terminal — Design

A small "terminal" panel inside the CCC web UI for running easy shell commands
(`git status`, `npm test`, `./deploy.sh`, etc.) without leaving the page. One
command at a time, output streams back, cancellable. Not an interactive PTY —
no `vim`, no `top`, no programs that prompt for input.

## Goals

- Type a command, hit Enter, see output stream in. Like running it in a fresh
  terminal that already `cd`'d to the current repo.
- Support `cd` so users can move between directories naturally; reflect cwd
  in the prompt so it's never a guess.
- Cancel a running command (kills the process group, not just the parent).
- Survive long-running commands (`npm run build`, `./deploy.sh`) by streaming
  output as it arrives.
- Stay small: stdlib-only on the server, inline in `static/index.html` on
  the client. No new pip deps, no bundler.

## Non-goals

- Interactive TTY apps (`vim`, `top`, `htop`, `claude` itself, `less` in
  paged mode). Those need a real PTY — out of scope.
- Mid-command stdin. If a script prompts `(y/n)`, it hangs; users pass `-y`
  / `--yes` flags or pipe input on the command line.
- Persistent env vars, aliases, or sourced files across commands. Only `cwd`
  persists. `export FOO=bar` from one command does not leak into the next.
- Multiple concurrent commands per browser session. One in-flight at a time;
  pressing Enter while running is rejected (or cancels — see open question).
- A full shell history search (Ctrl-R style). Up/down arrow recall is fine;
  fuzzy search is not.

## Architecture overview

```
┌────────────────────────┐                      ┌────────────────────────┐
│ static/index.html      │                      │ server.py              │
│ ┌────────────────────┐ │   POST /api/term/run │ ┌────────────────────┐ │
│ │ Terminal panel     │ ├─────────────────────►│ │ _term_handle_run   │ │
│ │  - cmd input       │ │   {cmd, cwd}         │ │  spawn subprocess  │ │
│ │  - output log      │ │                      │ │  start_new_session │ │
│ │  - cwd display     │◄┼──── SSE stream ──────┤ │  stream stdout/err │ │
│ │  - cancel button   │ │   {type, data}       │ │  on exit: send     │ │
│ └────────────────────┘ │                      │ │   exit + new cwd   │ │
│                        │   POST /api/term/    │ ├────────────────────┤ │
│                        │   cancel             │ │ _term_handle_cancel│ │
│                        ├─────────────────────►│ │  os.killpg(SIGTERM)│ │
│                        │                      │ └────────────────────┘ │
└────────────────────────┘                      └────────────────────────┘
```

Single in-flight command per browser session. The SSE response itself is the
"running" state — when it closes, the command is done.

## Components

### Server: `_term_*` handlers in `server.py`

Three new endpoints, all gated by the existing `_check_same_origin` check:

- **`POST /api/term/run`** — request body `{cmd: str, cwd: str}`. Validates
  `cwd` is inside `REPO_ROOT` (reuses the same clamp as `/api/open`). Spawns
  `bash -c <cmd>` via `subprocess.Popen` with `start_new_session=True` so we
  can later kill the whole process group. Stdout and stderr are merged
  (`stderr=STDOUT`) — one stream is simpler to render and matches what the
  user sees in a real terminal. Streams output as Server-Sent Events:
  - `event: data\ndata: <chunk>` for each readable chunk
  - `event: exit\ndata: {"code": <int>, "cwd": <str>}` at the end
  - `event: error\ndata: <msg>` on spawn failure
- **`POST /api/term/cancel`** — request body `{token: str}`. Kills the
  process group of the in-flight command via `os.killpg(pgid, SIGTERM)`,
  then `SIGKILL` after a 2s grace period if still alive.
- **`GET /api/term/cwd`** — returns the server-side cwd for this session
  (used on first load to seed the UI). Defaults to `REPO_ROOT`.

A small per-session state dict keyed by a `term_token` cookie:

```python
TERM_SESSIONS = {}  # token -> {"cwd": Path, "popen": Popen | None, "pgid": int | None}
```

The token is set by the server on first `/api/term/cwd` hit if the cookie is
missing. Sessions are pruned after 1h of inactivity. If the server restarts,
the UI silently re-initializes — the cwd resets to `REPO_ROOT`.

### `cd` handling

Before spawning, the server parses the command for a leading `cd` (with
shlex). Three cases:

1. **`cd <path>` alone** — resolve path relative to current `cwd`, validate
   it exists and is inside `REPO_ROOT` (same clamp as `/api/open`), update
   `TERM_SESSIONS[token]["cwd"]`, send a synthetic `exit` event with
   `code: 0` and the new cwd. No subprocess spawned.
2. **`cd <path> && <rest>`** — split, update cwd, run `<rest>` in the new
   cwd (recursively, so `cd a && cd b && ls` works).
3. **`cd` embedded inside a complex line** (`for d in *; do cd $d; done`) —
   we don't try to parse it. The subprocess runs as-is, but its `cd` doesn't
   propagate back to `TERM_SESSIONS`. This is documented as a known limit;
   the typical "navigate then run" pattern is covered by case 2.

Plain `cd` with no args resets cwd to `REPO_ROOT` (not `$HOME` — `$HOME`
would break the path-clamp invariant).

### Client: terminal panel in `static/index.html`

A new collapsed panel pinned to the bottom of the page (above the existing
status strip). Toggle via a button in the existing top bar. Persisted
open/closed state in `localStorage`.

Inside the panel:

- **Output log** — monospace `<div>` of pre-formatted lines. Auto-scrolls
  to bottom on new data unless the user has scrolled up. ANSI color codes
  rendered to spans (small inline parser: just `\x1b[<n>m` SGR support, no
  cursor moves). Last ~5000 lines kept; older lines dropped.
- **Prompt line** — `<span>` showing `cwd` + `$`, then a single-line
  `<input>` for the command. Up/down arrows recall the last 50 commands
  from in-memory history (also persisted to `localStorage`, capped 50).
- **Cancel button** — visible only while a command is running. Sends
  `POST /api/term/cancel`. Output log shows `^C` and the exit event.
- **Current-cwd display** — derived from server state, updated on every
  `exit` event. If the server returns a cwd outside `REPO_ROOT` (shouldn't
  happen, but belt-and-braces), client clamps display to `<repo>/`.

JavaScript flow per command:

```
on Enter:
  if running: ignore (or cancel — see open question)
  append "<cwd>$ <cmd>" to log
  POST /api/term/run with {cmd, cwd: currentCwd}
  open EventSource on the response (SSE)
  on data event: append chunk to log
  on exit event: append "[exit N]" if N != 0; update cwd; close source
  on error event: append red error line; close source
  on EventSource error: append "[connection lost]"; close source
```

### Security

This endpoint executes arbitrary shell as the user running CCC, with no
intermediate permission prompt. That is strictly more powerful than
`/api/inject-input`, which routes text through a Claude session whose tool
calls still surface permission prompts in the user's terminal. The terminal
panel is therefore the most security-sensitive surface in CCC. The same
existing protections apply, plus extra hardening:

1. **Same-origin check** on all three endpoints (existing
   `_check_same_origin`).
2. **Bind-host warning**: if `CCC_BIND_HOST=0.0.0.0`, server logs a
   *louder* warning at startup mentioning the terminal endpoint
   specifically. The terminal still works — same as inject-input does
   today — but the warning is bumped to make the trade-off visible.
3. **Path clamp** on `cd`: any cwd update must resolve under `REPO_ROOT`.
   Guards against `cd /` → `rm -rf` from a tricked session, since the
   subsequent command would still have to be issued.
4. **No persistent state across server restarts** — `TERM_SESSIONS` is in
   memory only. Worst case after a restart: the UI shows an old cwd until
   the next command, which the server snaps back to `REPO_ROOT`.
5. **Cancellation kills the process group**, not just the parent. A script
   that spawned children (`make -j`, `./run.sh` that backgrounds a server)
   takes the whole tree down.

Documented in `SECURITY.md` as a new section: "In-UI terminal endpoints —
RCE-equivalent, same posture as inject-input, do not enable network bind
without trusted-network."

### Error handling

- **Spawn fails** (`bash` missing, weird OS) → `error` event, no `exit`.
- **Command exits non-zero** → still `exit` event, with the code. UI shows
  `[exit 1]` etc. in red.
- **`cd` to a path that doesn't exist or escapes `REPO_ROOT`** → `error`
  event explaining why. cwd unchanged.
- **Cancel with no running command** → 409 with `{"error": "not running"}`.
  UI no-ops.
- **Concurrent `run` while one is active** → 409 `{"error":
  "already running"}`. UI shouldn't let this happen, but the server is
  authoritative.
- **SSE drops mid-stream** (network hiccup, browser tab background) → the
  subprocess keeps running on the server. UI shows `[connection lost]`.
  User can reload; old output is gone but the subprocess will eventually
  exit and clean itself up.

## Data flow: a typical session

```
1. Page loads → GET /api/term/cwd → {cwd: "/Users/.../repo"}
2. User types "ls" → POST /api/term/run {cmd: "ls", cwd: "/Users/.../repo"}
   ← SSE: data "CHANGELOG.md\n..."  data "..."  exit {code:0, cwd: ".../repo"}
3. User types "cd morning" → POST /api/term/run
   ← SSE: exit {code:0, cwd: ".../repo/morning"}  (no subprocess spawned)
4. User types "ls" → POST /api/term/run {cmd: "ls", cwd: ".../repo/morning"}
   ← SSE: data "morning_store.py\n..." exit {code:0, cwd: ".../repo/morning"}
5. User types "./long-script.sh" → POST /api/term/run
   ← SSE: data "starting..." (...10s later...) data "step 2..."
   user clicks Cancel → POST /api/term/cancel
   ← SSE: data "^C" exit {code:-15, cwd: ".../repo/morning"}
```

## Testing

- **`tests/test_smoke.py`**: import-time check that the new endpoints are
  registered (no behavior assertion needed, per repo convention).
- **Manual QA checklist** in the PR description:
  - `ls`, `git status` round-trip output.
  - `cd subdir && ls` updates the prompt and lists the right dir.
  - `cd ../../../etc` is rejected (path clamp).
  - `./scripts/long.sh` (sleep 10) cancellable mid-run; process tree dies.
  - Two browser tabs each have independent cwd.
  - Reload mid-command → output gone, but next command works.

## Open questions

1. **Enter while running — ignore, or cancel-then-run?** I default to
   *ignore* (safer; user explicitly cancels). Consider a Cmd/Ctrl+Enter
   shortcut for "force-cancel and run new" if the ignore feels annoying.
2. **Per-tab vs per-browser cwd?** Default: per-browser (one
   `term_token` cookie). Per-tab would need `sessionStorage` + a token in
   the request body. Per-browser is simpler and matches how a single user
   typically thinks about "where am I."
3. **Where does the panel live visually?** Bottom drawer is the proposal.
   A floating window is the alternative — heavier UI work. We'll start
   with the drawer and revisit if it feels cramped.

## Out of scope (deliberately)

- PTY / interactive apps. Future work; would likely use a second optional
  endpoint backed by `pty.openpty` + a tiny WebSocket polyfill — but only
  if demand emerges, since it's a real maintenance burden.
- Multiple terminals / tabs in the panel. One is enough for "easy commands."
- Saved command snippets. The `localStorage` history covers the recent-use
  case; deliberate-snippet-management is a different feature.
- Auth. CCC has no user system; whoever can reach the same-origin can
  already inject input into Claude sessions. Nothing new here.
