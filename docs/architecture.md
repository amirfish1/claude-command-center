# Architecture

[Back to the README](../README.md) · [Orchestration API](orchestration.md)

CCC attaches to the session state that agents already write. The dashboard
is a stdlib Python HTTP server with vanilla HTML, CSS, and JavaScript. A
separately managed `ccc-worker` owns persistent agent transports, so the
dashboard can restart without closing worker-owned Claude, Codex, or Kimi
connections.

## Persistent execution worker

The dashboard and worker communicate over an authenticated, mode-0600 Unix
socket. The worker records spawns and turns in
`~/.claude/command-center/control-plane.sqlite3`, including idempotency keys,
leases, results, and parent-child edges.

Work known not to have been sent replays after a drain or worker start.
Work that may have reached an engine becomes `uncertain` and is never blindly
replayed. Live-process evidence can reconcile it; an explicit
`POST /api/control-plane/resolve` supports `retry`, `complete`, `fail`, or
`cancel`.

Maintenance settings show worker health and active, queued, and uncertain
counts. **Pause dispatch** queues new owned work durably. **Restart dashboard**
drains dispatch, restarts only the HTTP/UI process, resumes dispatch, and
replays provably unsent work. `GET /api/control-plane/work` and
`GET /api/control-plane/graph?root_id=…` expose those records. Maintenance can
also start an offline worker and inspect, open, or restart WatchTower.

## Dashboard and state

`server.py` and `ccc_server/` provide the backend; `static/` provides the
build-free UI. Session metadata lives in JSON sidecars under
`~/.claude/command-center/`, alongside local databases for durable work.
See [Configuration](configuration.md) for the worker socket and ledger paths.

## Data sources

For Claude Code, the main session inputs are below. Other engines use their
own stores through adapters; see [Engine support](engine-support.md).

1. **`~/.claude/projects/<project-slug>/*.jsonl`** — Claude Code's own
   session transcripts. Written by Claude regardless of how the session was
   launched. One file per session, appended line-by-line.

2. **`~/.claude/sessions/<pid>.json`** — Claude's live-session registry.
   Claude writes this for interactive TUIs; the server matches entries against
   `ps -A` to tell which sessions are still running and what TTY they're on.

3. **`~/.claude/command-center/live-state/<sid>.json`** — written by the
   `PostToolUse` and `Stop` hooks registered after you approve them.
   Every tool invocation bumps a per-session sidecar file with `status`,
   `tool`, `file`, `has_writes`, `timestamp`. This is how the kanban can tell
   "Claude is actively running tools" from "Claude is waiting for input".

4. **`gh`** (and optionally `vercel`) CLIs — shelled out to for GitHub
   issue state, labels, assignees, and deploy status. Responses are cached
   in-process for 5 minutes.

## Data sinks (write-through state)

Session metadata changes land in JSON sidecar files under
`~/.claude/command-center/`:

| File | Contents |
|---|---|
| `session-names.json` | `{session_id: display_name}` — user-set names |
| `archived-conversations.json` | `[session_id, ...]` |
| `verified-conversations.json` | `[session_id, ...]` |
| `session-issues.json` | `{session_id: issue_number}` |
| `conversation-order.json` | `[session_id, ...]` — custom ordering |
| `fix-deploy-spawned.json` | `{commit_sha: {pid, name, spawned_at}}` — dedupe for auto-fix-deploy |

These sidecars are separate from the worker's durable SQLite ledger. Do not
edit active work records by hand; use the control-plane resolve API for
uncertain work.

## Request flow

A typical `/api/sessions` request:

```
browser                server.py
   |  GET /api/sessions
   |---------------------->
   |                       ├─ scan ~/.claude/projects/<slug>/*.jsonl
   |                       ├─ read ~/.claude/sessions/*.json + ps -A
   |                       ├─ read sidecar state
   |                       ├─ merge with overrides (names, verified, archived)
   |                       ├─ enrich with GH state (cached 5min)
   |                       └─ sort
   |  <-- JSON array  ----|
```

The same endpoint returns backlog items (open GitHub issues + `TODO.md`)
merged into the list with `source: "backlog"`.

## Agnostic attach

The UI makes no distinction between:

- **Terminal sessions** you started yourself with `claude` — surfaced via
  `~/.claude/projects/*.jsonl` + `~/.claude/sessions/<pid>.json`.
- **Headless sessions** spawned by the UI — launched as
  `claude -p --input-format stream-json` subprocesses, with worker-owned
  execution recorded in the durable ledger. The session's stdin pipe stays open,
  so follow-up messages can be injected without opening a terminal.
- **Resumed-on-demand sessions** — dormant transcripts brought back via
  `claude --resume <sid> -p ...` when the user injects input into an inactive
  card. Same stream-json mechanism.

All three converge into the same card model in the UI.

## Classification

`classifyKanbanColumn` (client-side, in `static/app.js`) takes a session
entry and returns one of: `backlog / needs-attention / icebox / working /
waiting / review / testing / verified / archived`. The rules:

```
archived flag           -> archived
verified flag           -> verified
source is backlog       -> backlog (open) / verified (closed as completed)
                                    / archived (closed otherwise)
                                    / needs-attention (label) / icebox (label)
icebox label            -> icebox        (wins over liveness — explicit park)
is_live                 -> working       (any live session — sidecar or not)
pushed / committed      -> review
not live + edits + assistant-last -> review
needs-attention label   -> needs-attention
claude-in-progress (dead) -> working
otherwise               -> working       (dead + empty — render adds a blue
                                          "no edits" chip via hasNoEdits())
```

A separate `hasNoEdits(c)` helper drives a small blue **"no edits"** chip in
both the list view and the kanban card. Liveness is irrelevant — any session
whose Claude has never touched a file gets the chip, so you can spot
resumable shells (and pre-tool fresh sessions) without a separate column.

The full annotated list lives in [`kanban-rules.md`](kanban-rules.md), with a
draggable diagram in [`kanban-rules.html`](kanban-rules.html).

Manual drag-drop writes a client-side override into `localStorage`
(`ccc-column-overrides`). Overrides auto-clear only when the session's natural
state advances past the override (e.g., an override of `working` is dropped
once the session's commits get pushed). Stale `planning` and `inactive`
overrides from older builds are dropped on first render.

## Hooks

Startup copies CCC's `hooks/*.py` scripts into
`~/.claude/command-center/hooks/`. It registers them in
`~/.claude/settings.json` only after your approval. An update that changes an
approved item asks again. See [Agent config consent](agent-config-consent.md).

The hooks read Claude's stdin event and update tiny JSON files under
`live-state/`. These tell CCC whether a session is running a tool or waiting
for input. Hook errors are handled without prompting the agent.

## macOS-specific bits

- **Jump to terminal** uses AppleScript via `osascript` to focus an existing
  Terminal/iTerm tab by TTY, and for "rename/color" it keystrokes `/rename`
  and `/color` into Claude's TUI via System Events.
- **Launch in terminal** opens a new Terminal/iTerm window running
  `claude --resume <sid>`.
- **Liveness** uses `ps -A` rather than `pgrep -x`; on macOS `pgrep`
  occasionally drops pids, particularly for processes started under tmux.

## What isn't here

- No Redis or external message broker.
- No multi-user authentication. The dashboard binds to loopback by default;
  trusted-network access is opt-in. See [SECURITY.md](../SECURITY.md).
- No per-user multi-tenancy.

The list and transcript paths cache expensive reads. The durable execution
worker and scheduled Jobs are separate from those request-driven caches.
