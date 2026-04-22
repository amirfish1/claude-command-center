# Contributing

Thanks for looking at the code. A few notes before you spend time on a change.

## Status

This is a tool I use daily on a single-founder SaaS workflow. I'll merge fixes
and small features that keep the scope tight. I'm intentionally slow to accept
architectural rewrites — the simplicity (two files, stdlib only) is load-bearing.

## Running locally

```bash
git clone https://github.com/amirfish1/claude-command-center
cd claude-command-center

# Point it at any repo you want the UI to watch.
CCC_WATCH_REPO=~/some/project ./run.sh
```

Open `http://localhost:8090`.

The server writes Claude Code hook configuration into `~/.claude/settings.json`
on first run, and copies hook scripts to `~/.claude/command-center/hooks/`. If
something goes wrong there, the server logs what it did at startup.

## Proposing a change

1. Open an issue first if the change is larger than a bug fix or one-screen
   feature. I'd rather align on direction than review a big PR cold.
2. Small fixes — just open a PR against `main` with a short description of
   what broke and how the change avoids it.
3. Keep the dependency count at zero. No `requirements.txt`, no `package.json`,
   no build step. If a feature needs a library, it probably belongs in a
   separate repo that plugs in.
4. UI changes go in `static/index.html`. Hard-refresh the browser — no server
   restart needed for static assets.
5. Server changes go in `server.py`. Restart with `./run.sh`.

## Navigating the code

Both files are big on purpose. This map gets you to the right region in
under a minute.

### `server.py` (~3800 lines)

| I want to change... | Look here |
|---|---|
| Session column classification rules (server-side hints) | search for `classify` / `_add_sidecar_fields` (~L1918) |
| How a session's JSONL is parsed (titles, commits, push markers) | `_extract_tail_meta` (~L121) |
| Detecting live processes + TTY | `find_live_claude_processes` / `session_live_status` (~L738, L848) |
| Listing all sessions (the big fan-in) | `find_conversations` (~L1756), `find_all_sessions` (~L1929) |
| Backlog = GitHub issues + TODO.md | `_fetch_backlog_issues` / `find_backlog_items` (~L1419, L1471) |
| GitHub integration (create/link/close/verify) | grep for `_gh(`, `close_github_issue_with_commit` (~L423), `mark_issue_in_progress` (~L2720ish) |
| Spawn headless claude / follow-up injection | `spawn_session` (~L2321), `_write_stream_json_user_message` (~L2385), `resume_session_headless` (~L2414) |
| Jump to terminal / tab rename+color on jump | `launch_terminal_for_session` (~L969), `inject_input_via_keystroke` (~L1069) |
| Hook install / sync | `ensure_hooks_installed` at the bottom (~L3680) |
| HTTP routing | `class CommandCenterHandler` (~L3101), `do_GET` (~L3102), `do_POST` (~L3182) |
| Vercel auto-fix-deploy loop | `vercel_deploy_status` (~L2790), `vercel_deploy_status_with_autofix` |

### `static/index.html` (~4500 lines)

Organized top-to-bottom as: CSS → HTML → inline `<script>`. The JS is an
IIFE — state is private. Key functions:

| I want to change... | Look here |
|---|---|
| Column rules (where does a card land?) | `classifyKanbanColumn` (~L2037) |
| Kanban rendering | `renderKanbanBoard` (~L2101) |
| Drag, drop, marquee multi-select, pan scroll | inside `renderKanbanBoard`, scroll a few hundred lines |
| Sidebar / list view rendering | `renderSidebar` (~L1893) |
| Selecting a conversation and streaming events | `selectConversation` + `fetchConversationEvents` + `renderConversationEvents` (~L3667) |
| Issue pane (body + comments + close buttons) | `renderIssueInConvPane` + `wireIssueCloseButtons` (~L3338) |
| Sticky "Original ask" header (issue link, commit-and-resolve, hide-tools toggle) | in `renderConversationEvents` where `_firstUserMsgRendered` is first set |
| Pending-spawn card (optimistic UI when launching) | `insertPendingSpawnCard` (~L1799) |
| Send to terminal / inject helper | `injectToSession` (earlier in file) |
| Live TTY buttons (resume / jump / launch) | functions near L1216–L1500 (`buildResumeCommand`, `jumpToTerminal`, `launchTerminal`) |
| Toolbar (top row: search, pkood, new session, run, hide desc, git only) | HTML around L1090-ish, JS wiring around L3870 |
| Paste-image handler | `attachImagePaste` / `uploadPastedImage` |

### Cross-cutting

- **State on disk**: everything under `~/.claude/command-center/` is the
  app's own persistence. Delete any single file to reset that feature.
- **Hooks**: `hooks/post-tool-use.py` + `hooks/stop.py` get copied to
  `~/.claude/command-center/hooks/` at server startup. They're small (<60
  lines each) — read them first if you want to understand the sidecar
  signal.

## Testing

There's no test suite today. If you add one, pytest + stdlib is fine; don't
introduce a test framework with its own toolchain. Focus on the session
classification logic (`classifyKanbanColumn`, `session_live_status`) — that's
where subtle bugs hurt.

## Scope I'll push back on

- Linux / Windows support (the macOS-specific glue isn't accidental — it's
  why "jump to terminal" works).
- Electron / native wrappers. Browser is the UI on purpose.
- Replacing the single-file frontend with a framework. The whole point is
  "view source, understand it in an afternoon."
- Making it a service anyone else on the network can access. This is a
  local-only dev tool.

## Scope I'll happily accept

- Bug fixes, obviously.
- Cross-provider support: today it only understands Claude Code JSONL and
  `gh`. Other agent runtimes (Aider, Cursor headless, Gemini CLI) could
  plug in as additional source adapters.
- Reducing hidden state. Anywhere you find a sidecar file I could live
  without, open an issue.
- Docs. Especially "why does this card land in <column>?" walkthroughs.
