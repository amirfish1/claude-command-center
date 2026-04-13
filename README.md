# Claude Command Center

A local Kanban-style web UI for managing every Claude Code session, worktree, GitHub issue, and deploy on this machine. One Python file + one HTML file, zero runtime dependencies, runs on `localhost:8090`.

## Run

```bash
cd /path/to/the/repo/you/want/to/watch
~/dev/claude-command-center/run.sh
```

Or directly:

```bash
CCC_WATCH_REPO=~/code/some-repo PORT=9000 python3 ~/dev/claude-command-center/server.py
```

Open http://localhost:8090.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `CCC_WATCH_REPO` | `$PWD` | Repository the viewer watches (used for `gh`, `vercel`, cwd for spawns, and to locate Claude's per-project JSONL dir) |
| `PORT` | `8090` | HTTP port |
| `VERCEL_PROJECT` | `bookyourmat-app` | Vercel project name for deploy status polling |
| `CCC_TITLE_STRIP` | `BYM` | Comma-separated prefixes to strip from GitHub issue titles (e.g. `BYM,FINIE` strips both `[BYM Problem]` and `[FINIE fix]`) |

## Layout

```
claude-command-center/
├── server.py          # all Python logic
├── static/
│   └── index.html     # UI (HTML/CSS/JS, ~3800 lines)
├── hooks/
│   ├── post-tool-use.py    # installed into ~/.claude/log-viewer/hooks/
│   └── stop.py
├── run.sh             # launcher
└── README.md
```

## Features

- **Kanban**: Backlog → Planning → Working → Waiting → Review → Testing → Inactive → Verified → Archived.
- **Backlog sources**: open GitHub issues (via `gh`) + `TODO.md` in the watched repo.
- **Live conversation panel**: SSE streams the selected session's JSONL in real time.
- **Spawn headless**: big modal with Subject + Body; launches `claude -p` with stream-json I/O so follow-ups can be injected via the input bar without opening a terminal.
- **Jump to terminal** (AppleScript focuses live sessions by TTY).
- **Launch in terminal** (`claude --resume` in a new Terminal.app tab).
- **Drag-drop with marquee multi-select** between columns.
- **Auto-spawn `/fix-deploy`** on Vercel production ERROR (deduped by commit SHA).
- **Issue linking**: click a backlog card → Start Session auto-creates or links a session. "Verify" closes the issue with a commit SHA comment. Drag-to-Archived closes the issue as "not planned".

## Data & state

- **JSONL session logs**: `~/.claude/projects/<slug-of-watched-repo>/*.jsonl`
- **Session registry**: `~/.claude/sessions/<pid>.json` (maintained by Claude itself)
- **Side-car state** (`~/.claude/log-viewer/`):
  - `session-names.json`, `archived-conversations.json`, `verified-conversations.json`
  - `session-issues.json`, `conversation-order.json`, `fix-deploy-spawned.json`
  - `live-state/<sid>.json` (written by hooks)

## Hooks

On first run, `ensure_hooks_installed()` registers `PostToolUse` and `Stop` hooks in `~/.claude/settings.json` that invoke the two scripts in `hooks/`. These write per-session sidecar state so the Kanban classifier can tell "actively working" from "waiting on user".

## Follow-up injection (stream-json stdin)

Spawned headless sessions are launched with `--input-format stream-json`. The server keeps `Popen.stdin` open and the conversation panel's input bar routes to `POST /api/sessions/spawned/<pid>/inject`, which writes a `{"type":"user", ...}` line to stdin. No terminal required.

Caveats:
- Follow-up channel dies on **server restart** (stdin pipe closes). The `claude` process itself keeps running. To continue sending messages, open it in a terminal via "Launch in Terminal".
- The pid→session mapping uses `~/.claude/sessions/<pid>.json`. New spawns only; already-running claude processes can't be injected into.

## Known quirks

- Port-8090 is hardcoded in `run.sh` unless `PORT=` is set.
- `vercel` and `gh` CLIs must be on `$PATH`.
- UI changes to `static/index.html` don't need a server restart — just hard-refresh the browser. Python changes do need a restart.
- `ps -A` is used instead of `pgrep -x` for liveness because `pgrep` silently drops some pids on macOS.
