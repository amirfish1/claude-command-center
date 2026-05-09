# Changelog

All notable changes to this project will be documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Screenshots in the bug-report modal — an "Add screenshot" button opens
  the macOS area-selector (`screencapture -i`) so the user draws a
  rectangle over exactly what they want to share. The preview renders in
  the modal with Retake / Remove controls. On submit the image is committed
  to a dedicated `bug-screenshots` branch of `amirfish1/claude-command-center`
  and embedded inline in the issue body via `raw.githubusercontent.com`. If
  the push fails (typical for OSS users without write access), the image is
  saved to `~/.claude/command-center/bug-screenshots/`, Finder pops to it,
  and the issue body carries a drag-drop instruction so the user can attach
  it manually. New endpoints: `POST /api/bug-report/capture`,
  `POST /api/bug-report/reveal`. `POST /api/bug-report` now accepts an
  optional `screenshot_b64` field.
- **Sibling-worktree detection in the workspace strip.** Workspace pill now
  surfaces a `🌿 +N worktrees (X subagent · Y manual)` chip when the session's
  repo has worktrees besides the one it's editing in. Tooltip lists each
  path · branch with `[agent]` for entries locked by superpowers /
  orchestration skills (lock reason starts with `claude agent`). Catches
  the "subagent silently forked a branch" case the user might not realise
  happened. Uses `git worktree list --porcelain` against the session's
  canonical repo (cwd's main repo if it's a worktree, the cwd itself if a
  shared clone, or the inferred `effective_cwd`). New `worktrees`,
  `worktrees_agent_count`, `worktrees_manual_count` fields on
  `/api/session/<id>/workspace`.
- **Effective-workspace inference from tool calls.** When a session's launch
  cwd is an empty stub directory but its actual edits land in a real repo
  elsewhere (e.g. cwd `~/my-finance-app` while the session reads/writes files
  under `~/Apps/BYM+Finie`), the workspace strip above the input bar now
  surfaces a second `via tool calls: ~/Apps/BYM+Finie ⎇ main ↑1` pill.
  Inference walks the session JSONL collecting Read/Edit/Write `file_path`s
  and Bash `cd` / `git -C` redirects, resolves each to its git toplevel, and
  picks the dominant repo (>50% of resolved paths, ≥2 evidence points).
  Stale paths under the literal cwd are remapped to known `cd` targets when
  the substituted variant exists on disk. Display-only — never used to
  dispatch git writes; future write actions must use literal cwd or
  per-action evidence. New `effective_*` fields on `/api/session/<id>/workspace`.
- **"Last interacted" indicator on cards.** Each kanban card now shows a small
  italic "Last interacted Xm ago" line whenever you've typed a message into the
  card or clicked one of its action buttons (currently routed through
  `/api/inject-input` — typing, Approve, Deny). Drag-drop column moves do **not**
  count as interaction. Stamps persist to
  `~/.claude/command-center/last-interactions.json`, and the kanban now sorts by
  `max(last_interacted, modified)` so a card you just typed into bubbles to the
  top instantly even before Claude responds.
- **"Open in Claude Desktop" button** beside Jump/Launch in the
  conversation toolbar (and the conversation-pane chrome). Resumes the
  current session inside the Claude Desktop GUI app via the
  `claude://resume?session=<uuid>` deep-link — the desktop app imports
  the CLI session and navigates to it. macOS only for now (relies on
  `open(1)`).

### Changed
- **Renamed `Planning` column to `Icebox` and collapsed pre-tool live state into `Working`.**
  The old `Planning` column was doing two unrelated jobs: a transient "live
  but no tool fired yet" pre-window, and a long-lived "parked by user" intent.
  The transient half didn't earn a column (it's seconds long, no human action
  required), so it now lives in `Working` and the column is renamed `Icebox` to
  match the GitHub label that drives it. New tiebreak: a card with both the
  `icebox` label and a live process lands in `Icebox` — the explicit "park"
  signal beats implicit liveness. The classifier shrinks from 15 rules to ~10.
  Stale `planning` localStorage overrides from older builds drop on first
  render. `mark_issue_in_progress` now also strips the `icebox` label so the
  GitHub state matches the new column. See [`docs/kanban-rules.md`](docs/kanban-rules.md)
  and [`docs/kanban-rules.html`](docs/kanban-rules.html).
- **Conversation pane styled to match Claude Desktop.** User messages render
  as a chat bubble (blue tint, rounded corners, no USER label or timestamp)
  with explicit SF Pro / system-ui font, 16px / line-height 1.6. Assistant
  rows lose their purple background and left border; metadata (line number,
  timestamp) dimmed to 35% opacity. Body gets `-webkit-font-smoothing:
  antialiased` and `font-feature-settings: "kern", "liga", "calt"` for
  crisper type rendering on macOS.
- **Tool calls now collapse into a "Ran N commands ▶" group.** Consecutive
  Bash/Read/Edit/Grep events fuse into one collapsible container in the
  conversation pane. Single-command groups get a smart label
  ("Read foo.py", "Edited bar.tsx", "Ran lsof -i :3001…", "Spawned
  subagent: …"); multi-command groups read "Ran 3 commands". Click the
  header to expand. Inside expanded groups, tool rows stay visible even
  when the global "Hide tools" toggle is on.
- **Tool results now render inline.** The server captures tool_result
  content (truncated to 800 chars) and the UI renders it as a monospace
  preview block under the matching tool_call (red left border for errors,
  default muted for stdout). Replaces the previous behaviour of hiding
  tool_result events entirely.

### Fixed
- "Send to terminal…" input bar now appears for **dormant** sessions, not
  just live ones with a TTY. The backend's `/api/inject-input` endpoint
  already routed dormant sends through headless `claude --resume`, but the
  UI's visibility check (`live && tty`) hid the bar — leaving users with
  Resume/Launch buttons and no way to type a follow-up. Bar now shows for
  any selected session; placeholder adapts to "Resume and send…" when
  dormant, "Send to terminal…" when live, "Send to pkood agent…" when
  pkood.

### Removed
- **Issue Watcher subprocess + `find_log_files` data path.** The standalone
  `scripts/claude-issue-watcher.sh` polling daemon (and its sidebar
  start/stop panel) is gone. The script had been missing from the repo for
  some time and the panel was already dead in the UI; this commit deletes
  the scaffolding behind it: `WATCHER_SCRIPT`, `_watcher_proc`,
  `_watcher_lock`, `_watcher_output_lines`, `_reader_thread`,
  `_find_zombie_watchers`, `_kill_zombie_watchers`, `watcher_status`,
  `watcher_start`, `watcher_stop`, the `/api/watcher`, `/api/watcher/start`,
  `/api/watcher/stop` endpoints, and the `watcher_enabled` field on
  `/api/config`. Same on the front-end: `.watcher-panel` HTML/CSS,
  `pollWatcher`, the watcher button handler, and APP_CONFIG plumbing.
  Issue triage now happens inline — the kanban surfaces issue cards with
  a "Fix" button that calls `spawn_issue_fix()` directly, and remote
  agents drive the same flow over `/api/ask`.
- **`find_log_files` + `LOG_DIR/issue-N.log` data path.** Removed the
  `find_log_files()`, `_extract_spawn_meta()`, `parse_log_file()`, and
  `parse_event()` functions, the `FALLBACK_DIR` constant, and the
  `/api/logs` and `/api/logs/<issue>` endpoints. The dual-source merge
  in `find_all_sessions()` (which produced `source="watcher"` cards)
  is gone — sessions come from `find_conversations()` (interactive) +
  `find_pkood_agents()` + `~/.claude/tasks/` only. Front-end:
  `sessionIssueByConv`, `issueLogPoller`, `issueLogLastLine`,
  `stopIssueLogPoller`, `pollIssueLogs`, the `source === 'watcher'`
  branch in `selectConversation`, and the matching source-badge are all
  removed.
- **`spawn_issue_fix` no longer writes a synthetic stream-json header.**
  The function used to prepend a `spawn_meta` event and a synthetic
  user-message event so the `parse_log_file` UI viewer had something to
  render. With that viewer gone, the headers are dead writes — the
  spawned `claude -p` already writes its own `~/.claude/projects/.../<sid>.jsonl`
  which surfaces as the interactive session card. The local log file is
  still written (renamed `spawn-issue-{N}-{ts}.log` for naming consistency
  with `spawn_session`) because `_reattach_spawned_orphans` reads it via
  `extract_session_id` to backfill the session id after a restart.

### Changed
- User messages in the conversation pane now render in blue (the
  shared `--accent` colour) instead of green, so they read as
  "the human's turn" rather than blending with the cyan "result"
  rows. Assistant messages stay purple, results stay cyan.
- Sessions/Issues tabs removed from the main pane. The dedicated `/api/issues`
  view (and its tab bar) is gone — GitHub issues are still surfaced via
  inline kanban cards, with a "Fix" button per card. The "← Back" mobile
  button moved into `convToolbar`.
- "Needs your attention" panel relocated from the dead split-kanban layout
  into the sidebar (between the conversation list and the Issue Watcher
  panel). It's still collapsed-by-default and still drag-resizable.
- "View" filter menu (Last 10h / Compact / GitHub-only / pkood spawn) and
  "✨ Titles" bulk-summarize button relocated from the dead split-kanban
  toolbar into the layout-agnostic `.ccc-topbar`. Generic
  `.ccc-topbar .topbar-btn` style added so the new entries match the repo
  picker visually.

### Fixed
- Clicking a kanban card opens the conversation in the main pane again. The
  card-click path went through `getConvView()`, which until now still routed
  to the dead split-pane (`$convPanelView`) when `kanbanView=true`, so the
  conversation rendered into an invisible element and the right pane stayed
  on the empty state.

### Added
- Persistent spawn-PID registry at `~/.claude/command-center/spawned-pids.json`
  plus a startup sweep that reattaches surviving headless `claude -p` children
  after a server restart. Previously the in-memory tracking dict was wiped on
  restart, leaving live orphans unreachable from the dashboard ("Send failed:
  unknown pid") until the user manually killed them. The sweep verifies each
  recorded PID is still alive *and* still belongs to a `claude` process (PID
  reuse defence) before re-registering it; dead/reused entries are pruned so
  the registry doesn't grow forever. Pattern adapted from
  comfortablynumb/claudito (MIT). No orphan is ever killed — reattach only.
- **Classifier test coverage.** New `tests/test_classify.py` drives
  `find_conversations()` and `_add_sidecar_fields()` against a hand-crafted
  `tests/fixtures/mock_session.jsonl` (Read + Edit tool_use, matching
  tool_results, trailing `<session-state>` and `result` events) so the
  parser that turns transcripts into kanban-card metadata is no longer
  untested.
- Surface `~/.claude/tasks/<session_id>/*.json` (Claude Code's native TodoWrite
  output) as backlog cards. One card per session — title taken from the
  in-progress task (falls back to first pending, then most-recent completed),
  with a small `task` source-tag and `done/total` counts. Sessions already
  represented on the board are skipped to avoid dups.
- **Notification hook drives a real Needs-Approval signal.** A new `Notification`
  hook (`hooks/notification.py`) writes a `<sid>_needs_approval.json` marker
  whenever Claude Code asks the user for permission; PostToolUse clears it. The
  kanban now routes those cards into a dedicated "Waiting" column with a
  pulsing 🔔 badge above the title, replacing the brittle pending_tool/age
  heuristic that confused "tool fired but not yet returned" with "Claude is
  blocked on a permission prompt." Hook auto-installs on next server start.
- **Live "what's running" signal on cards and chat pane.** The kanban card now
  surfaces the currently-executing tool (e.g. `Bash npm test`, `Read foo.py`)
  as an animated badge while a session is live, instead of showing only a glow.
  The conversation detail pane gains a sticky strip that does the same, refreshed
  every 5s from `/api/session-status`. New `PreToolUse` hook (`hooks/pre-tool-use.py`)
  writes a `<sid>_in_flight.json` marker so long-running tools (Bash, WebFetch)
  read as "running 8s" instead of "8s ago"; PostToolUse clears it on completion.
  Hook auto-installs into `~/.claude/settings.json` on next server start.
- `CCC_ALLOWED_ORIGIN` env var — comma-separated list of additional origins
  added to the same-origin POST allowlist. Pair with `CCC_BIND_HOST=0.0.0.0`
  to reach the UI from a phone or other device over a trusted network
  (Tailscale, VPN). The same-origin check otherwise rejects POSTs from any
  Origin that isn't `localhost` / `127.0.0.1` / `[::1]`, which is what made
  Tailscale access stop working after the OSS-launch security hardening.
  Documented in `README.md` and `SECURITY.md`; startup prints the active
  allowlist when set. There is still no auth — every entry is a peer that
  can run commands as you.
- **First-class trusted-network access.** The `CCC_ALLOWED_ORIGIN` env var
  added in the previous commit is now joined by two more layers, all merged
  into the same-origin allowlist at startup: a persisted JSON config at
  `~/.claude/command-center/network.json` (so settings survive shell
  restarts), and a `CCC_TRUST_TAILNET=1` opt-in (or `trust_tailnet: true` in
  the JSON) that shells out to `tailscale status --json` and adds the local
  node's MagicDNS hostname + Tailscale IPs automatically. New endpoints
  `GET /api/network-config` (returns the live config plus a tailnet probe)
  and `POST /api/network-config` (writes the JSON, restarts in-place via
  `os.execvp`). The POST is **localhost-only** even though the broader
  allowlist accepts tailnet origins for everything else — a peer cannot
  expand its own trust further. New "Network access…" entry in the sidebar
  settings popover drives all of it from the UI: a checkbox to bind on all
  interfaces, a checkbox to trust the detected tailnet, and a free-text
  field for additional origins (e.g. other VPNs). Env vars still win when
  set, so CI overrides keep working. README and SECURITY.md updated, plus
  `run.sh` no longer defaults `CCC_BIND_HOST` (would otherwise clobber the
  JSON-config layer).

### Fixed
- Mobile: "Send to terminal…" input bar in the conversation panel was
  invisible on iOS Safari — the panel used `position: fixed; inset: 0`
  with no safe-area / dynamic-viewport handling, so the bottom of the
  panel (where the input lives) sat under the URL bar and home
  indicator. Now uses `100dvh` and `padding-bottom:
  env(safe-area-inset-bottom)` so the input stays visible above both,
  and resizes when the on-screen keyboard opens.

## [3.2.0] - 2026-05-08

### Added
- **CCC now ships its own conversation-history indexer.** A bundled `_history_index/` package walks every Claude Code and Codex transcript on the machine into a SQLite FTS5 store at `~/.claude-index/index.db`. No separate `pip install claude-index` step, no manual launchd plist — the first time you search and no index exists, an inline OOBE prompt offers to build one in the background. A topbar pill ("📚 History · 12m ago") shows the freshness of the most-recently-indexed message, spins while ingesting, and exposes manual re-trigger on click. If `sqlite-vec` and Ollama (`nomic-embed-text`) are present locally, semantic embeddings come along for the ride: `/api/search-history?semantic=1` runs hybrid retrieval (top-K BM25 ∪ top-K vec, fused via RRF) and tags each result `_source ∈ {bm25, vec, fused}`. Sidebar rows that match via the vec path get a purple "semantic history" badge instead of the lexical-only blue "history" one, so the user can see when semantic recall is doing the work. Falls back to BM25 silently when sqlite-vec / Ollama isn't available — semantic is opportunistic, never a prerequisite. The on-disk format is unchanged; existing standalone `claude-index` installs coexist on the same file.
- New `🧹 Clear` button on each group chat row alongside the existing ✏️ rename and 📦 archive. Clears the chat's message history (header + sidecar preserved), writes a system log line marking the wipe, and explicitly nudges all participants so they re-engage with the fresh whiteboard. Useful when a session got stuck in a no-op loop and a clean slate kicks the conversation forward. Backend: new `POST /api/group-chats/clear` and `_group_chat_clear` helper; the nudge re-fires `_register_coordination` first so an idle-dropped chat comes back to life on clear.
- URLs in fenced code blocks, in inline code that mixes a URL with other text, and in inline Bash tool-result output (`tool-result-output` `<pre>`) are now clickable one-click anchors instead of plain text. New `_linkifyEscapedUrls(html)` helper post-processes already-HTML-escaped content and wraps `https?://...` substrings in `<a target="_blank" rel="noopener">`. Used inside `renderCodeBlock` / `highlightCode` and as a fallback inside the `renderInline` inline-code branch when the content has a URL alongside other text.
- Three changes to the group-chat sidebar workflow: (1) the reader pane now behaves like any other session view — clicking another conversation in the sidebar switches in-place instead of requiring a "← Back" click; (2) sessions can be dragged from the conversation list onto a chat row to add them as participants (new `/api/group-chats/add-participant` endpoint, `/group-chat` is auto-injected into the session); (3) clicking the new "+" button on the section header creates an empty group chat (server now accepts `session_ids: []` so a topic-only chat is valid).
- **Closed group chats stay visible until you archive them.** The "In Group Chat" sidebar section now shows both active *and* recently closed coordinations — closed rows are ghosted with a small "closed" pill so you can still open the reader and review the conversation. Each row gets a 📦 Archive button that persistently moves the chat into the per-repo Archived section (rendered inline alongside session rows with a 💬 icon prefix). Cross-repo group chats appear in the Archived section of every participating repo. New endpoints: `POST /api/group-chats/archive`, `POST /api/group-chats/unarchive`, `GET /api/group-chats/archived?repo_path=…`; the existing `GET /api/group-chats/active` now returns both active and closed (unarchived) chats with a `status` field, and the topbar badge counts only `status === 'active'` chats.
- Group chat sidebar entries now expose more "is this thing actually moving?" signal: (1) each indented participant row carries a "last activity" chip (time since the session's transcript was last touched), a yellow WIP chip when the session has an in-flight tool, and a "waiting" chip on whoever the orchestrator would nudge next; (2) the chat row itself shows the chat file's last-modified timestamp inline, plus a sub-line summary like `Watcher → waiting on CHUCK` so the next-expected speaker is obvious without opening the reader. Backend `_list_group_chats` now returns per-participant `participant_meta` (live/wip/pending_tool/last_activity) plus `last_author_hash`, `last_author_is_human`, and `waiting_on_hashes` for the row hint, mirroring the nudge-targeting logic so the UI summary matches what the watcher would actually do.
- **Click the model pill on a session card to switch model + context.** The model badge that used to be a read-only tooltip is now a button: tap it and a small popover opens with the right list for that session's engine — Claude (`opus-4-7`, `sonnet-4-6`, `haiku-4-5`, with a 1M-context toggle for opus/sonnet), Codex (`gpt-5.5`, `gpt-5-codex`, `o3`, `o3-mini`), or Gemini (`gemini-2.5-pro`, `gemini-2.5-flash`). An "Other…" text input lets you type any model the underlying CLI accepts, so unreleased models work the day they ship. For live Claude sessions (TTY or CCC-spawned) the picker injects `/model <alias>[1m]` straight into the running process via the existing `_inject_text_into_session` route — same plumbing as `/api/inject-input`. For Codex, Gemini, and dormant Claude (where the engines don't support runtime model swaps) the choice is persisted to a new `~/.claude/command-center/session-overrides.json` sidecar and applied as `--model …` on the next resume; the pill renders a small `→ next` chip until then. Backed by `POST /api/session/<id>/model` (and `/clear` to reset to the session default).
- Multi-session coordination: Ctrl/Shift-click sessions in the conversation list, click "Coordinate…", enter a topic, and Claude Code sessions self-organize via a fresh per-topic group-chat file. Live-reader panel in the conv pane lets you follow and participate directly from the CCC.
- Group chat rows now show their participants in an indented list directly below each chat header, with names pulled from the chat's `name_map`. Conversation rows whose session participates in any active or closed-but-unarchived chat get a new "💬 IN GROUP CHAT" badge in their signal chip row. Sessions in a chat used to be filtered out of the main In Progress list entirely; they're now visible there alongside the badge AND in the chat's indented list, so the user can see at a glance which conversations are currently coordinated.
- Each participant in the indented list under a chat row now has a small `×` button (visible on hover) that drops the session from the chat. New `POST /api/group-chats/remove-participant` updates the sidecar; the watcher's nudge loop reads `session_ids` fresh each tick, so the removed session stops being nudged immediately.
- Three related polish items for group chats: (1) message author tags now render as `<8-hash>: <name>` instead of bare hashes — both forwards (skill writes the new format directly using the chat sidecar's `name_map`) and backwards (the reader frontend's expansion converts old `— b1216dcf 👋` lines into `— b1216dcf: CHUCK 👋`); (2) each chat row gets a ✏️ rename button on hover that prompts for a new topic and updates the sidecar via a new `POST /api/group-chats/rename`, with a system log line marking the change; (3) the "+" button on the In Group Chat header no longer prompts — it creates a chat with the default topic "empty chat" and you rename it via the ✏️ button afterwards.
- Group chats now log every orchestrator action inline as a `> _<ts> — system: <action>_` line in the chat file: chat creation, add participant, remove participant, archive/unarchive, and per-tick nudges (with the list of pinged session names). Watcher feedback loop is suppressed by advancing the in-memory mtime baseline past each system write so administrative log lines aren't treated as participant activity — without that, every "pinged" line would itself trigger another nudge a minute later.

### Changed
- Stop the In Progress and GH Issues repo groups from reshuffling on every poll tick. When two folders' max-modified timestamps differ by less than 5 minutes, the previous-render order is preserved instead of swapping rank — so a fresh tool-call in repo B no longer bumps it above repo A every refresh. Brand-new folders still enter at their natural position.
- Tighten the `/group-chat` skill so participants don't bail on a quiet chat. The user explicitly invited them, and the chat header's topic line counts as a topic — sessions used to read an empty file, conclude "no topic, no participants" and immediately `👋 Leave`, leaving every chat dead-on-arrival when participants didn't wake up at exactly the same time. New rules: introduce yourself once with a `💬`, wait through re-injections, and only `👋 Leave` after either (a) actually engaging two-way and resolving, (b) 10 minutes of dead silence (real-meeting rule), or (c) the topic is plainly the wrong room. The Leave action's bullet now points back at the joining section.
- Tighten the `/group-chat` skill's joining rules further after observing sessions still bailing on first read. New strict rules: (1) one post per skill invocation, then exit — posting `💬 standing by` and `👋 Leave` back-to-back at the same timestamp is the bug being fixed; (2) don't evaluate the topic at all (placeholder topics, "Untitled" topics, etc., do not justify leaving — the user adds the real topic later); (3) re-arrival with no new content means exit silently, do not re-introduce; (4) explicit list of forbidden phrases ("no topic", "nothing to coordinate", "leaving — ping me later") that flag premature exits. `👋 Leave` is now allowed only on work-resolved or 10-minute timeout, never on topic evaluation.
- Merge the global topbar (Worktrees / Stats / Terminal / Vercel / History pills) into the main toolbar row, recovering ~33px of vertical space at the top of the page. The buttons now sit at the right end of `#convToolbar` instead of in their own fixed-position bar above the sidebar/main split.
- When a Human posts to a group chat, the watcher's nudge now pings ONLY the agent who wrote immediately before the Human (the most likely intended recipient of the reply) instead of fanning out to everyone except the Human. Pinging everyone caused N-1 sessions to waste a turn introducing themselves to a question that wasn't for them. Falls back to the everyone-except-last-writer behavior when the last author is an agent or there's no prior agent in the tail (fresh-thread case). The regex that detects authors now matches both `<8-hex>` agent tags and bare `Human` markers.

### Fixed
- Stop archived chats from being resurrected at server boot. `_start_coordination_watcher` re-registered every recently-modified chat regardless of its `archived` flag, so a chat the user explicitly archived via 📦 would silently come back to life on the next restart and the watcher would resume nudging participants of a chat they thought was closed for good. Worse: those participants might be in *another* active chat too, and would receive `/group-chat chat="<old-archived-path>"` injects for the wrong chat. The boot recovery now reads each chat's sidecar and skips entries with `archived: true`.
- **Context-usage pill now resets after `/compact`.** Previously the `ctx N / limit` figure stayed pinned to the pre-compact peak because the JSONL extractor walked every assistant turn and let `latest`/`peak` accumulate over the whole file — pre-compact turns no longer contribute to the live context window, so this overstated the displayed usage until the next post-compact assistant turn. Fixed by detecting the `{type: system, subtype: compact_boundary}` event Claude Code emits at each compaction (manual or auto) and resetting the running totals at that boundary. The pill now reflects only the post-most-recent-compact segment, matching what the user sees in the TUI. The extracted dict now also carries a `compact_count` field for downstream consumers.
- Stop hanging `/api/conversations/all` on cold cache — `_resolve_session_cwd` was running an `os.walk` per stale-cwd row inside the cross-project bulk scan, compounding into a multi-minute hang. Resolution now happens lazily via the per-repo `find_session_cwd` / `find_conversations` paths (already cached) instead.
- Two related fixes for group-chat coordination races. (1) `_register_coordination` was clobbering existing watcher entries — including resetting `last_nudge` to 0 — every time it was called. In the clear / add-participant flows that meant: between the register call and the explicit nudge that followed, the background watcher could tick, see the file changed AND `last_nudge=0` (debounce passed), and fire its OWN nudge, racing with our explicit one. Two `pinged …` log lines at the same second. Now the function preserves an existing entry's `last_nudge` and only refreshes `mtime` + `last_activity`. (2) Skill rule 0 added to Section 2: the chat file is the only source of truth — sessions must Read the file fresh inside the invocation before deciding whether they've already posted, never rely on conversational memory. Hopefully unblocks sessions like Chuck that hallucinate having posted when the file shows no such entry.
- Remove duplicate "In Group Chat" header in archive mode — `renderArchiveList` was injecting a second copy on top of the one `renderConversationList` already rendered, leaving two stacked headers in the sidebar whenever a coordination was active.
- Group-chat sessions were guessing their own identity when `$CLAUDE_SESSION_ID` was unset in their shell — picking the `name_map` entry whose display name "felt right" for their role, sometimes posting under another participant's hash. Two fixes: (1) the orchestrator's inject command now passes `sid="<full-session-id>"` explicitly, so the skill always has a reliable source regardless of shell env; (2) the skill's Identity step is rewritten as numbered substeps with explicit "do not guess" / "do not impersonate" wording — when sid is unavailable OR the hash isn't in `name_map`, the session is required to post a single `💬` flagging the problem and exit, never substitute another participant's identity.
- Fix the "In Group Chat" sidebar section not appearing after creating a coordination — single-session chats were blocked by a stale min-2 client check, the section refresh only fired up to 15s later, and the change-detector compared list lengths instead of paths so identical-count swaps were missed.
- Show the "In Group Chat" sidebar section on a clean reload. The polling was wired inside `setArchiveFolderFilter`, so it only kicked in when the user touched the folder filter — a fresh page load left `_gcActiveChats` empty and the section silently never appeared. Polling is now set up once at boot. Bonus: the same handler used to start a new 15s `setInterval` on every folder change, leaking timers; that's gone too.
- Fix session rows whose recorded cwd was moved by resolving the new folder from transcript file-path evidence.
- After viewing a group chat and clicking another conversation, the standard "Send to terminal" input box was missing. The reader had been replacing the entire conv-pane's `innerHTML`, which created new DOM nodes with the same IDs but orphaned the boot-time element references that other handlers were bound to. The reader now renders into `#conversationsView` only and toggles the surrounding input bar's visibility, so the input/send wiring stays live across reader open/close cycles. Also covers chat-message author hash IDs (e.g. `— 25ea49ae`) being expanded into display names in the reader, with the short hash shown alongside each name in the indented participants list.
- The watcher's nudge loop was pinging the most recent author on every tick, creating exactly the response loop the exclude logic was meant to prevent. The regex captured the full tail of the chat-header line (`b1216dcf: CHUCK 💬`) and looked it up in `name_map` by display_name (`CHUCK`) — every match missed, `exclude_sid` stayed None, last writer got nudged again, wrote a reply, watcher fired again, repeat. Match against the 8-char hash prefix instead and look it up against `session_ids` directly — works for both the new `<hash>: <name> <emoji>` format and the legacy bare-`<hash> <emoji>` one.
- Stop the watcher from firing nudges on chats that have gone quiet — the recent fix to ping only the addressed agent had a hole when the trailing chat window contained nothing but system `pinged` lines (no real author posts in the last 3K bytes). With no author detected, exclude/only logic stayed null, the nudge fell through to ping-everyone, wrote another `pinged` line, the next watcher tick saw that as a change and re-fired — self-perpetuating loop with no actual activity behind it. Two changes: (1) expanded the tail window from 3K to 12K bytes so the regex can see participant posts even after dozens of system entries pile up; (2) if the window still has no authors, the nudge skips with `{"ok": true, "skipped": "no recent author"}` — no inject, no log line, no follow-up tick.
- Make `~/...` paths in conversation messages clickable hyperlinks alongside the existing `/Users/...` paths, opening in the default editor via `/api/open`.
- Stop the watcher from continuously re-pinging participants in 60-second loops with same-second duplicate log lines. Two changes inside `_coordination_watcher`: (1) hold `_coord_lock` across the read-of-last_nudge AND the write that claims it, so concurrent ticks can't both pass the debounce check and both fire — eliminates the same-second `pinged …` dups; (2) belt-and-suspenders post-nudge baseline bump — after the nudge writes its log line, re-stat the file and overwrite `entry["mtime"]` so the next tick sees the post-log mtime as already-baselined and skips. The earlier in-line bump inside `_group_chat_log_system` should have covered this, but the loop kept happening; the explicit re-stat is defensive against any path that misses the bump.

## [3.0.0] - 2026-05-05

### Added
- New "All repos" toggle in the sidebar header. Switches the conversation list to a flat, reverse-chronological view of every conversation across every folder you've ever Claude-Code'd in (from `~/.claude/projects/`), each row tagged with its folder. Read-only — clicking does nothing yet, but you can search/filter to find lost conversations across folders without spawning servers for them. Toggle off to return to the active repo's session list.
- Added an alphabetical sort toggle (A↓) in the sidebar header. Click to sort sessions A–Z by title; click again or use the chronological sort button to switch back.
- Archive search input now matches against the session UUID, so you can paste a `session_id` (e.g. `9858e87d-73bd-419f-9e8b-5d89eb9db9a1`) and find the conversation directly. Useful when CCC tooling, logs, or external scripts surface a UUID without a title.
- Clicking a backlog issue or task card in the sidebar now renders an inline detail pane (state chip, labels, title, opened date, issue body as markdown) instead of leaving the conversation pane blank. Previously `/api/conversations/<backlog-id>` 404'd because there's no session JSONL, and the frontend never recovered.
- **Sidebar header reorganized + new ⋯ overflow menu in the conv-pane
toolbar.** The four conversation-list controls (Board / Archive / Sort /
Refresh) move from under the search box up into the sidebar's
"Claude Command Center" header row, packed into a `.sidebar-header-actions`
group with new `.sh-btn` styling. The empty space to the right of the
title was wasted before; this puts the always-needed controls a level
higher so the search-box row is just the search box. Adds a `⋯` overflow
button at the right edge of the conv-pane toolbar that opens a per-session
actions menu — currently surfaces "Move to repo…" (re-buckets the session
JSONL into a different repo's `~/.claude/projects/<slug>/` dir via a new
`POST /api/sessions/<sid>/move` endpoint, allow-listed against
`load_known_repos()`), and is designed to grow other per-session actions
later. The move endpoint uses `_encode_project_slug` so target dirs
match what current Claude Code writes (handles `+`, `.`, `_`, spaces —
the same regression `8216fae` fixed).
- **Codex placeholder card now persists and renders the run log.**
Codex `exec` is one-shot and writes no Claude-JSONL, so before this
the optimistic kanban placeholder vanished after 30s with no real
card to take its place — a codex spawn looked like it had failed
even though the run had completed. The placeholder is now permanent
for codex (no auto-cleanup; the user archives it manually), and
clicking it loads the spawn log into the right pane: parsed
`item.completed` agent messages, a token-usage footer, and a
collapsible stderr section. The pane polls `/api/sessions/spawned/
<pid>/log` every 1.5s while the codex process is alive and locks
to the final transcript on exit. New endpoint:
`GET /api/sessions/spawned/<pid>/log` returns
`{ok, pid, engine, log_path, text, running, exit_code}` looked up
from the in-memory spawn registry. State is client-side only — a
page reload still drops the card; full codex JSONL ingestion remains
the proper follow-up.
- **OpenAI Codex as a spawn engine.** The kanban toolbar now has an
**Engine** dropdown (`claude` | `codex`) where the old `pkood spawn`
checkbox used to live, and the new-session modal mirrors it.
Selecting `codex` routes the next spawn through `codex exec --json
--dangerously-bypass-approvals-and-sandbox` instead of `claude -p`,
runs in the chosen working directory, and tracks the child on the
same kanban with a green `codex` chip.

Codex spawns are fire-and-watch in this iteration — no mid-run
inject (Codex `exec` is one-shot), no `claude --resume`-style
jump-in, and Codex JSONL ingestion isn't wired up yet. The
selector greys out automatically when the Codex CLI binary
can't be located (looked up via `$CCC_CODEX_BIN` →
`which codex` → `/Applications/Codex.app/Contents/Resources/codex`).

The `pkood:` prompt-prefix shortcut and `/api/pkood/spawn` endpoint
are unchanged. New endpoints: `POST /api/sessions/spawn-codex`,
`GET /api/sessions/spawn-codex/availability`. New env vars:
`CCC_CODEX_BIN` (binary override), `CCC_CODEX_MODEL` (model name,
default `gpt-5.5` — verified at release time against
codex-cli 0.125.0-alpha.3; note that `gpt-5.5-codex` is rejected
with a ChatGPT account).
- Codex sessions now appear as first-class conversation cards from Codex's durable thread store, with normal transcript viewing, live tailing, terminal launch, and input resume flows.
- **Drag-to-split conversation pane.** Drag a conversation card from the
  sidebar list (or a kanban column) onto the right edge or bottom edge
  of the chat pane to open a second conversation alongside the current
  one — vertical or horizontal split. Each pane has its own composer,
  send button, and SSE stream. Click the `×` in a pane header to close
  it; the survivor expands back to full width. Two-pane max; below
  900px viewport the split collapses to single-pane.
- Added a clear button to the conversation search box so filtered sidebar views can be reset in one click.
- **Cost pill in the conv-pane input strip.** Next to the existing `ctx` pill,
a small `$0.34` chip surfaces the Anthropic API list-price equivalent for
the session's tokens. Hover for a per-category breakdown (input, cache
write, cache read, output) with token counts. Subscription users (Claude
Pro/Max) pay flat, but the figure is the cleanest cross-model "how
expensive was this session" comparison. Server: `extract_session_usage` now
returns `cost_usd`, `cost_breakdown_usd`, and the per-category token totals
on `/api/session/<id>/usage`. Rate table covers Opus 4 / Sonnet 4 / Haiku 4
and falls back to Sonnet rates for unknown models.
- New `GET /api/issues/all` endpoint returns open + recently-closed GitHub issues across every known repo (recent ∪ pinned), in parallel via a thread pool with a 5-minute per-repo cache. Each issue is tagged with `repo_path` + `repo_label` so click-to-spawn knows the cwd. Per-repo failures (no gh auth, missing dir, no remote) land in an `errors` map without breaking the whole call. Foundation for the upcoming cross-repo Issues UI section in archive view.
- Cross-repo GitHub issues now appear in the All-repos view's existing GH Issues section. Each row carries its repo's folder chip; the "Start" button spawns a session in the issue's own repo rather than relying on server-global repo state. Open issues only in v1 — closed ones are filtered out client-side. Folder filter dropdown narrows to a specific repo's issues. Archive button is hidden on cross-repo issue rows since closing a foreign-repo issue requires its own context; switch to that repo to manage its issues. Powered by `/api/issues/all` (5-min per-repo cache).
- Files from this conversation — header pill listing every image, PDF, doc, presentation, video, MD, and HTML mentioned in a session, openable in one click via macOS default app (local) or new browser tab (URLs).
- Add Gemini CLI as a third session engine with discovery, transcript viewing, token usage, spawn/resume, and activity/commit signals.
- Added a GH Issues refresh control so the sidebar issue list can be reloaded without refreshing all conversations.
- Sidebar row list now opens with a collapsible "GH Issues" section at the top — open GitHub issues plus TODO.md / PARKING_LOT cards with no session yet, mirroring the kanban column of the same name. Below it sits a new "In progress" section that wraps the active sessions, then "Archived" at the bottom. Sessions linked to a GH issue stay in "In progress" with a muted `#N` chip on the row, so the count in "GH Issues" reflects only un-started work.
- **Search past conversations from CCC.** A new 🔎 History button in the
top toolbar (shortcut: `/`) opens a right-side drawer that runs BM25
keyword search across every Claude Code session that has been indexed
by the separate `claude-index` tool. The drawer reads
`~/.claude-index/index.db` opened with `mode=ro` so CCC can never
mutate the index that claude-index owns.

The drawer shows BM25-ranked results on the left with `<mark>`
highlighted snippets; clicking a row opens the full message — with
metadata (session, cwd, branch, model, source-file) — in the
click-through pane on the right. Filters: time window
(All / Today / 7d / 30d) and a "this repo only" toggle pre-filled
from the current CCC workspace.

Bare multi-word queries are auto-OR-rewritten so a single missing
word can't zero out the result set; explicit FTS5 operators
(`"quoted"`, `OR`, `NEAR`, `prefix*`) pass through unchanged. When
the index hasn't been built yet, the search returns a friendly
empty state pointing at `claude-index`.

New endpoints: `GET /api/search-history?q=&since=&cwd=&limit=`,
`GET /api/history-message?uuid=`. No new runtime dependencies —
read-only `sqlite3` is stdlib. The Ollama / hybrid-vector search
path is intentionally **not** part of this change; CCC stays a
keyword-only consumer of the index.
- **Conversation history search now augments the sidebar list inline.** Typing in the "Search conversations" input still does the existing instant local filter (display name / first message / branch / source). 180 ms after you stop typing, the local claude-index FTS5 store is queried in the background; sessions that matched there get a small "history" badge next to their title and a snippet line previewing why they matched. Sessions that exist only in the index (other repos, older work not currently loaded) appear as synthetic rows trailing the local matches. Falls back silently when the index is missing or the request fails — zero degradation for users who haven't installed claude-index. Snippet preview strips `[tool_use:NAME]` markers, cat -n line-number prefixes, and markdown-table separator rows that previously dominated FTS5 snippets. Works in both single-repo list view and All-repos archive view.
- Added: server background thread reaps idle `claude` sessions every 30 min — SIGTERMs any process whose JSONL has had no user/assistant/result event in the last 24h. Activity is measured via `last_meaningful_ts` (not file mtime), so administrative writes like `/rename` and a long-running agent that's still emitting messages don't count as idle. Catches the long tail of sessions that were abandoned without archiving and forgotten cron agents that the archive-time kill never sees. Tunable via `_IDLE_REAPER_AGE_HOURS` / `_IDLE_REAPER_INTERVAL_S` constants in `server.py`.
- **In-UI terminal panel.** A new ⌨ Terminal button on the topbar opens a
small one-shot terminal at the bottom of the page — type a command, hit
Enter, output streams back. `cd` is parsed server-side so the prompt's
cwd survives between commands; the path is clamped to the selected repo so
`cd /etc` is rejected. Cancel kills the whole process group, so a
runaway `make -j` or `./deploy.sh` doesn't leave orphans behind.
Up/down arrows recall the last 50 commands. Hotkey: Cmd/Ctrl+`.

Not a real PTY — `vim`, `top`, and any program that prompts for
interactive input will hang. Use `--yes` flags, pipe input on the
command line, or run those from a real terminal.

New endpoints (gated by the existing same-origin check): `GET
/api/term/cwd`, `POST /api/term/run` (SSE), `POST /api/term/cancel`.
This is the most security-sensitive surface in CCC — strictly more
powerful than `/api/inject-input` because there's no Claude permission
prompt in the loop. Do **not** enable network bind (`CCC_BIND_HOST=
0.0.0.0`) without a trusted network. See
`docs/superpowers/specs/2026-05-01-in-ui-terminal-design.md`.
- Conversation input bar now has an **Esc** button next to the send button. Clicking it sends an interrupt to the selected session via the new `POST /api/inject-esc` endpoint. For live Terminal/iTerm2 sessions it lands a real Esc keystroke (cancels Claude Code's in-flight response, or clears the input buffer if nothing is streaming). For CCC-spawned headless sessions with no TTY it sends `SIGINT` to the spawned `claude -p` subprocess — note this terminates the spawn entirely rather than just cancelling the current message. Hidden for pkood agents and for dormant/new-session/backlog-issue states where there's nothing live to interrupt.
- Render `.claude/pasted-images/paste-*.{png,jpg,…}` paths as inline images in the "Original ask", "Earlier ask", and user-message panels instead of leaving them as bare filesystem paths. Backed by a new `/api/pasted-image` route, sandboxed to `~/**/.claude/pasted-images/`.
- **`./run.sh --install-service` (macOS).** Installs CCC as a launchd
agent under `~/Library/LaunchAgents/com.github.claude-command-center.plist`
so it starts at login and survives reboots. Bakes in whatever `PORT` and
`CCC_*` env vars were set when you ran it. Re-run to update config;
remove with `./run.sh --uninstall-service`. Logs go to
`~/.claude/command-center/logs/service.{out,err}.log`.

Refuses to install if the target port is already bound by something
other than a previous version of the agent — avoids silent crash loops
where launchd's `KeepAlive=true` would mask a port collision and retry
forever. Post-load, polls the port for up to 2.5s to verify the service
actually came up, instead of trusting `launchctl load`'s return code.

The README's Quickstart now documents both commands as the canonical
flow: `./run.sh` to try it, `./run.sh --install-service` to keep it.
- Bottom input bar now appears when viewing a backlog GitHub issue in the right pane. Typing a prompt and submitting spawns a session for that issue — equivalent to clicking "Edit & start" on the kanban card, with your text appended to the standard "Fix issue #N — TITLE / Run `gh issue view N`" preamble.
- **macOS native notifications when Claude needs your attention.** The
`Stop` and `Notification` hooks now fire `osascript display notification`
banners alongside their existing sidecar writes, so you see a system-tray
ping even when CCC isn't focused (or is on another desktop space). Two
events:

- **Claude finished a turn** (Stop hook) → "Ready for your input" banner
  with the session-id prefix as subtitle.
- **Claude needs approval** (Notification hook) → "Claude needs your
  approval" banner with the permission-prompt message as the body.

Opt-out via `CCC_NOTIFY=0` in the shell env. Falls through silently on
non-macOS systems (no `osascript` on PATH). Banners are fire-and-forget
via `subprocess.Popen` — hooks never block on notification delivery.
Browser-side `Notification` API can come later as a follow-up; this
covers the "I'm on my Mac and switched away from CCC" case which is the
most common one.
- Sidebar Merge button now offers auto-rebase recovery when a PR fails to merge with conflicts. The toast becomes a confirm dialog ("PR #N has merge conflicts. Auto-rebase against the PR base and retry? This force-pushes with --force-with-lease."). On confirm the server finds the worktree on the head branch, refuses if it's dirty, fetches the PR's base ref via `gh pr view --json baseRefName`, rebases (aborts cleanly on text conflicts), force-with-lease pushes, retries `gh pr merge --squash`, and auto-archives on success. Only the rebase-without-conflict case auto-completes; semantic-but-clean rebases are still possible — same trade-off as any rebase. Endpoint: `POST /api/conversations/{id}/rebase-merge`.
- Topbar repo picker now shows live CCC servers in a "Running" section (one entry per peer in the registry, with port). Selecting a peer navigates to that server's page. Repos you've used but aren't currently a CCC server appear under "Switch this server to…" — selecting one performs the legacy one-off switch on the active server, no new process spawned. Picker auto-refreshes every 10s so siblings starting later show up without a reload.
- Vertical repo sidebar on the left edge: one circular icon per known repo. Running CCC servers appear first (click to navigate to that server's page); known-not-running repos appear below a divider with a dimmer dashed style (click to switch this server's repo, the legacy one-off flow). Active server is highlighted. Hidden when no repos are visible.
- **PR merge-state badge on kanban rows.** Sessions that ran `gh pr create`
now show a state-aware chip in the row's signal slot:

- `↗ PR #14` (cyan) — open
- `✓ PR #14` (purple) — merged
- `× PR #14` (muted) — closed without merge

State is fetched once per unique PR URL via `gh pr view <url> --json
state,mergedAt`, cached for 60s so the kanban refresh cadence (~10s)
doesn't shell out per row per poll. Cross-repo / fork PRs work because
gh resolves the repo from the URL itself. The chip now renders for *any*
session with a captured PR (previously gated to worktree rows only) —
which matches the actual user question of "did the PR I opened get
merged?".

Cache busts automatically when CCC's own merge button calls `gh pr
merge`, so the badge flips immediately. Web-UI merges still take up to
60s to surface (next cache expiry).

New fields on `/api/conversations` rows: `pr_state` ("OPEN"/"MERGED"/
"CLOSED"/""), `pr_merged` (bool), `pr_merged_at` (ISO 8601 string).
- Sidebar list view has a new "Ready to merge" section between GH Issues and In progress: collapsible, green-tinted count badge, and contains every session whose work has landed in a recorded PR (`tail_pr_number`). Lifts merge-ready sessions out of "In progress" so the highest-leverage clicks aren't buried under live work.
- All-repos view: drag a row onto another repo's group header (or a row in another group) to pin the session there. The pin is visual-only — the JSONL transcript and recorded cwd are untouched — but the row will appear under the pinned repo in both the all-repos archive and the destination repo's single view, and disappear from the original repo's single view. A 📌 indicator on the row clears the pin. Persisted to `~/.claude/command-center/repo-pins.json`. New `POST /api/repo/pin` endpoint, allow-listed against the repo picker.
- GH Issues rows in the sidebar list now have a green **Start** pill (spawns a session for the issue, same as the kanban "Start session" button) and the row's archive button is relabelled **Close** so it matches what actually happens — the GH issue is closed "not planned".
- Sidebar conversation rows now show a small Merge button (🔀) immediately to the left of the archive button, visible only when the row plausibly has an open PR (a recorded PR number from `gh pr create`, or a `pushed` signal on a non-default feature branch). Clicking confirms, then runs `gh pr merge --squash` against the recorded PR number (or branch as fallback) in the session's working directory. Branch cleanup is intentionally left to the worktree-removal flow — `gh`'s `--delete-branch` fails on worktree-checked-out branches and surfaces as a misleading "Merge failed".
- "+ New session" now exposes a folder dropdown above the input box so you pick where the new session will land before submitting. Default = the active folder filter when narrowed to one repo, or the first known repo when the filter is "All"; selection persists in localStorage. Previously `spawnFromInlineInput()` could silently use an implicit server repo regardless of which folder you were viewing.
- **Live block-level streaming** for CCC-spawned headless sessions. The
conv pane now tails the spawn log's stream-json events as they happen
and renders prose blocks + tool calls in a transient "streaming"
bubble at the bottom, instead of waiting for the JSONL transcript's
end-of-turn write. A green pulsing `live` badge next to the Launch
button indicates the spawn-log tail is active. New endpoints:
`GET /api/session/<sid>/spawn-info` (capability check) and
`GET /api/session/<sid>/spawn-stream` (SSE). Externally launched and
pkood sessions are unaffected — they still render from JSONL only.
- Stats overlay: a "Stats" button in the topbar opens an Overview/Models panel summarising every Claude Code transcript on the machine — sessions, messages, total tokens, active days, current/longest streak, peak hour, favorite model, and a 7×24 day-of-week × hour activity heatmap. Range filters (All / 30d / 7d), with per-file aggregates cached by mtime so range switches are instant.
- **Subagent-worktree alert dot** on the topbar Worktrees button. When
superpowers / orchestration skills have spawned locked agent worktrees
the user may have forgotten about, an orange dot appears on the
button. Polls `/api/repo/worktrees` every 60s; the badge tracks
`agent_count > 0` and the button's tooltip surfaces the count.
- Sidebar session rows now show two side-by-side "uncommitted" pills: a solid `tools` pill driven by tool-event tracking (Edit/Write seen, no commit yet) and an outlined `git` pill driven by ground-truth `git status --porcelain`. Both are rendered while the signals are being watched for divergence — a row showing only one of them flags a gap between what the agent thinks it did and what git sees.
- **Worktree-per-spawn checkbox.** A new `🌿 worktree` toggle next to the
existing `pkood spawn` toggle (in both the inline new-session row and the
new-session modal) lets you launch the session in a fresh git worktree on
a `feat/<slug>` branch, isolated from main. When enabled, CCC runs `git
worktree add <repo-parent>/<repo-name>-wt-<slug> -b feat/<slug>` against
the source repo before spawning Claude there — so the agent literally
cannot accidentally commit to main even if it ignores the multi-agent
git-hygiene rules. Path collisions get a numeric suffix (`...-wt-foo-2`),
branch collisions get the same suffix on the branch name. New optional
`worktree: bool` field on `POST /api/sessions/spawn`; response gains
`worktree_path` and `worktree_branch` when applicable. `pkood` spawns
ignore the flag (out of scope).
- **🌿 worktree toggle in list-view new-session bar.** The same `🌿 worktree`
checkbox that already lives in the kanban-toolbar new-session modal now also
appears in the input-context strip when the list-view "+ New session" button
puts the bar into new-session mode. Previously this entry point spawned via
`spawnFromInlineInput` with no `worktree` flag, so list-view users had no way
to launch an isolated `feat/<slug>` worktree without switching to the kanban
view first. When checked, the inline path POSTs `worktree: true` to
`/api/sessions/spawn` exactly like the modal does (codex spawns still ignore
the flag, matching the modal's precedent).
- **Open-PR visibility in the Worktrees modal.** Each worktree row now
shows a `PR #N` badge (linked to GitHub, with `draft` flavour for draft
PRs) when its branch matches an open PR's head ref. A new "Open PRs
without a worktree" section lists open PRs whose branch has no local
worktree, so nothing is hidden. Powered by `gh pr list` cached for 30s
on the server, surfaced via the existing `/api/repo/worktrees`
endpoint (new fields: `open_prs_count`, `orphan_prs`, plus a `pr`
field per worktree entry).

### Changed
- `/api/ask` now uses a live TTY keystroke plus JSONL-tail path for active Claude sessions, avoiding a fresh `claude --resume` subprocess while dormant sessions keep the existing headless resume flow.
- Sidebar: archiving the currently-open session now auto-selects the next active row (or the previous one if it was at the bottom) so you don't land on a blank pane.
- "All repos" is now the default sidebar view; opt-out persists in localStorage so toggling off sticks across reloads.
- Within the "In progress" section, conversations from the last 24 hours are grouped under a small folder chip header (freshest folder first), so you can scan what's hot in each repo without hopping. Cards older than 24 hours fall below a divider and continue as the existing flat chrono list with gap separators. Single-repo mode is unchanged.
- Replaced the All repos toggle with a persistent archive folder filter that narrows the conversation list without switching server repos.
- Archiving a row now SIGTERMs its headless `claude -p` agent. Previously the agent (plus its MCP children) stayed running indefinitely after archive, accumulating across days of use. Resume via Jump (`claude --resume <sessionId>`) is unchanged and still rebuilds full context from the on-disk JSONL — no work is lost.
- Fixed: `_kill_session_by_id` was looking up the wrong field name (`session_id` instead of Claude's `sessionId`), so every call returned "no process found" and killed nothing. Its only existing caller (Morning view's active→dormant drag) has been silently broken since it was written.
- Fixed: `_kill_session_by_id` now signals **all** PIDs registered against a session, not just the first. Jump spawns a new `claude --resume <sid>` process while the original headless agent is still alive, so two PIDs share the sessionId — archive previously left one alive. Each PID is also `ps`-validated to be a `claude` process before being signaled, so recycled PIDs can't take out unrelated processes.
- Aligned archive project group chips with the row time column and retired the alternate Board toggle from the sidebar.
- Show Codex result token counts in conversation turn footers instead of an unknown cost placeholder.
- Replaced the bold Codex row background in the conversation list with a small inline Codex marker on the metadata row.
- Made Codex sessions visually distinct with blue-tinted sidebar rows and a blue conversation-pane accent.
- Sidebar resizer now allows the conversation pane to shrink to ~200px (was capped at 40vw). Toolbar buttons wrap onto multiple rows as the pane narrows. The kanban-split conversation panel can also be dragged narrower (floor 40px), with the session UUID, font-size buttons, and live/desktop controls hiding via container queries as space tightens.
- Cut /api/conversations cold-scan from ~135 s to ~6.6 s on large repos by hoisting the per-row `git rev-parse` cache out of the loop, persisting `_conv_meta_cache` to `~/.claude/command-center/conv_meta_cache.json` across server restarts (mtime-keyed, atomic writes), and adding a 30-day activity filter on `last_meaningful_ts` (`?include_old=1` bypasses; `CCC_MAX_CONV_AGE_DAYS` overrides).
- Pinned the title-summarizer and morning-braindump `claude -p` callers to a stable `~/.claude/command-center/scratch/` cwd so their throwaway session JSONLs no longer pollute the user's project conversation store. Old throwaways in the scratch slug are auto-deleted at server startup after 7 days (`CCC_SCRATCH_GC_DAYS` overrides).
- Added a concurrency guard on `find_conversations()` so the browser's 10-second `/api/conversations` poll doesn't pile up duplicate cold scans during a slow first request.
- **Conv-pane sticky header now tracks the most recent user message you've
scrolled past, and auto-sizes to fit that message.** Previously the sticky
pinned the *first* user message ("Original ask") at a manually-resizable
fixed height. Now, as you scroll down past later user messages, the sticky
body swaps to whichever user message has just fully cleared the sticky's
bottom edge, and the label flips from "Original ask" to "Earlier ask". The
"Original ask" rendering keeps its first-sentence/grey-rest split; "Earlier
ask" shows the full message in regular weight (no headline split for ad-hoc
later turns). The drag-to-resize handle at the bottom of the sticky is gone
— the box auto-sizes to whichever message it's currently showing, since
the swapping content makes a hand-tuned fixed height meaningless. Implemented
via a `requestAnimationFrame`-throttled scroll listener on
`.conversations-view`; only top-level user_text rows are tracked (messages
nested inside collapsed tool-call groups are ignored). Side effect: the
first user message's in-conversation chat bubble is hidden via a
`.is-pinned-in-sticky` class — it's already permanently rendered in the
sticky as "Original ask", so showing both was redundant.
- Engine picker (claude vs codex) now sits inline next to the new-session prompt — in the sidebar's bottom input bar (occupies the Esc slot in `__new__` mode) and the Kanban toolbar — instead of being buried in the View ▾ menu. All selectors stay in sync via `localStorage.ccc.spawnEngine`.
- GH Issues now shows five issues per project by default with a Show more control for longer project lists.
- Load recent and live session cards first so large transcript histories no longer block the initial board render.
- The sidebar **+ New session** button now opens an empty conversation pane on the right (with the input bar focused) instead of a full-screen modal. Type a prompt, press Enter, and the new session is spawned. The previous modal flow remains available from other entry points.
- Combined terminal and app resume controls into one Launch split button with Terminal, Claude Desktop, and Codex destinations.
- Sidebar Merge button shows a friendlier toast when `gh pr merge` fails on a conflicted PR. Was: `Merge failed: GraphQL: Pull Request has merge conflicts (mergePullRequest)`. Now: `Merge failed: PR has merge conflicts — resolve locally (rebase/merge main, push), then retry`. Raw `gh` stderr is still returned in the response (`data.stderr`) for debugging.
- Sidebar Merge button now asks the row's session to do the merge when it's still alive, instead of running `gh pr merge` directly. The session carries the original spawn instructions (e.g. "do not merge until LCP confirmed"), the test-plan invariants, and the worktree context, so it can refuse, suggest verification, or merge-and-clean-up using its own judgment — exactly what happens when you ask manually. Closed/dormant sessions still go through the direct `gh pr merge <url>` path.
- The sidebar **+ New session** action is now a small button next to the "Claude Command Center" title (matching the rest of the header action cluster) instead of a full-width box above the deploy panel. Behaviour is unchanged — it still opens the inline new-session pane on the right.
- **Dropped the `Inactive` column; replaced with a small "no edits" chip.**
  Sessions that used to land in `Inactive` (dead, no commits, no edits) now
  sit inside `Working`. A small lowercase blue **"no edits"** chip — sitting
  alongside `pushed` / `committed` in the list view, and next to the stage
  chip in the kanban — flags any session whose Claude has never touched a
  file. Liveness deliberately doesn't matter: a freshly-spawned session with
  no tool calls yet shows the chip just like a dormant shell does. Driven by
  a small `hasNoEdits(c)` helper: `!c.has_edit && !c.verified && !c.archived`
  (no labels, no stage, no liveness checks — the chip describes one thing).
  Stale `inactive` localStorage overrides drop on first render.
- "Original ask" sticky header is now capped at 25 % of the viewport with internal vertical scroll. Sibling-spawn prompts can run 50+ lines and were pushing the actual conversation events off-screen; the box now scrolls inside itself, and a manual drag of the resize handle still wins.
- "Original ask" sticky-header text now skips the sibling-spawn preamble ("You are a sibling Claude Code session…", sandbox rules, footguns) and starts from the embedded `## Feature:` / `## Task:` / `## Goal:` heading. The boilerplate is identical across every spawn — burying it makes the actual task scannable.
- Show completed read-only agent sessions as read-only instead of no edits so helper/subagent work does not look idle.
- Renamed the kanban "Backlog" category to "GH Issues" to make its source clearer. The internal column key (`backlog`) and saved column order are unchanged.
- Rename-saved toast now anchors to the bottom-left of the viewport instead of the bottom-center, so it no longer overlaps the conversation pane's input box.
- Breaking: Repo-scoped API calls now require an explicit repo path, session-derived context, or an all-repos aggregate endpoint; the old server repo-switch flow now returns a deprecation error instead of mutating process state, and `CCC_WATCH_REPO` is no longer used. **Migration:** scripts that used `POST /api/repo/switch` should pass `repo_path` (or `cwd`, or `session_id`) directly on the repo-scoped endpoint they were targeting next; missing repo context now returns `400 repo_required`. Aggregate endpoints (`/api/conversations/all`, `/api/issues/all`, `/api/repo/list`) take no repo argument and continue to work as before.
- Simplified Settings to appearance, network access, and help, and moved recent In Progress filtering into a 1d/7d sidebar toggle.
- Sessions spawned by the sibling-orchestrator skill ("You are a sibling Claude Code session…") now auto-title from the embedded `## Feature:` / `## Task:` / `## Goal:` heading instead of the boilerplate preamble. Sidebar rows, sticky header, and kanban cards all show e.g. "Feature: in-app bug reporting" instead of "you-are-a-sibling-claude-code-session-…".
- Sidebar conversation rows now keep the branch chip (`main` or 🌿 worktree) flush right next to the archive button, so the branch is always the last thing on the line. Lifecycle chips (`committed` / `pushed`) move to the left of it instead of after it.
- Tightened sidebar row chip clusters so status, PR, branch, and engine chips sit flush together without inter-chip gaps.
- Sidebar header now shows a compact Vercel deploy pill (status detail moved to its hover tooltip), and the "+ New session" button takes the prominent slot below the header where the Vercel panel used to live.
- **Sidebar row cleanup** — chips, branch pill, and archive grouping.

Chips: dropped `working` / `idle` / `waiting for input` / `planning` /
`coding` and the non-pkood `blocked`. The yellow live-tool pill already
shows what a session is doing right now, so the activity chips were
redundant; `planning` and `coding` were defaults dressed as signals.
Non-pkood rows now show 0 chips by default and just `committed` /
`pushed` when those carry meaning. Pkood rows keep their full state
machine (`running` / `idle` / `blocked` / `stuck`) since pkood owns
that truth.

Branch pill: worktree-aware. When tool-call inference detects that a
session is editing in a different worktree than its launch cwd
(launched in shared clone, but `Edit` paths land in `feat/x`), the row
shows the inferred branch in orange with a 🌿 leaf instead of the
launch branch in purple. Sessions launched directly inside a worktree
get the same treatment via a cheap `.git`-is-file check. The inference
is cached by `(session_id, jsonl_mtime)` so idle sessions don't repay
the JSONL walk on every refresh.

Archive section: archived rows now sit in a collapsible `Archived (N)`
section at the bottom of the list (default collapsed, state in
`localStorage`), instead of being filtered out by a top-bar toggle.
Same source of truth as the kanban Archived column, so tapping the
per-row archive button drops the card to that section visibly.
- Declutter the session sidebar by hiding legacy view/sort controls, moving repo switching beside All repos, and folding appearance/view options into Settings.
- Single-session project groups now render as inline rows with the repo chip before the session title.
- **Headless spawns survive CCC restart.** Replaced `subprocess.PIPE`
for `claude -p` stdin with a FIFO opened RDWR (`<log>.stdin`). Because
the child inherits the RDWR fd as fd 0, the kernel's writer count
stays ≥ 1 for the FIFO's lifetime, so a CCC restart no longer EOFs
the subprocess. The reattach sweep reopens a fresh writer end from
`entry["fifo"]`, restoring the inject channel to long-running agents.
The on-disk spawn registry now persists the FIFO path; FIFOs are
unlinked when their subprocess exits. Pre-FIFO entries reattach
without an inject channel — same behavior as before.
- Sticky header slots are now adaptive instead of always splitting 50/50. The "Earlier ask" sub-block collapses to zero height until you've scrolled past a later user message, so the "Original ask" body uses the full left-column height when there's nothing else to show. When an "Earlier ask" exists *and* the right-hand "Session activity" column is empty (no commits, pushes, or PRs in this session yet), the Earlier ask is promoted into that empty right column — Original ask on the left, Earlier ask on the right, top-aligned and using the full column height instead of stacking under the original.
- Sticky header: merged "Original ask" and "Session activity" into a single fixed-height panel with a vertical divider; each column scrolls independently if its content overflows.
- Auto-generated session titles now skip a leading file path or URL when the prompt begins with one, so a pasted screenshot path no longer dominates the card title.
- Changed conversation title clicks so inactive titles select the conversation first and only a second click starts rename.
- Renamed the kanban "Working" column to "In progress" so it matches the new sidebar section header. Internal column key (`working`) and saved column order are unchanged.
- **Workspace strip shows a single pill** instead of "launch cwd · via
tool calls · effective cwd". The strip's job is to answer "where does
this session's `Edit` actually go?" — now it does that with one pill,
preferring the tool-call-inferred effective cwd when it differs from
the launch cwd, falling back to the launch cwd otherwise. A small
"inferred from N/M tool-call paths" tooltip on the kind label keeps
the disclosure without spending real estate on a second pill. Removed
the `+N worktrees (X subagent · Y manual)` button from the per-session
strip — the topbar Worktrees button is the single entry point.
- Worktree sidebar rows now show a `PR #N` chip (linked to the PR Claude opened with `gh pr create`) instead of the generic `committed`/`pushed` chip when the PR number is detectable.
- **Worktrees pill** in the input-context strip is now a clickable button
that opens a real modal listing each sibling worktree (path · branch ·
agent/locked/detached tags · lock reason) instead of relying on a
native browser tooltip. The modal is keyboard-dismissable (Esc) and
backdrop-clickable.

### Fixed
- Drag-to-open another conv pane now actually fires. The drop overlay's `dataTransfer.dropEffect = 'copy'` did not match the drag source's `effectAllowed = 'move'`, so per HTML5 DnD spec the browser silently cancelled every drop — `drop` never fired. Aligned the overlay to `'move'`. Bug had been present since the original drop-overlay commit (`bb4f8f5`).
- Closing a split conv pane no longer leaves the survivor at half height. `renderSplitLayout()` was clearing the divider and extra panes when collapsing back to single-pane, but the inline `style.flex = '<ratio> 1 0'` set by the divider drag stayed on the survivor. With `sum(flex-grow) < 1`, the spec only distributes that fraction of free space, so the pane rendered at the dragged ratio with empty space below. Now clears the inline flex on collapse.
- Spawning a Codex or Gemini session from the list-view inline input now auto-jumps the right pane to the new placeholder card so the spawn-log stream renders. Mirrors the kanban-toolbar dispatch — without it the pane stayed on the "Spawning new session…" empty state and made the spawn look broken even though the agent was running.
- "All repos" now keeps its loading state during the first cold archive scan instead of briefly showing "No conversations on disk."
- All Repos rows now preserve resolved PR state before reusing the sidebar renderer, so merged or closed PR sessions no longer linger in Ready to merge just because they once recorded a `PR #N` chip.
- Rows with a recorded PR now show `PR #N` even outside worktree rows, so the remaining Ready to merge entries explain why they are actionable.
- The All Repos scanner now defines its recent-session probe window at module load, avoiding a fresh-process `NameError` while building archive metadata.
- "All repos" archive cold scan went from ~20–29s to ~12s on a ~940-session library by skipping the per-session git inference (`_infer_effective_repo`) for sessions older than the 3-day pills window. Old sessions can't have `cd`'d into a different worktree since "now," so their JSONL-header `cwd`/`gitBranch` are still accurate; only recent sessions need the inference walk. Warm cache hits remain ~0.1s.
- Boot kick for the cross-folder archive now waits for the active repo's `/api/sessions` to return before firing `/api/conversations/all`, instead of racing it. The two were sharing CPU/subprocess slots in the same Python process and dragging each other out — `/api/sessions` from <1s up to ~3s during the contention. The active repo is now interactive immediately on boot, then the archive populates the sidebar.
- Loading overlay copy now matches what the boot is actually waiting on. With archive mode as the default, the cold scan is the cross-folder JSONL walk, not `/api/sessions` — the overlay says "Loading conversations… Scanning Claude Code transcripts across every folder. Faster on subsequent loads." instead of the misleading "Loading sessions…".
- Fixed grouped archive rows so size, status, and branch chips stay aligned in stable right-side columns.
- Conversation archive filtering now keeps the `GH Issues` and `Ready to merge` sections visible in All view and when narrowed to a project, using the same client-side folder filter as the rest of the sidebar.
- Archive rows now use the last real transcript activity instead of metadata-only file rewrites, so old renamed sessions no longer appear freshly active.
- Archiving a row from the sidebar list now actually moves it to the Archived section even when the session has a pending Notification-hook approval marker. Previously the `needs_approval` flag pinned the row to "In progress" (via the Waiting kanban column) and only the archive icon flipped to ↩, making it look like archive had been undone.
- Fixed Board button text overlapping the archive button in the sidebar header — stale 28px width from the icon-only era was clamping the button width.
- Server no longer dumps `BrokenPipeError` / `ConnectionResetError` tracebacks when the browser disconnects mid-response (typical for hard reloads or tab closes during an in-flight `/api/sessions`). Swallowed at the request-handler level — the underlying disconnect was always benign; only the noise was a problem.
- Show the real failure reason when Close & announce cannot inject its command into a session.
- Show Codex thinking, active-tool activity, and pending spawns in conversation list rows, preferring the exact running tool name and falling back to the yellow WIP signal when no tool is known.
- Hide stale Codex pending-tool activity chips once the Codex session is no longer live.
- Codex session summaries that report an opened PR, branch, and worktree now populate the same sidebar metadata as Claude sessions, so they show `PR #N`, render worktree branch indicators, and land in Ready to merge.
- Codex spawns now run `codex exec --ephemeral` so CCC's fire-and-watch
  path does not trigger Codex CLI's post-run "thread not found" rollout
  persistence warning. The Codex log viewer also suppresses that benign
  warning, stdin notices, and startup plugin-manifest warnings when
  rendering existing spawn logs.
- Prioritized transcript rendering when selecting a conversation so background metadata and archive refreshes no longer keep the pane stuck on Loading.
- Reverted the conversation list's fixed scan columns back to a compact single-row layout while preserving a tiny live-dot gutter, clipping long branch names, keeping repo group chips left-aligned, showing right-aligned icon actions only on hover/selection, hiding noisy backlog sizes, and hiding redundant `[... Problem]` / `[... Feature announcement]` project tags on GitHub issue titles.
- Restore the close/archive action on cross-repo GitHub issue rows by sending each row's concrete repo context.
- Use Claude diagnostic context samples as a fallback for the conversation footer when transcripts omit normal token-usage records.
- Drag-to-open-another-conv-pane now actually opens the pane. The overlay's `dropEffect = 'copy'` did not match the drag source's `effectAllowed = 'move'`, so the browser cancelled every drop silently. Aligned the drop overlay to `'move'`.
- "Earlier ask" body in the sticky header now renders in the user-message accent blue, matching "Original ask" and the in-conversation user bubbles instead of the default sticky-header text color.
- All repos mode now hides CCC-generated helper sessions such as title summarizer prompts and one-off image-read JSON extractors. The active repo list and cross-repo archive now share the same generated-helper filter, so these utility JSONLs no longer appear as normal work rows.
- GH issue titles with quotes now keep their full text when starting a session from the sidebar or board.
- Keep inline session-title edits alive when the conversation list auto-refreshes.
- Add an "All" In Progress window and group every session in the selected window by project when using the by-project view.
- Show the project chip before the session title when In Progress rows are shown by time.
- Fix the conversation footer when a transcript contains CCC's own input-context HTML snippet.
- Sidebar Merge button now auto-archives the row after a successful direct `gh pr merge`. Previously the row stayed in "Ready to merge" with the same `PR #N` chip and merge button, so a confused user could re-click and get a second misleading "Merged" toast (gh is idempotent on already-merged PRs). The row now collapses into the archive section, the merge button disappears, and the toast reads `Merged PR #N → archived`.
- Also auto-archives on the via-session path when the PR is already MERGED on GitHub. Previously, clicking Merge for a session whose PR had already been merged + cleaned up (worktree removed, branch deleted) would inject a useless "please merge this" prompt into the live session — the agent correctly reported "already done" but the conversation never archived, so the row stayed in the sidebar forever. The endpoint now runs `gh pr view --json state` first; if state is MERGED, it archives the conversation and returns `via: "already-merged"` without injecting anything. Idempotent — re-clicking on an already-archived merged session is a no-op.
- Sidebar Merge button no longer fails with `GraphQL: Could not resolve to a PullRequest` when the session opened the PR in a different GitHub repo than the one its working directory now points at. The full PR URL captured from `gh pr create` is now stored alongside the bare number and passed to `gh pr merge`, which lets `gh` resolve the repo from the URL itself instead of guessing from the cwd's git remote.
- Keep the conversation footer's model visible when an older session has no token usage samples, showing context as unavailable instead of hiding the usage area.
- Hid low-priority archive row metadata, starting with file size, when the sidebar is too narrow for readable session titles.
- Fixed: spawning a new session no longer makes its row in the sidebar disappear for ~2 seconds before reappearing, and the right pane now follows the spawn end-to-end — the new card is auto-selected on click and the selection carries through the placeholder→real swap with no "Loading…" flash.
- Fixed the input-context strip (worktree/branch/ctx-token pills) lingering with stale data from the previously selected session when entering "Start a new session" mode.
- Fixed conversation rendering when tool results contain image/block payloads instead of plain text.
- GH issue Start buttons now immediately move the issue into In progress while the session spawn finishes.
- Archive button on pkood agent cards now actually hides the card. The toggle was already persisting the ID to disk, but `find_pkood_agents()` returned `archived: False` regardless, so the card stayed in the active list. Pkood cards now consult the same archived set as claude sessions.
- Sidebar rows now recognize Claude Code `pr-link` transcript events, not just `gh pr create` tool output, so sessions like `afcc907b-3ab5-44ac-9222-b42c1f1fe60e` surface `PR #242` in the row list and Ready to merge section. Bumped the conversation metadata cache schema so already-scanned sessions are re-parsed with the new PR-link extractor.
- Sidebar's "Ready to merge" section now hides sessions whose PR has already been merged or closed. Previously any session that ever ran `gh pr create` stayed in the bucket forever, turning it into a graveyard of completed work. The server resolves PR state via `gh pr view` with a 5-minute in-process cache and a small thread pool so the dashboard's refresh cadence doesn't fan out to gh; failures keep the row visible to be safe.
- Repo chips in the conversation list now align with the timestamp lane instead of the task title.
- Repo-switch POST (`/api/repo/switch`) now aborts after 10 s instead of hanging the loading overlay forever when the server is unresponsive. On timeout you get a toast ("Switch timed out after 10 s — server unresponsive") and the picker reverts.
- Fixed: the optimistic "Sending…" pill now re-anchors to the bottom after the real user message lands, so the order reads "your message → Sending…" instead of the pill floating above what you just sent.
- Session ID chips now copy reliably from both the main conversation header and split conversation toolbar.
- Cleaned up the sidebar issue list: GH issues now group by collapsible project buckets by default, stay one row tall, keep deploy/history controls out of the wrong header row, move terminal-sent sessions up optimistically, surface active Codex turns as WIP in the conversation list, open relative transcript file links from the session cwd, and avoid transcript scroll traps on long code blocks.
- Kept live sidebar status chips visible on narrow conversation lists by hiding lower-priority metadata before WIP/tool/state indicators.
- **Sidebar "+ New session" button now honors the engine dropdown.**
The prominent sidebar CTA was hardcoded to `/api/sessions/spawn`
(Claude) regardless of whether the toolbar **Engine** selector was
set to `codex`, so picking Codex and then clicking + New session
silently produced a Claude run. `spawnFromInlineInput` now reads
`$kptEngineSelect.value` and routes to `/api/sessions/spawn-codex`
when codex is selected, the optimistic placeholder card gets the
right `codex` chip, and the empty-state copy ("…spawn a fresh
Claude agent") swaps to "Codex" or "Claude" based on the current
selection. Toolbar **Run** and the New Session modal already did
this — only the sidebar CTA path was broken.
- Fixed archive sidebar rows so timestamps stay in the left scan column before cross-repo project chips.
- Hide redundant repo chips in conversation lists filtered to one project.
- Fixed a transcript scroll jump when later user messages move into the sticky "Earlier ask" panel.
- Keep the sticky header's Files pill inside the fixed-height panel so it no longer shifts transcript scroll position when it appears.
- Let long sticky Original ask and Earlier ask content scroll inside the fixed ask panel instead of clipping.
- **Streaming bubble now hands off cleanly to the JSONL renderer.** Each assistant message is keyed by `message_id` end-to-end (server payload → bubble `data-msg-id` → JSONL row `data-msg-id`); the moment the JSONL row paints, the matching bubble is removed in place. Eliminates the brief duplicate render and the temporary 3-second linger workaround. The live `(thinking…)` cue is preserved during the streaming phase and becomes the collapsed "Thinking" toggle once the message finalizes. Includes diagnostic `[S HH:MM:SS.mmm]` / `[J HH:MM:SS.mmm]` render-time stamps on streamed blocks and JSONL events, useful for verifying hand-off timing on screen.
- Tail-meta `has_commit` / `has_push` now detect the `git -C <path> commit/push` form (and other flag-prefixed `git` invocations like `git --no-pager commit` or `git -c key=val push`). Multi-worktree sessions no longer render as "uncommitted" after a real commit just because the command used the form CLAUDE.md mandates for shared-clone safety.
- **Terminal panel: input row no longer clipped.** The placeholder text
on the input row was rendering with its top half cut off in some
layouts. Two fixes: (a) the row now has `flex-shrink: 0` and a
`min-height: 32px` so it can't be squished by the flex container; (b)
when the multi-repo left rail is visible, the panel slides right by
48px so the rail's repo dots stay above (and clickable) instead of
being painted over.
- Fixed live terminal sends to avoid macOS System Events keystroke failures and show clearer permission guidance when terminal automation is blocked.
- **Terminal panel: input no longer stuck after one command.** The
`/api/term/run` SSE response was sent with `Connection: keep-alive`
but no Content-Length, so the browser's reader never saw end-of-stream
and the input stayed disabled after the first command finished. Now the
endpoint sends `Connection: close` and the client also breaks the read
loop on the `exit` event, so back-to-back commands work as expected.
- Fixed user-message bubble vanishing when sticky header expanded: the dynamic-ask tracker now measures against the stable original-ask block (not the growing full sticky) and briefly un-pins the active bubble before re-measuring, so a just-scrolled-past question can be un-pinned again on scroll-back.
- Sessions launched in the shared clone but editing a sibling worktree (via `cd ../<repo>-wt-*`) now show the correct dirty/clean state on the sidebar row. The `worktree_dirty` probe now runs `git status --porcelain` against the *effective* worktree (inferred from tool-call paths), not the literal session cwd.
- Detect when a session that launched in the shared clone has `cd`'d into a sibling worktree. The conv pane now surfaces the worktree's branch and ahead/behind counts via a deterministic `git worktree list` match against the session's `cd` / `git -C` targets, instead of being filtered out by the count heuristic.

## [0.1.4] - 2026-04-25

### Fixed
- Sessions spawned in repos whose path contains non-alphanumeric
  characters (most commonly `+`, but also `.`, `_`, spaces) are now
  visible on the kanban. Claude Code 2.x sanitises every non-alnum
  character to `-` when naming its `~/.claude/projects/<slug>/`
  subdir; CCC's encoder previously only replaced `/`, so a repo at
  e.g. `~/Apps/BYM+Finie` had its sessions written under
  `-Users-amirfish-Apps-BYM-Finie` while CCC scanned
  `-Users-amirfish-Apps-BYM+Finie`. Symptom: clicking "Start session"
  on a backlog card briefly showed a placeholder in Working, then
  the placeholder vanished and the backlog card never cleared,
  while the spawned `claude -p` kept running invisibly.

## [0.1.3] - 2026-04-24

### Added
- Claude-Desktop-style UI chrome: prominent "+ New session" button at the
  top of the sidebar, a unified panel-toggle icon (replaces the legacy
  `×` / `◀` glyphs in the conv-panel and kanban-panel toolbars) with a
  `Cmd+\` keyboard shortcut, a `Cmd+K` / `Cmd+P` "Search chats and
  projects" command palette over the existing in-memory session list,
  a sun/moon appearance picker (Theme: Light / Dark / Match system,
  Font: System / Mono — persisted to localStorage), and a sidebar gear
  popover with View on GitHub / Get help / Search sessions entries.
  Light theme is now a first-class option; the existing dark palette is
  unchanged.
- In-app bug reporting — a "Report a bug" link in the topbar opens a modal
  that auto-attaches CCC version, browser user-agent, and the currently
  selected session id, then files a GitHub issue (label `bug`) against
  `amirfish1/claude-command-center` via `gh issue create`. If `gh` is
  missing or fails, the modal renders the issue markdown so the user can
  copy it to the clipboard and file the report manually. New endpoint:
  `POST /api/bug-report`. Pattern adapted from BookYourMat. (#5)

### Fixed
- Spawn experience feels snappy: the kanban toolbar `Run` button now inserts an
  optimistic placeholder immediately (it was previously waiting for the spawn
  POST to return), the placeholder→real-card swap inherits the column via a
  60 s sticky pin so fresh sessions don't bounce Planning↔Working↔Review while
  the server settles on sidecar/live/stage, and cards fade in + animate on
  legitimate column changes instead of snap-jumping. Closes the "card appears
  late, glows, jumps around" gripe.

## [0.1.2] - 2026-04-24

### Added
- In-app update: a subtle 'Update available' pill in the topbar when a newer
  release tag is published on GitHub. Clicking opens a modal with the
  changelog link and an 'Update now' button that runs `git fetch + reset
  --hard origin/main` in the install dir (pre-flight checked for local
  modifications and branch=main) and restarts the server in-place via
  `os.execvp`. Browser auto-reconnects when the new process binds the port.
  Closes #3.
- Browser tab favicon — inline SVG data URL showing the ⌘ glyph in Claude
  orange on the app's dark surface. No new file, no server route.
- Orchestration skill `ccc-orchestration` and `POST /api/ask` endpoint —
  any Claude Code session on the machine can now spawn, inject into, and
  synchronously ask sibling sessions through CCC over plain HTTP. The
  skill is auto-installed to `~/.claude/skills/ccc-orchestration/SKILL.md`
  on server startup (skip with `CCC_SKIP_SKILL_INSTALL=1`). CCC also
  writes its base URL to `~/.claude/command-center/port.txt` on startup
  so the skill (and any other scripted caller) can discover the running
  instance without hardcoding the port. `/api/ask` reuses the existing
  `resume_session_headless` infrastructure: it tails the spawned
  subprocess's stream-json log, resolves on the next `result` event, and
  returns `{ok, text, cost_usd, duration_ms, num_turns}`. Timeouts return
  any partial assistant text seen so far and leave the underlying session
  running.
- Fenced code blocks in assistant messages now render as proper syntax-
  highlighted blocks instead of plain text with literal backticks. Supported
  langs: ts/tsx/js/jsx, py, bash/sh/zsh, json. Includes language label, a
  copy-to-clipboard button (hover state for `Copied` feedback), horizontal
  scroll for long lines, and token colors adapted from the GitHub dark
  palette. Hand-rolled regex tokenizer — no library dependency.
- Newly-appeared session cards get a transient shimmer glow on the kanban
  for ~30 seconds after first detection. Signals "this card is still
  settling — it may jump to a different column shortly." Only triggered
  for sessions that show up during a live poll; initial page load doesn't
  glow everything. CSS-only (bounded iteration count) + one scheduled
  re-render to clean up the class so the gradient doesn't linger static.
- Conversation-pane input redesigned Claude-Desktop-style: pill-framed
  container with focus ring, multi-line auto-resizing textarea (caps at
  ~160px then scrolls), inline arrow send button, and a keyboard-hint
  footer showing `⏎ send · ⇧⏎ newline`. Enter submits (Shift+Enter adds
  a newline). Send button disables when the input is empty or no session
  is open. IME composition guarded so Chinese/Japanese candidate commits
  don't accidentally fire a send.
- Each message card in the conversation view now shows a relative timestamp
  next to its line number. Tiers: `just now` (<1 min) → `N minutes ago` (<1 h)
  → `N hours ago` (<5 h) → `HH:MM` (same day, older) → `Yesterday · HH:MM`
  → `MMM D · HH:MM`. Hover reveals the full localized date-time.

### Fixed
- Pkood-spawned agents no longer produce two kanban cards (a `pkood-*` one
  with working input plus a broken "Send to terminal…" claude-session one
  that can't reach the pty). Each pkood agent is now linked to its
  underlying `~/.claude/projects/*/<uuid>.jsonl` and the duplicate card is
  absorbed into the pkood card. Linking is primarily by the
  `claude.ai/code/session_*` bridge token printed in claude's banner and
  also recorded as a `bridge_status` event in its jsonl — the shared
  token is per-process and uniquely identifies each claude instance. When
  the bridge token isn't available we fall back to a cwd + spawn-time
  window heuristic. Dead pkood agents are left un-merged so their
  underlying jsonl stays resumable via the CLI. The merged card pulls in
  the jsonl's display name and tool-use signals so the user sees one
  richer card per running agent.
- "Launch in terminal" no longer builds a broken `cd` for repos whose name
  contains hyphens. `find_session_cwd` used to fall back to decoding the
  `~/.claude/projects/` directory name by replacing every `-` with `/`,
  which silently turns `claude-command-center` into `claude/command/center`.
  The fallback also triggered for very young sessions whose `.jsonl` hadn't
  logged a `cwd`-bearing event in its first 40 lines, and the wrong path
  was cached in-process for the lifetime of the server. The fallback now
  scans sibling `.jsonl` files in the same project dir (which share a cwd)
  instead of decoding the dir name, and a miss is no longer cached.
- Sending to a Terminal.app / iTerm2 session from the split-panel input no
  longer leaves the terminal stuck on top. The osascript inject now
  captures the previously-frontmost app before activating the terminal
  and restores it after the keystroke lands, so CCC (in the browser)
  regains focus automatically. Still briefly flickers — macOS's keystroke
  API fundamentally requires the target app to be frontmost — but the
  user ends up back where they were.
- Per-card ✨ "regenerate title" button now shows on every session card that
  has a first user message, not only un-summarized ones. Previously, once a
  card was user-renamed (`name_overridden`), the button was hidden and there
  was no in-UI way back to an AI-generated title. On renamed cards the
  button is dimmed and its tooltip flags the destructive intent
  ("Regenerate title — replaces your manual rename").
- Session → GitHub-issue auto-link no longer uses the jsonl tail
  (`tail_issue_number`) as a last-resort signal. The tail scan matches any
  `gh issue …` command, `Closes #N` commit, or `github.com/.../issues/N`
  URL Claude happens to run mid-conversation, which produced false links
  when an assistant turn merely *discussed* an unrelated issue. Auto-link
  now relies solely on spawn-time identity — `display_name`, the first
  user message, and the branch — where genuine "I'm working on #NNN"
  intent lives. Explicit side-car mappings remain authoritative.
- Haiku title-summarizer subsessions no longer leak into the kanban. The
  `/api/sessions` scan now skips conversations whose first user message
  starts with our internal `Produce a concise 4-8 word title…` prompt,
  so clicking the ✨ Titles button on the CCC repo (or any repo watched
  from the CCC working directory) stops filling the board with identical
  throwaway cards.
- Archived/verified cards no longer flash back into their old column
  briefly after the click. Previously the 10s `/api/sessions` poller
  could overwrite the optimistic `c.archived = true` mutation if a
  request was already in flight when the user clicked. A short-lived
  client-side override map (30s TTL, auto-cleared once the server
  agrees) shields the optimistic value across stale poll responses.
  Fixes both the explicit Archive/Verify buttons and the drag-drop paths.
- `run.sh` no longer clobbers the persisted watched repo when launched
  from the CCC source tree. It used to force `CCC_WATCH_REPO=$PWD`
  unconditionally, which overrode `~/.claude/command-center/last-repo.txt`
  whenever the script ran from its own install dir. Now: explicit env
  var still wins, otherwise `$PWD` wins unless `$PWD` is the install
  dir AND a persisted selection exists — in which case we defer to it.

## [0.1.1] - 2026-04-23

### Fixed
- Chat input at the bottom of the conversation pane was clipped by the fixed
  topbar's 33px body padding — only a 1px border-top sliver showed. The split
  kanban view now sizes to `calc(100vh - 33px)` so the input row is visible.

### Added
- Repo picker now has a "…" button for picking folders the `$HOME` scan
  can't reach (paths outside `~/`, or nested below a top-level dir).
  The picked path is persisted to `~/.claude/command-center/custom-repos.txt`
  via a new `POST /api/repo/add` endpoint and auto-switches on success.

## [0.1.0] - 2026-04-22

Initial public release.

### Added
- Kanban board over all live + dormant Claude Code sessions, classified by
  signals (commit / push / sidecar status / GitHub label).
- GitHub issue → session → verify → close pipeline with attention queue.
- Headless `claude -p` spawn with stdin-pipe follow-up, plus resume-on-demand.
- Optional Vercel deploy polling and auto-fix-deploy.
- Optional [`pkood`](https://github.com/anthropics/pkood) integration for
  background agent runners.
- Repo picker — live-switch the watched repo from the toolbar without restarting.
- AI title regeneration via `claude -p --model haiku`.
### Security
- `127.0.0.1` bind by default. `CCC_BIND_HOST=0.0.0.0` requires opt-in and
  prints a startup warning.
- Same-origin POST check (Origin header) on every state-changing request.
- `/api/open` clamped to paths under repo/log roots. Default action
  is `open -R` (Reveal in Finder), not launch.
- `/api/repo/switch` validates targets against the picker allow-list.
- See [`SECURITY.md`](SECURITY.md) for the full threat model.

[Unreleased]: https://github.com/amirfish1/claude-command-center/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.3
[0.1.2]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.2
[0.1.1]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.1
[0.1.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.0
