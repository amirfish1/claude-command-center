# Claude Command Center

A local command center for Claude Code that doesn't care how your agents
were launched. Terminal sessions, headless processes, or spawned from the
dashboard — it latches onto all of them and lets you drop in and out of any
task to fix things.

## Why this exists

Most Claude Code orchestration tools are opinionated wrappers. They want to
own execution — you launch agents *through* them, and in return you get a
dashboard. That's fine until it isn't. The moment you open a terminal,
`claude --resume` something, and iterate on it by hand, you're outside the
tool's universe. The dashboard can't see it. The work you just did doesn't
show up on the kanban, against the issue, in the review queue.

This goes the other way. It treats Claude Code's on-disk state as the
source of truth — `~/.claude/projects/*.jsonl` transcripts, the
`~/.claude/sessions/<pid>.json` live registry, and per-tool-call sidecar
files written by two hooks we install into `~/.claude/settings.json`. If
Claude Code is running anywhere on your machine, it shows up here. If you
close the dashboard, your sessions keep running. If you open a terminal and
iterate by hand, the card updates.

The dashboard also knows how to *spawn* headless sessions (via
`claude -p --input-format stream-json`) and *resume* dormant ones on demand —
but those are additive. The thing it's built around is attaching to work
that already exists.

## Quickstart

Requirements: macOS, Python 3, and [Claude Code](https://docs.claude.com/en/docs/claude-code) installed.
Optional: [`gh`](https://cli.github.com/) for GitHub integration, `vercel` for deploy status.

```bash
git clone https://github.com/amirfish1/claude-command-center
cd claude-command-center

# Point it at any repo you want to watch. Defaults to cwd.
CCC_WATCH_REPO=~/some/project ./run.sh
```

Open [http://localhost:8090](http://localhost:8090).

First launch copies two hook scripts into `~/.claude/command-center/hooks/` and
registers them in `~/.claude/settings.json`. After that, every Claude Code
session on your machine — terminal, headless, or dashboard-spawned —
writes sidecar state the UI uses for the kanban.

## Core concepts

```
┌─────────────┐   writes   ┌────────────────────────────────┐
│ any claude  │ ─────────> │ ~/.claude/projects/*.jsonl     │
│ process     │            │ ~/.claude/sessions/<pid>.json  │
│ anywhere on │            │ ~/.claude/command-center/          │
│ your mac    │            │   live-state/<sid>.json        │
└─────────────┘            └──────────────┬─────────────────┘
                                          │  reads
                                          v
                              ┌───────────────────────┐
                              │ server.py (stdlib)    │
                              │ :8090                 │
                              └───────────┬───────────┘
                                          │
                                          v
                              ┌───────────────────────┐
                              │ static/index.html     │
                              │ kanban + detail pane  │
                              └───────────────────────┘
```

- **Session** — any Claude Code transcript on disk, alive or dormant.
- **Attach** — the server reads Claude's own files + sidecar state the
  installed hooks write after every tool call. Nothing to configure
  per-session.
- **Columns** — Backlog → Planning → Working → Review → In Testing →
  Verified / Inactive / Archived. Columns are derived from session state
  (live? commits? pushed? sidecar activity?), overridable by drag.
- **Backlog** — open GitHub issues + `TODO.md` entries, surfaced as cards
  next to your active sessions so everything lives on one board.

## Features

- **Kanban** across every session, with drag-drop between columns,
  rubber-band multi-select, and per-column tinting.
- **GitHub integration** — start a session from an issue with one click
  (auto-adds `claude-in-progress` label + self-assigns). Verify closes the
  issue with a commit-SHA comment. Drag to Archived closes as "not
  planned". Issue body + comments render inside the dashboard (no iframe —
  GitHub blocks that).
- **Attach to existing sessions** — terminal `claude` processes show up
  automatically. Jump-to-terminal focuses them by TTY; rename/color the
  tab via Claude's own slash commands.
- **Headless spawn with follow-up** — launch `claude -p` sessions from the
  dashboard and keep talking to them via an in-browser input bar (no
  terminal needed, stdin pipe stays open).
- **Resume-on-demand** — injecting into a dormant session auto-spawns a
  headless `claude --resume` to deliver the message.
- **Auto-fix deploys** — optionally polls Vercel, spawns a `/fix-deploy`
  session on new production ERRORs (deduped by commit SHA).
- **AI-assisted titles** — click ✨ on any card to regenerate its title
  via `claude -p` (Haiku by default). Used for cleaning up auto-generated
  session slugs.

## Morning view (Phase 1 — skeleton)

A second page at `/morning` that will become the single morning landing spot
for goals, strategic priorities, today's tactical queue, and an inbox of
LLM-extracted captures from free-form sources.

Phase 1 ships the UI shell with sample hardcoded data — no ingestion, no
session launching, no Notion migration yet. Those land in Phase 2+.

- Landing page: `http://localhost:8090/morning`
- Goal detail: `http://localhost:8090/morning/goals/<slug>`
- JSON: `http://localhost:8090/api/morning/state`, `/api/morning/goals/<slug>`

## Architecture

Two files: a single Python file (stdlib-only HTTP server) and a single HTML
file (vanilla JS, no framework, no build). State lives in JSON sidecar
files under `~/.claude/command-center/` — all human-readable, all rewriteable
by hand.

The server has no background workers. Every API request scans Claude's
session directories, merges in sidecar state, enriches with cached GitHub
issue data, and returns a flat list. The client classifies into columns
using rules like "has_push → Review", "live + sidecar_has_writes → Working".

Hooks are the only invasive thing. On first run the server copies
`hooks/post-tool-use.py` and `hooks/stop.py` to `~/.claude/command-center/hooks/`
and merges entries into `~/.claude/settings.json`. After that, Claude Code
fires them after every tool invocation, each hook writes a tiny JSON file
under `live-state/`, and the server reads those to answer "is this session
actually doing something right now or is it idle waiting for input?".

For more depth: [`docs/architecture.md`](docs/architecture.md),
[`docs/session-attach.md`](docs/session-attach.md).

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `CCC_WATCH_REPO` | `$PWD` | Repository the UI watches (cwd for spawns, `gh` calls, project slug lookup) |
| `PORT` | `8090` | HTTP port |
| `CCC_BIND_HOST` | `127.0.0.1` | Interface to bind. Set to `0.0.0.0` to expose on the LAN — **no auth, see [`SECURITY.md`](SECURITY.md)** |
| `CCC_USER_NAME` | *(empty)* | First-name shown in the Morning view greeting ("Good morning, Amir."). Leave empty for a name-less greeting. |
| `CCC_ENABLE_MORNING` | *(off)* | Set to `1` to enable the Morning view (goals / strategic / tactical / braindump). Off by default — most users only need the kanban. |
| `CCC_TITLE_STRIP` | *(empty)* | Comma-separated prefixes to strip from GitHub issue titles (e.g. `ACME,FOO` strips `[ACME ...]` and `[FOO ...]`) |
| `CCC_ORG_PATTERNS` | *(empty)* | Multi-tenant org-tagger. Format: `Label1:pat1a\|pat1b;Label2:pat2`. Each issue body is scanned and tagged with the first matching label so the UI can group backlog by org. |
| `VERCEL_PROJECT` | *(unset)* | Vercel project name. Leave empty to disable deploy polling. |

## Roadmap

**Shipped**
- Kanban over all live + dormant Claude Code sessions
- GitHub issue → session → verify → close pipeline
- Headless spawn with stdin-pipe follow-up
- Resume-on-demand
- Auto-fix-deploy (Vercel)
- AI title regeneration

**Not yet**
- Test suite. Zero tests today. The session classifier is where this hurts
  most.
- Non-Claude-Code agent runtimes. The ingestion layer would port to
  anything that writes structured transcripts (Aider, Gemini CLI, etc.),
  but adapters don't exist yet.
- Code split. `server.py` and `index.html` are each one huge file on
  purpose — you can read the whole product in an afternoon. That tradeoff
  bends eventually; it hasn't yet.

**Out of scope**
- Linux / Windows. The macOS-specific AppleScript glue is why attach and
  jump-to-terminal work end-to-end. Porting means stubbing those out.
- Multi-user / network-exposed mode. This is a local dev tool. If you're
  looking at it on a remote host, something has gone wrong.
- Electron / native wrap. Browser is the UI on purpose.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 2026 Amir Fish

## Acknowledgments

Built on top of [Claude Code](https://docs.claude.com/en/docs/claude-code).
The `gh` CLI and Vercel CLI are optional integrations but do most of the
heavy lifting where they're used.
