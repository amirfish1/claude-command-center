# Architecture

Short version: it's a thin read-mostly layer on top of Claude Code's own
on-disk state, plus a few write-through integrations (GitHub, Vercel). No
long-running background workers, no database, no daemon — the server runs
while you're looking at it and goes away when you close it.

## Two files

```
server.py          ~3.8k lines   Python 3 stdlib HTTP server
static/index.html  ~4.5k lines   HTML + CSS + vanilla JS (no framework)
```

That's the product. Everything else is hooks (two small scripts) and state
files under `~/.claude/command-center/`.

## Data sources

The server reads from four places. Nothing else is authoritative.

1. **`~/.claude/projects/<project-slug>/*.jsonl`** — Claude Code's own
   session transcripts. Written by Claude regardless of how the session was
   launched. One file per session, appended line-by-line.

2. **`~/.claude/sessions/<pid>.json`** — Claude's live-session registry.
   Claude writes this for interactive TUIs; the server matches entries against
   `ps -A` to tell which sessions are still running and what TTY they're on.

3. **`~/.claude/command-center/live-state/<sid>.json`** — written by the
   `PostToolUse` and `Stop` hooks that the server installs on first run.
   Every tool invocation bumps a per-session sidecar file with `status`,
   `tool`, `file`, `has_writes`, `timestamp`. This is how the kanban can tell
   "Claude is actively running tools" from "Claude is waiting for input".

4. **`gh`** (and optionally `vercel`) CLIs — shelled out to for GitHub
   issue state, labels, assignees, and deploy status. Responses are cached
   in-process for 5 minutes.

## Data sinks (write-through state)

All mutations land in human-readable JSON sidecar files under
`~/.claude/command-center/`:

| File | Contents |
|---|---|
| `session-names.json` | `{session_id: display_name}` — user-set names |
| `archived-conversations.json` | `[session_id, ...]` |
| `verified-conversations.json` | `[session_id, ...]` |
| `session-issues.json` | `{session_id: issue_number}` |
| `conversation-order.json` | `[session_id, ...]` — custom ordering |
| `fix-deploy-spawned.json` | `{commit_sha: {pid, name, spawned_at}}` — dedupe for auto-fix-deploy |

Everything is JSON, everything is rewriteable by hand if something goes
wrong. There is no migration layer.

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
  `claude -p --input-format stream-json` subprocesses, tracked in an
  in-memory `_spawned_sessions` list. The session's stdin pipe stays open,
  so follow-up messages can be injected without opening a terminal.
- **Resumed-on-demand sessions** — dormant transcripts brought back via
  `claude --resume <sid> -p ...` when the user injects input into an inactive
  card. Same stream-json mechanism.

All three converge into the same card model in the UI.

## Classification

`classifyKanbanColumn` (client-side, in `static/index.html`) takes a session
entry and returns one of: `backlog / planning / working / review / testing /
inactive / verified / archived`. The rules:

```
archived flag          -> archived
verified flag          -> verified
source is backlog      -> backlog (open) / verified (closed as completed)
                                   / archived (closed otherwise)
live + sidecar waiting -> working     (actively between turns)
live + has_writes      -> working     (actively running tools)
live + sidecar         -> planning    (no tools used yet)
pushed / committed     -> review
not live + no sidecar  -> inactive
```

Manual drag-drop writes a client-side override into `localStorage`
(`ccc-column-overrides`). Overrides auto-clear only when the session's natural
state advances past the override (e.g., an override of `working` is dropped
once the session's commits get pushed).

## Hooks

On server startup:

1. Copy `hooks/post-tool-use.py` and `hooks/stop.py` from the repo into
   `~/.claude/command-center/hooks/` (only if contents changed).
2. Merge hook entries into `~/.claude/settings.json` under `hooks.PostToolUse`
   and `hooks.Stop`, pointed at those copies.

Both hooks read Claude's stdin (a small JSON event), then write/update the
session's sidecar file. They never block Claude — errors are swallowed.

## macOS-specific bits

- **Jump to terminal** uses AppleScript via `osascript` to focus an existing
  Terminal/iTerm tab by TTY, and for "rename/color" it keystrokes `/rename`
  and `/color` into Claude's TUI via System Events.
- **Launch in terminal** opens a new Terminal/iTerm window running
  `claude --resume <sid>`.
- **Liveness** uses `ps -A` rather than `pgrep -x`; on macOS `pgrep`
  occasionally drops pids, particularly for processes started under tmux.

## What isn't here

- No database. No Redis. No message broker.
- No auth. `localhost`-only by design.
- No per-user multi-tenancy.
- No scheduled jobs. Cache refresh is request-driven.
