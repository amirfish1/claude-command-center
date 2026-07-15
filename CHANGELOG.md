# Changelog

All notable changes to this project will be documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [5.8.0] - 2026-07-15

### Added
- All-tab lane headers now accept dragged sessions to pin them under Coding, Workers, or Messages.
- Car Mode: a first-class hands-free voice operator. Launch it from the sidebar (🚗) or Settings → Advanced. The modal explains the feature and its cost (~$0.35/hr), lets you store optional Anthropic/Deepgram API keys locally, and starts/stops the voice agent. Degrades gracefully by which keys are set — with both you get hands-free voice; without a Deepgram key it points you to CCC's free built-in browser mic and read-aloud instead of erroring.
- Add confirmed bulk archive controls for repeated session clusters.
- Codex desktop ↔ CCC single-writer sync: CCC now detects when the Codex desktop app is attached to (or actively writing) a thread, shows "Desktop writing" / "⧉ desktop" on the conversation badge, blocks overlapping turns with a write-gate that queues CCC messages durably until the desktop turn finishes, and records durable coordination events (desktop turn detected, message queued, CCC turn boundaries) in the conversation.
- Added zero-token-cost presentation modes with semantic list pagination, live activity reassurance, and per-pane answer decks.
- Add drag-and-drop file attachments to session composers, with managed 100 MiB uploads.
- Added a server-backed model catalog that merges local harness model data and lets new custom model IDs be selected without a CCC release.
- **Federated CCC fleet**: pair CCC instances across machines (Settings → Nodes & peers…) and get one control plane — a Fleet view showing every repo's worktree/dirty/unpublished/PR/deployment state per node with honest staleness, "Continue on…" session handoff that pushes-then-fetches via git (never copies files), rewrites transcript paths, and lease-guards ownership on both sides, location-transparent spawn/inject/ask/group-chats through global `node:session` references, and a reviewed Resolve-all plan that pushes, pulls, marks-ready, merges, pings owning sessions, and removes only provably merged worktrees — as a persisted, restart-resumable job. Loopback bind and pairing-secret auth throughout; unpaired peers are rejected.
- Group-chat replay now presents each participant message at 2× scale near the
lower third of the screen, grows text downward from a fixed top edge, pauses
for reading, then animates it into its transcript position.
- Split the All sidebar tab into Coding, Workers, and Messages lanes, including Hermes and WatchTower workers.
- Replaced the main Merge tab with a Queues tab that reuses the full WatchTower queue view while keeping the desktop right-rail Queue tab available.
- Added conversation-scoped Mode 3: working agents can return safe, designed slide artifacts alongside normal answers, with Present fallback, toolbar progress, complete live updates, keyboard navigation, and answer-aware auto-advance.
- Added a separate Productivity dashboard with project and daily delivery evidence, 6–16 week trends, Git/agent/token/time metrics, computer-presence coverage, and WatchTower activity.
- Added a Queue manager for creating and editing WatchTower queue configurations from the dashboard.
- Add a WatchTower activity-log shortcut between the Queue list and its tickets.
- Add All, Bugs, and Features filters to the Queue panel.
- Show live WatchTower worker sessions and Past 24h worker history inline beneath each Queue health row.
- Add a durable Cancel action to queued composer messages so they can be withdrawn without disturbing later FIFO entries.
- Session icons now retain distinct engine colors while brightness and readable $, $$, and $$$ markers show unified cost tiers, with a separate dot for active work.
- Added distinct Active, Archived, and Trashed conversation states: Active and Archived remain visible in All, Trash is a separate bottom bucket, and every tab now exposes the correct Archive, Move to Active, Trash, or Untrash action.
- Add reasoning-effort defaults to global Codex spawn settings and per-queue WatchTower configuration.
- Added a lazy expandable Input disclosure that shows complete redacted payloads for Claude transcript tool calls.
- **WatchTower sentinel badge** in the sidebar header, next to the version label. A small beacon shows live queue health (open count, queue count, stuck count) sourced from the existing `/api/ux-fixes/health` payload; the count pill and beacon color switch to amber when any queue is stuck. Click it for a popover breakdown of the top queues, with a CTA that scrolls to and highlights the queue panel. The beacon only animates a brief tick once per poll (~20s) instead of looping continuously, so it costs nothing while idle or backgrounded.

### Changed
- Codex status and wake indicators now show when CCC is using the managed app-server path.
- Changed Codex model options to the current 5.5, 5.6 Sol/Terra/Luna, 5.4, 5.4 Mini, and 5.3 Codex Spark set.
- Conversation panes now reveal newly arriving assistant prose word by word, matching replay mode's type-in feel without replaying old transcript loads.
- Replace the landing-page hero mockup with a current three-pane CCC workspace showing sessions, conversation context, and its linked issue queue.
- Model Advisor now refreshes after meaningful session changes, coalesces scans, and avoids continuous footer polling.
- Rebuilt the ccc.amirfish.ai landing page around the product story: pain-first hero, six problem-family chapters with visual proof slots and expandable capability depth, corrected version pill and Windows support cells, honest architecture claims, and AA contrast on muted text. Backed by the new canonical docs under docs/product-story/.
- Changed Mode 2 to open at the newest answer's first slide and moved navigation to large, centered side controls.
- Moved the Queue panel’s Add control into a trailing queue row.

### Removed
- Presentation controls now use a simpler `Off | Present` choice; saved Mode 1 preferences automatically migrate to Present.

### Fixed
- All-tab lane drops now repaint the session into the selected Coding, Workers, or Messages lane immediately, without requiring a browser refresh.
- All-tab lane overrides now persist across row id aliases and keep overridden terminal rows visible in their selected lane instead of leaving them hidden in Trash.
- Preserve the last-opened conversation and the selected All subtab across hard refreshes and periodic sidebar updates.
- Annotate stays visible on non-macOS installs; only the native Screen capture button is hidden when desktop screenshot support is unavailable.
- Fixed the conversation archive getting stuck on its startup loading screen.
- Fixed archived session rows so they show an explicit Restore action that moves the session back to Active.
- Show Codex Desktop's injected in-app browser state as a compact, expandable context card instead of user-written prompt text.
- Detect and automatically resume Codex work stranded after context compaction or a silent active goal turn, including across CCC restarts, with durable per-conversation state, bounded retries, visible recovery status, FIFO handoff for queued messages, and safeguards for legitimate blocking states.
- Queued Codex messages now advance immediately on new input and turn completion while preserving per-conversation FIFO order; the periodic watcher is retained only for recovery.
- Codex wakes now prefer the managed app-server, track turn notifications, warn when accepted turns show no activity, and can interrupt active app-server turns.
- Codex sessions spawned by CCC now appear in Codex mobile, and CCC user-renamed Codex sessions sync their title into Codex's local session metadata.
- Reject Codex models missing from the installed `codex debug models` catalog instead of starting silent sessions.
- Codex model picker effort choices now mark the currently active reasoning effort, even when it comes from the latest session usage rather than a queued override.
- Fixed slow opening of Codex conversations that end without visible output.
- Codex Steer controls now hide unless the current turn can actually be steered from CCC, and stale Steer attempts fall back to a normal follow-up send.
- Codex messages now fall back from a stale or unavailable Steer action to the normal ordered Send queue instead of failing with a misleading external-process error.
- Show Codex-spawned agents beneath their parent session in the All view and use task-oriented labels when Codex exposes reliable task metadata.
- Read current Codex multi-bucket quota data correctly and show authoritative account-wide daily totals alongside locally attributed graph detail.
- Fixed dormant Codex sends so the conversation pane shows wake progress immediately instead of staying opaque while input is still being accepted.
- Queued Codex follow-up messages now use one durable queue watcher across local CCC server instances, preventing stale queue snapshots from blocking wake delivery.
- Stop labeling an active Codex turn as “another writer” when CCC reconnects after a restart; messages still queue safely behind the turn in FIFO order.
- Stop a stalled conversation compaction from leaving its progress banner and controls stuck indefinitely.
- Fixed End and composer clicks resetting long conversations to the top instead of preserving the requested reading position.
- Make in-conversation find search the full transcript after a short typing pause and skip matches hidden inside collapsed content.
- Fixed presentation arrows and End navigation, and kept bottom-pinned transcripts at the tail when focusing the composer.
- Fixed Current Sessions flooding with ended spawned child-worker sessions.
- Prevent dormant Codex `/goal` updates from leaving a permanent “Delivered — waiting” message bubble.
- Fixed new-session model pickers to keep cached models scoped to their engine.
- Show Codex reasoning effort next to the model in the conversation footer.
- Conversation word-by-word reveal now stops auto-following as soon as you scroll away from the exact transcript tail.
- Keep the mobile conversation Back button available when switching sessions after viewing subagent task tabs.
- Fixed Mode 2 presentation pagination to fill slides by rendered content height instead of splitting compact numbered and bulleted content into sparse pages.
- Fixed the new-session model picker showing models from a different engine.
- Fixed first-launch installation from the macOS DMG by replacing fragile Terminal automation with an observable native installer, actionable recovery, and a built-DMG integration test. Update checks now wait until the dashboard loads so Sparkle's first-run prompt cannot dismiss an installation error or accidentally select Retry or Quit.
- Fixed New session composers showing queued or steered messages from the previously viewed session.
- Keep queued Codex prompts draining when another CCC server's watcher exits.
- Pressing Escape now exits conversation presentation Mode 2, including while the composer is focused.
- Presentation mode now mirrors every live transcript update and interactive control, keeps newly completed answers visible, and restores the regular view's exact scroll/pin state on exit.
- Fixed presentation-mode pane state from intercepting End, composer, and transcript clicks and resetting the conversation position.
- Prevented background presentation refreshes from replaying the slide entrance animation and making the view jitter.
- Fixed Present mode so readers already following the final slide advance when a new answer adds slides, while readers browsing earlier slides stay in place.
- Sort the Queue panel's All history view by ticket creation time, newest first, while preserving work-priority ordering in Open.
- Explain when auto-drain is enabled but no queue tickets are runnable, and surface drain-toggle failures instead of silently ignoring them.
- Paginated the Queue All-history view so large ticket corpora no longer freeze the browser main thread.
- Fixed Queue panel overflow in the status rail when queue details are long.
- Fixed the Queue scope picker so manually selected queues stay selected while the Queue tab rerenders.
- Make Queue scope changes repaint immediately from the loaded ticket data instead of waiting for a full queue refetch.
- Keep queue tickets constrained to the resized sidebar width instead of letting long titles expand the Queue tab.
- Queue status dots now distinguish inert open work (gray) from active WIP (blue) and successfully closed work (green).
- Queue health badges now explain what STUCK, WAITING, PARKED, CLEAR, and LIVE mean in their tooltip/ARIA labels.
- Make in-progress Queue status dots use a dark blue core with a stronger electric-blue glow.
- Keep queued-message trays scoped and ordered, prevent late tool output from resurrecting completed Codex turns as “Thinking,” and place CCC coordination/tool rows at their actual chronological positions.
- Show Steer immediately beside Cancel when a Codex composer message enters the durable queue.
- Keep queued composer messages visible and in FIFO order when a Steer attempt cannot reach the active Codex turn.
- Restored conversations now prefetch their bounded transcript tail and paint before the full archive sidebar render, substantially reducing time spent on the conversation loading state.
- Session-name matches now remain at the top of visible search results.
- Keep Claude Code hooks on macOS from breaking after Homebrew Python upgrades.
- The throughput dashboard now restores its complete billing-period graph instantly from stale cache, refreshes atomically in the background with live timing and session progress, limits discovery to the latest 14 days, and shows counted discovery/read/cache/parse phases instead of an opaque first-run wait. Claude and Codex now share the same billing-period chart experience, metric cards, weekly scale, and reset boundary; redundant quota/cost cards and the legacy aggregate fallback have been removed. Usage rate, cache totals, model quota contributions, and next-reset times now use explicit authoritative values, while empty auxiliary sections stay hidden. A Combined mode composes the existing engine snapshots without another scan and shows normalized Claude/Codex quota lines on a compact timeline with two prior-cycle days and a forecast ending at 100%. The chart keeps those real prior-cycle bars but no longer draws a misleading position-aligned “Last week” line across the compressed timeline.
- Show a WatchTower ticket's configured repository in its detail view.
- Show cached input counts in per-turn transcript token chips.
- Show recoverable tool-call failures as amber warnings instead of alarming red errors.
- Fixed Trash persistence across refresh, including Workers in All, and advance to the next visible conversation when trashing the open row.
- Conversation Speak now skips queued or otherwise pending user echoes, so it reads the latest agent reply instead of a parked Codex send.
- Avoid a broken image icon when a transcript refers to an image that was not saved.
- Fixed word-by-word playback so list bullets and other markdown structure do not appear before their words are revealed.

## [5.6.0] - 2026-07-08

### Added
- The `/v1/open` beacon now accepts an optional `dev: true` marker that the worker stores as `is_dev=1` on the row. The public stats page filters those rows out so the maintainer's frequent restarts do not inflate the daily boot / distinct-IP counts. Enable it by exporting `CCC_TELEMETRY_DEV_MODE=1` before launching the server; the beacon otherwise behaves exactly the same (no identity, IP dropped, daily-rotated ip_hash). Documented in `docs/telemetry.md`.
- Codex's model switcher now has a Reasoning effort section (Light/Medium/High/Extra High), matching the Codex app's split Model + Reasoning menu.
- `run.sh` now sources `~/.claude/command-center/config.local.env` if present, so machine-local env vars (e.g. soak flags) survive a reboot instead of relying on `launchctl setenv`.
- Added Hermes WhatsApp bridge-log and pending-ask files as conversation sources.
- Added native Windows PowerShell install and foreground launch paths.
- Added collapsible sidebar groups for adjacent repeated session rows.
- Integrated Gemini and Hermes as first-class engines in the new session UI (composer engine selector).
- Added backend support for spawning new Hermes sessions headlessly (`spawn_session_hermes`) via the `/api/sessions/spawn-hermes` and `/api/sessions/spawn-hermes/availability` endpoints.
- Added OpenSSH ControlMaster multiplexing bridge (`CCC_SSH_HOST`) for remote session discovery, process spawning, and log streaming over SSH.
- Added Stitch to the conversation background color palette with custom design variables matching the Cyan Mono Terminal specification.
- Added a hover zoom control on the throughput chart for focusing the last 48 hours.
- Added a Codex tab to the throughput analyzer with engine-specific aggregate loading and session filtering.
- Added a "Fable % of weekly" dashed line to the main throughput chart's weekly overlay, showing Fable's actual cumulative contribution to the weekly quota (token share × cumulative actual %) alongside the existing "if opus only" hypothetical.
- Throughput dashboard now renders a cached initial graph immediately and refreshes the expensive aggregate data in the background.
- Added clickable throughput reset markers with reset details and manual-marker edit/delete actions.
- The ticket detail modal now has an "Add comment" section for logging a status update (timestamped, visible via `wt find`) without closing the ticket. Also removed the redundant footer "Close" button — the × already closes the modal.
- Verbose toggle in the status rail (left of Annotate) keeps tool calls, command details, and thinking expanded in conversation views; silent thought markers now explain that the reasoning text wasn't saved to the transcript.
- Sends routed through WatchTower now show the exact delivery stage on the pending message: transport-specific "Accepted" copy (live terminal / worker stdin / headless resume), receipt-backed verification polling, "Landed in transcript" confirmation, and an automatic native-resume fallback when a delivery is verified lost.

### Changed
- Conversation pane: the floating "↑ Last" button now relabels to "↑ Previous" once your last message is reached, and each click steps back to the next-earlier user message.

### Removed
- Removed the vestigial repo-picker machinery (sidebar dropdown, repo-picker modal, folder filter): the conversation list has exactly one mode — all repos. Surfaces that need a concrete repo now derive it from the open conversation, which un-breaks the 🌿 Worktrees button, the kanban-toolbar Run button, the Terminal panel (its cwd now follows the open conversation), and the history-search "this repo only" filter. Conversation popouts stay pinned to their repo.

### Fixed
- Fixed the All tab's 1d/7d/All filter so older Ready-to-merge and archived group-chat rows cannot bypass the selected window.
- Fixed duplicate session rows appearing in the conversation list when spawning new Antigravity sessions. The actual session ID generated by the Antigravity CLI is now extracted from logs to dynamically backfill and overwrite the temporary placeholder session ID in the spawn registry.
- Fixed Antigravity spawns dying after 5 minutes with "Error: timeout waiting for response" — CCC now passes `--print-timeout` (default 2h, override with `CCC_ANTIGRAVITY_PRINT_TIMEOUT`) to every headless AGY run, and the resume-staleness window scales with it.
- Fixed archive refresh performance by keeping auto-unarchive liveness sweeps out of read-only archive scanners.
- Queue tickets with claim metadata no longer render with the open/green status dot.
- Fixed a pending-input queue deadlock that could leave conversation detail panes stuck on Loading.
- Route synchronous `/api/ask` requests for Hermes sessions through the Hermes resume harness instead of Claude JSONL resume.
- Fixed Hermes split-pane composer sends so resumed Hermes sessions use the Hermes resume route instead of the spawned-session stdin route.
- Fixed layout cutoff of the model name in the conversation footer by making the workspace container a flex-shrinkable element, capping long branch names, and hiding extra usage details at small viewport widths.
- Restore visible repo suggestion chips in New session and keep the mobile Back button reachable in the session header.
- Clarified the stale "No active agent process" hint so it explains that the last send did not find a running agent and points users to resume headlessly or use Launch if typing is unavailable.
- Fixed split-pane composer routing for one-shot engines so Kilo, like Hermes, bypasses the spawned-session stdin route.
- Fixed headless wake/resume for Claude sessions that were accidentally launched from `/` but belong to an inferred repo.
- Spawn defaults (Settings) are no longer silently overwritten by the new-session or Kanban-toolbar engine/model dropdowns — only Settings -> Spawn defaults' Save button persists the default now.
- Fixed split conversation panes so the bottom composer controls and status rail follow the active pane instead of leaking updates into the first pane.
- Fixed a bug where a mid-session message loaded as the first message of a truncated transcript tail was incorrectly treated as the original ask (pinned/prompts added), and loaded Google Fonts (Space Grotesk & JetBrains Mono) to fix Stitch typography.
- Terminal-control inject now submits with a native tty newline write instead of a System Events Return keystroke — injected text no longer sits unsent in the Codex/Claude composer when window activation silently fails, and injecting no longer steals focus (Tab-queue submits still use the keystroke path).
- Fixed the throughput quota overlay axis so low-percentage weeks scale to their visible data instead of always using a 100% floor.
- Fixed throughput reset markers so weekly quota charts do not open Codex reset details for unrelated five-hour markers.
- Fixed the throughput aggregate chart so it waits for weekly context before rendering and labels the previous-week overlay.
- Injecting into a dormant session no longer silently drops the message when the WatchTower messaging backend (`CCC_MESSAGING_BACKEND=wt`) is enabled: `wt send` now runs with `--no-queue` so any delivery it cannot complete immediately falls through to CCC's native headless resume, and the child wt runs with its CCC delegate disabled so a failed delivery cannot recurse back into `/api/inject-input`. (Pairs with the WatchTower-side fix that spawns `claude --resume` in the session's own project directory and verifies the child boots.)
- Refresh WatchTower ticket badges on every session-list serve, not just at cold rebuild, so a Codex worker's title stops freezing on the first ticket it ever closed (or the raw spawn prompt) while the cross-repo `?all=1` cache stays warm

## [5.5.0] - 2026-06-29

### Added
- Telemetry schema v3: opt-in daily ping now includes two more aggregate fields — `active_seconds_today` (sum of dashboard-tab-visible time today, 30s granularity, capped at 86400) and `total_sessions_managed` (lifetime count of *.jsonl files ever seen under ~/.claude/projects/, capped at 10000000). Worker still accepts v1 + v2 payloads. New `/api/telemetry/heartbeat` endpoint on the server is the local-only counter the dashboard pings every 30s while the tab is visible; CCC_TELEMETRY_DISABLED kills it. Stats page shows both new numbers per-install in the recently-active table.
- Added a Push all waiting summary with a Continue action while sessions are replying to commit nudges.
- Show Token Optimizer quality scores beside sidebar context percentages, and preview captured annotation screenshots in the annotation editor.
- Add a Queue header toggle for wrapping long issue titles in the right rail.
- Show Token Optimizer session quality beside the context estimate in the conversation input strip.

### Changed
- Document browser/UI verification (puppeteer via node snapshot.js) in AGENTS.md for Codex sessions — not the iab in-app browser or Playwright.
- Reveal the sidebar row brief (repo/source/branch metadata + goal) only on the selected/active row — no longer on hover or focus.
- Document the browser/UI verification path (puppeteer via snapshot.js, or chrome-devtools MCP) in CLAUDE.md — Playwright is not a dependency.
- Demoted the new-session center panel and grouped newly spawned sessions under a default Inbox object.
- Changed the queue add control to use a larger multiline ticket composer instead of the native browser prompt.
- Restore hue-based colorful backgrounds for repository chips in sidebar row previews.
- Document WSL2 as the supported Windows route and point unsupported installer runs there.

### Fixed
- Remove the redundant collapsible All header from the All sessions tab.
- Fixed page annotation screenshots so they are captured before the note/queue dialog opens.
- Let annotations fall back to native screenshot capture when browser pre-capture fails.
- Improve annotation screenshot failure diagnostics and carry warnings into queue context.
- Restore the Push all control on All/by-project repo headers.
- Improve assistant-message timestamp spacing and readability in conversations.
- Fixed Claude goal strips so active goals show a compact clear action instead of being stuck without a cancel affordance.
- Fixed crowded conversation breadcrumbs by shortening the stream-json process pill and hiding the toolbar-only freshness clock.
- Fixed the conversation composer so clearing a tall draft shrinks the input box again.
- Composer textareas no longer rely on native field sizing, avoiding a WebKit/WKWebView gray overlay artifact in the input box.
- Show current-session DONE briefs on row hover and keyboard focus.
- Spawned sibling sessions now persist parent linkage from `report_to`/`parent_session_id`, recover legacy return-address footers when available, and render nested under their dispatcher in Current Sessions.
- Current sessions now respect the Active view's 1d/7d/All time filter.
- Unassigned session rows now show a small `+` object chip that opens an object picker and assigns the session to the selected object.
- Move the Evergreen Agents object into its own bottom section in By objects and keep it out of Project tree.
- Place Evergreen Agent queue progress directly before the state badge.
- Fixed CPU spike on group-chat creation: added server-side 60 s debounce to `/api/group-chat/nudge` so the reader's 3 s poll no longer triggers back-to-back LLM calls per participant.
- Fixed 78–95 % CPU when a group chat is open: `_group_chat_participant_meta` now caches per-session liveness for 10 s instead of forking `ps` up to 20 times per participant on every 3 s poll. Read cache TTL also raised from 2 s to 3.5 s so consecutive polls share one build.
- Fixed group-chat rows so they obey the In Progress 1d/7d/All time window.
- `/compact` on a live headless stream-json session (voice/car-mode) now compacts in place by sending `/compact` over the session's own stdin, instead of killing the session and waiting 180s in a hidden terminal for a boundary that never appeared. The live status (Compacting… → Compacted, or the exact engine error) is surfaced in the response and toast.
- Keep live WIP and tool-state badges visible after archive cache rebuilds.
- Markdown file previews now temporarily widen the right rail and restore the previous rail width when closed.
- Fixed mobile live Bash/tool indicators so long command details stay collapsed by default.
- Fixed the add-to-object picker so it shows nested objects as a hierarchy.
- Make object rename Enter/Save apply immediately without rerendering the full sidebar.
- Allow the by-objects splitter to collapse Current Sessions completely.
- Restore object number prefixes in the project tree.
- Drop the redundant "Q" prefix from Token Optimizer quality badges.
- Normalize the Queue scope alias CC to the CCC project.
- Make queue status icons larger and add a glow to in-progress tickets.
- Move the right-rail Annotate action into the rail top bar.
- Hide the repeated session title from the status-rail breadcrumb while keeping the rail header title visible.
- Fixed object draft sync so open tabs preserve and ingest server-created drafts they have not loaded yet.
- Fixed Sonnet model pills and queued model switches so they no longer show or send a 1M-context variant.
- Fixed legacy spawned sessions missing hierarchy by adding a backfill that persists recoverable parent session links from CCC return-address prompts.
- Fixed terminal question answers getting queued instead of waking the waiting session.
- Show numbering on top-level objects in the by-objects view.

## [5.4.0] - 2026-06-25

### Added
- By-objects view now includes Expand all and Collapse all controls for object groups.
- Conversation rows now show a codex session's current **goal** as an inline chip after the title. The objective and status come from codex's native `/goal` feature (read server-side from `~/.codex/goals_1.sqlite` in one batched, cached snapshot — no per-row work), colored by status: active = green, paused/blocked/usage-or-budget-limited = amber, complete = muted. Goal set/clear reflects on the next serve without waiting for a transcript rebuild.
- Show Codex session goals above the composer and distinguish paused or blocked goals in session rows.
- Codex `/goal` now works on **dormant** sessions, not just a live TUI. `/goal <objective>` and `/goal clear` route through the app-server RPCs (`thread/goal/set` / `thread/goal/clear`) when the session has no live terminal, instead of bouncing `codex_slash_requires_live_tui` — mirroring how `/compact` already drives codex. The `pause`/`resume`/`edit` subcommands have no RPC and still require a live TUI. (Only reaches threads CCC's own app-server owns; a goal set in Codex.app's own UI is out of reach.)
- Added ArrowUp recall for the last sent composer command when the input is empty.
- Added a **COO board** — an at-a-glance live-status view of the sessions you're tracking, opened from a new **COO** button in the topbar (left of Today) or the `/coo` route. Starts empty and tracks whichever sessions you add.
- Added `POST /api/coo/tracked` — persists the COO row-checkbox tracked-session set to `coo-notes.json` as a top-level `tracked` array, so a shell Monitor and the `/api/sessions/events` SSE stream share one source of truth with the dashboard checkboxes. The browser mirrors toggles to the server (fire-and-forget) and a fresh browser seeds its set from the file.
- Add a compact copy button on successful transcript Done rows to copy the preceding agent answer.
- Add a by-objects row action to elevate a session into its own new object.
- Add goal-strip actions so Codex goals can be edited, cleared, paused, or resumed directly from the conversation view.
- Added inline CLI login for headless onboarding so Claude Code and Codex auth can run inside CCC without SSHing into the host.
- By-objects headers now include an archive button for custom objects; archived objects and their rows leave the active by-objects conversation view.
- Added `GET /api/sessions/events` — a Server-Sent Events stream that pushes session-state changes (state, question_waiting, needs_approval) to subscribers, so a monitor session can react to changes instead of polling `/api/sessions` on a timer. Sends a baseline snapshot on connect, then deltas, with a `: keepalive` comment every ~20s.
- By-objects view now supports Cmd/Ctrl/Shift-selecting multiple conversation rows and dragging them together into an object, with edge auto-scroll while dragging through long lists.
- Added System Health rows for shared GUI app engines with graceful Quit controls for Cursor.app, Antigravity.app, and Codex.app.
- Conversation search now augments local and claude-index matches with Total Recall session hits, filtering out knowledge/document results so the sidebar remains a sessions-only search surface.
- Add an inline Steer action on Codex user messages so previous prompts can steer the running turn directly.
- Sessions can now be **retired** from the UX-fixes-queue "continue" nudge watcher. Previously a worker that finished its tickets kept getting auto-nudged with `continue` forever as new tickets arrived in its project, with no off switch. New `POST /api/uxq/retire {session_id, retired?}` adds (or removes) a session from a `uxq-nudge-optout.json` skip-list the watcher honors before any ping — muting a parked session without draining the whole project queue.
- **Worktree init docs.** Added `docs/worktree-init.md` and linked it from the README so repo-specific setup hooks for fresh worktree spawns have a clear user contract.

### Changed
- By-objects grouping now lists custom objects before repo groups, and newly created objects appear at the top of the object list.
- Only show row-level COO tracking checkboxes after COO mode is opened, and make by-object group chevrons explicit collapse controls.
- **Landing page copy.** Updated `ccc.amirfish.ai` metadata, hero, install command, platform language, and comparison table to match current v5 engine and Linux support.
- Increase right-rail Queue tab typography so ticket rows and empty states are easier to read.
- Move session provider icons into the left row rail and enlarge right-rail queue ticket text.
- New-session folder shortcuts are now labeled as recent folder choices, and the folder dropdown is labeled as the full suggestion list.
- By-objects groups now accept session drops in the group body below the header, and custom object names can be renamed inline from the object header.
- Increase by-objects header title sizing for easier scanning.
- Make sidebar + object create a visible draft object and start inline rename instead of opening a naming dialog.
- Object rows in the by-objects view now use right-side controls for rename, play/pause, archive, and collapse.
- Move by-object task creation into the object header action row as a compact plus button.
- Move Files into its own right-rail tab between Metadata and Queue, while keeping Activity inside Metadata.
- The In progress row-list header now shows **+ object** before switching to by-objects mode, and creating one switches the list to object grouping so the new target is visible.
- Changed the right-side session rail into compact Metadata, Files, and Activity tabs.
- Rename the Archived sidebar tab to All and show active plus archived sessions there.
- Move sidebar row repo/source/branch metadata out of the resting state and reveal it on hover, focus, or active selection.
- Improve sidebar hover-preview legibility, space the Project Tree header, and remove the Active view grouping toggle.
- Put repository chips first in sidebar row previews, followed by goals, and remove engine/source labels from the preview line.
- Keep the by-objects project tree scroll position stable across refreshes, keep the Project tree header fixed outside the tree scroller, make Current sessions resizable with brighter compact titles, avoid duplicate title tooltips in Current sessions, move row metadata and goals into hover-only detail, and limit right-rail Metadata scrolling to the Files list.
- Move sidebar conversation search into the New session row.
- Object rename now uses a transparent stroke pencil icon instead of the fallback glyph.

### Removed
- Remove the manual Done bucket from the Active conversation sidebar so active rows stay in their normal groups.

### Fixed
- Fixed Active search feedback and Details-off row density for conversation search results.
- Tightened the Active sidebar by removing the redundant In Progress header, appending new object tasks below existing drafts, and showing Codex working state in rows and the conversation pane.
- Restore normal sidebar scrolling on the All tab when Active is saved in By objects mode.
- Preserve By objects split-pane scroll positions across periodic sidebar refreshes.
- Fixed By objects scrolling so the left conversation list no longer scrolls as a whole; current sessions and the project tree scroll independently.
- Align By objects draft rows with the real session rows inside object groups.
- Fixed several Active sidebar polish issues: draggable draft tasks, quieter summary details controls, remembered conversation searches, and separate project-tree scrolling.
- Codex new-session defaults keep `gpt-5.5` as the best-model default while still requesting `model_context_window=1000000`; choose `gpt-5.4` explicitly when the larger effective context window matters more than model quality.
- Codex spawn logs no longer show bare `[error]` noise from benign startup stderr or unsupported async plugin hooks above an otherwise normal Thinking state.
- Clear stale optimistic Thinking indicators when Codex compaction completes.
- Codex inputs sent during an active app-server turn now use CCC's durable pending queue instead of a volatile app-server queue.
- Keep Codex app-server queued follow-ups visible as queued input instead of leaving the optimistic echo stuck pending.
- Label Codex turns with no recorded assistant text as “No visible response” instead of an ordinary Done card.
- Codex stuck-state wake pills now show in-place feedback while a wake request is being sent.
- Prevent compaction while a sent message is still pending in the transcript.
- Conversation history loading now keeps hover prefetches and "Load earlier" paging bounded to transcript windows instead of full-loading large session logs.
- Cross-repo GitHub issue and Ready to merge feeds now ignore stale recent repos that are no longer in the known repo list.
- Keep Current sessions titles visually plain while preserving rename and AI markers in the rest of the sidebar.
- Keep the by-objects Current sessions panel from reopening below the seven-row preview height.
- Keep by-objects current-session rows compact so the collapsed section shows the intended seven sessions plus More before the project tree.
- Align by-objects draft task rows with real session rows under the same object.
- Fixed By objects draft task rows so clicking the task or its inline text opens the task details pane.
- Claude Code hooks now use an absolute Python interpreter path, preventing PostToolUse failures when the hook environment cannot resolve `python3`.
- **Linux installer gate.** The curl installer now accepts both macOS and Linux, keeps dependency messages platform-neutral, and opens the app with `xdg-open` when `open` is unavailable.
- CCC.app's main window can now resize down into the dashboard's narrow single-column layout instead of being clamped at 900px wide.
- Mobile conversation rows open from the first tap more reliably, and the conversation list avoids touch-scroll jank from per-row touch handlers and reorder measurements.
- Mobile conversation rows now avoid first-tap drag interception and defer sidebar refreshes while the list is scrolling.
- Remove the legacy floating mobile reload button so it no longer overlaps the composer.
- Hide empty object hints when an object contains nested child objects.
- Fixed the By objects view so current sessions leave room for seven rows plus More, and draft tasks align with session rows while keeping Play visible next to the task text.
- Object draft task prompts are preserved when titles change and can be edited or launched from the draft inspector.
- Larger by-object group titles so object rows read clearly in the active conversation list.
- Fixed the by-objects header plus button so it creates a child object instead of a draft task.
- Polish by-objects headers with uppercase root titles, title-click expansion, and larger chevrons.
- Fixed by-object hierarchy controls so bulk collapse hides sessions while keeping objects visible, chevrons collapse nested object subtrees immediately, and level colors avoid the purple nested chip.
- Keep inline object-task drafts intact when the conversation list refreshes while the user is typing.
- Number slash-separated by-objects headers so ordered object task lists are easier to scan.
- Kept timed-out spawn placeholders visible with retry and dismiss actions instead of silently removing them.
- Align By objects draft task rows with real child sessions in the project tree.
- Clarify the Queue tab empty state by showing the scoped project and whether tickets exist in other projects.
- Let right-rail queue rows reveal more note text as the rail is widened instead of pre-truncating every note.
- Hide empty by-objects groups while filtering conversations with search text.
- Restore sidebar search-result previews in the By objects current-sessions band.
- Fixed Push all ship-status polling so sessions with no concrete repo path do not repeatedly request ship status.
- Show sidebar DONE briefs only on the selected current session instead of on hover.
- Restore Current Sessions hover previews and keep left-side model icons from overlapping row titles.
- Align sidebar hover metadata with session titles, center left-side model icons, and make DID briefs brighter while omitting next-step text.
- Refine sidebar object grouping, tab order, merge-row filtering, and chip color modes.
- Let sidebar session outcome summaries wrap in a full-width bubble instead of truncating the completed-work text.
- Sidebar session titles now hide leading pasted-image paths so current-session rows stay focused on the actual task.
- Sidebar rows now keep object header actions hidden until hover or keyboard focus, label session outcome summaries as muted DONE metadata, identify COO status pills, and render silent Codex completions as quiet non-stuck status rows.
- Keep the By objects current-sessions band tall enough for seven rows plus More, and consolidate the right status rail into Metadata and Queue tabs.
- Gave Total Recall conversation search enough time for cold starts so TR results do not disappear as timeouts.
- Prioritized Total Recall session matches in conversation search so TR results are visible instead of buried after history hits.
- Sidebar conversation search now paints Total Recall session matches as soon as Recall responds, instead of waiting behind the slower local history search.
- Kept Total Recall session matches visible at the top of by-objects conversation search results instead of hiding older hits below current sessions.
- Project-named UX-fixes worker rows now show queue progress even when claims are recorded under a worker alias.

## [5.3.0] - 2026-06-17

### Added
- Attention API: a soft-block detector flags sessions that ended a turn awaiting you in prose ("paused for review", "want me to…", "pick one") — not just the formal AskUserQuestion/approval prompts. New cross-repo `GET /api/attention?scope=all` feed (session, repo, detected question, last few turns), a lightweight `GET /api/session/<id>`, optional `?since/state/limit/fields` projection on `/api/sessions`, and "Needs your attention" now renders across all repos with no repo selection.
- Added native Hermes conversation history rows and transcript viewing from `~/.hermes/state.db`.
- First-class headless Linux support: the server and browser UI run on Linux, `./run.sh --install-service` installs a systemd user service, and the macOS-only desktop conveniences (jump-to-terminal, screenshots, open-in-desktop, native folder picker) hide cleanly so there are no dead buttons. The system monitor reads memory and load from `/proc` and stdlib on Linux.
- Deep relative transcript links: a reveal-only bounded search resolves deep relative paths (CCC-143), and bounded-walk markdown links now open the target rather than only revealing it in the file panel (CCC-146).
- Queue/board controls: an add-to-queue button in the Files/Queue panel header (CCC-145), and a manual Done section above In progress that's always shown so the first drop has a target (CCC-151).
- `GET /api/session/<id>` now returns `mtime`, `folder_label`, and `sidecar_in_flight` so clients can show freshness, the originating folder, and whether a sidecar write is mid-flight without a second request.

### Fixed
- `find_all_conversations` no longer re-parses every transcript on each request: a cache-key bug compared a JSON-decoded list against the tuple it was stored under, so the lookup never hit and a busy server — and every restart — paid the full warm-scan cost on every call. Restores the `(mtime, size)` cache hit and clears the restart wedge.
- Hermes conversation rows can now be resumed from the CCC composer instead of becoming read-only after the first message.
- Linux headless Claude sessions now route follow-up messages through CCC's FIFO instead of misdetecting `?` as an interactive terminal.
- Smoke test no longer spawns a real interactive `claude` process when exercising the compact path, so CI and local `pytest` can't hang waiting on a live session.
- **Throughput dashboard** now opens on the last-hour aggregate view and charts aggregate ranges by hour instead of plotting every model call as an unreadable per-turn line.
- **Throughput dashboard** now deduplicates repeated Claude assistant snapshots, reads every Codex `token_count` event for aggregate usage, prices current Claude/Codex models, and accounts for Anthropic 1-hour prompt-cache writes.
- UI polish: the live-question answer card scrolls into view on mount (CCC-150), archive is a one-click action on live rows (CCC-149), empty thinking turns no longer render as "Ran 1 command" (CCC-148), headless/terminal pills show in the conversation popout (CCC-147), the new-project name field stays editable (the CWD suggestion no longer steals focus, CCC-144), the in-progress queue pill is marked with ▶ so it can't read as done (CCC-142), and mobile reclaims conv-header space with scrollable agent tabs and a tighter toolbar.

## [5.2.0] - 2026-06-12

### Added
- Throughput bar shows cache-adjusted token burn rate so cached prompts don't inflate the displayed speed.
- Gold icon + `$` badge on Fable-5 sessions in the conversation list so they stand out at a glance.
- "Waking up headless…" indicator shown when sending a message boots a fresh headless resume, so the wait is never silent.

### Changed
- New-session stage: repo chips, CWD picker, and worktree toggle moved from the center of the screen to just above the input bar for a cleaner hero and more natural workflow.
- Show cache-adjusted token throughput with per-model breakdowns on the throughput dashboard.

### Fixed
- 1d / 7d / All window toggle now always visible on the Archived tab (was hidden due to a stale flag).
- Group chats now sorted by most-recent message date, consistent with the conversation list.
- Inline-code paths to images, documents, and media files are now rendered as clickable open links.
- Model and token bar now renders correctly in the second split pane, not just the primary one.

## [5.1.0] - 2026-06-10

### Added
- New-session page now offers "Extend CCC" integration-recipe cards (driven by `kind: "extension"` entries in `static/templates.json`): click a cookbook recipe — Annotate → UX-fixes queue, or bug-report widget → GitHub issues — to prefill the composer with a ready-to-run integration prompt for your own app's repo.
- Flow: "+ object" button creates empty object groups, group headers drag to reorder or nest in by-objects view, and an object-group name opens its Flow MD doc (CCC-92, CCC-93)
- Cookbook: Gmail hourly triage → iMessage alert recipe (CCC-101)
- Group chats: add-participant picker on chat rows and the reader header, plus "Join with this link" — paste a token into any session's composer to join (CCC-97, CCC-98)
- Files panel flips to a Queue view for UX-fixes worker sessions, and the server auto-nudges idle queue workers (CCC-100)

### Changed
- New-session stage polish: chooser highlights the default path, centered and roomier prompt box, fun random project names; engine icon moved to the far right of conversation rows (CCC-87, CCC-94)

### Fixed
- Fixes: Launch no longer forks a session a live headless process owns (CCC-96), load-earlier banner no longer auto-fires on open, repo chips track manual CWD edits (CCC-91), Files-panel Queue view scopes to the worker's project (CCC-99)

## [5.0.1] - 2026-06-10

### Added
- Added an aggregated "Total (Last 7 Days)" view in the Token Throughput Analyzer to inspect combined token throughput trends and metrics across all sessions active in the last 7 days.
- Fixed an issue where stale conversation parsing cache entries would show zero token counts before the server reload.

### Changed
- Improved mobile tap target sizes and spacing for conversation list rows, and added instant tap/click feedback animations.
- Conversation search now keeps its index fresh automatically: every search quietly triggers a throttled incremental re-index in the background (only transcripts that changed are re-read), so search results track live conversations without any manual re-index step.

### Removed
- Removed the horizontal-drag gesture from the conversation pane that triggered the conversation collapse feature.

### Fixed
- Fixed the backend arguments passed to find_all_conversations in the 7 days throughput aggregation view API.
- Fixed token metrics for Codex sessions by forward-mapping token counts from result events to assistant events.
- Fixed token metrics for Antigravity sessions when the TUI app is offline by reading directly from the local SQLite conversation databases.
- Server failed to start on Python 3.10/3.11 (`SyntaxError: f-string expression part cannot include a backslash` at import) — introduced 2026-06-05, shipped in v5.0.0. The Terminal.app jump-to-session script now builds its AppleScript outside the f-string.

## [5.0.0] - 2026-06-09

### Added
- Claude Fable 5 support: new top-tier model available in the model picker, set as the default for spawned Claude sessions, with live mid-session switching. The Claude model picker is now a faithful replica of Claude Code's native `/model` menu (number-key shortcuts, 1M-context variants, Fast mode toggle on Opus models).

### Fixed
- Fixed model switching failing with "There's an issue with the selected model": versioned aliases like `sonnet-4-6` are now expanded to full `claude-` IDs before being passed to the CLI's `--model` flag on spawn and resume.

## [4.11.0] - 2026-06-09

### Added
- **ACP server adapter** (`ccc_acp.py`). Exposes CCC over the [Agent Client Protocol](https://agentclientprotocol.com) so editors and ACP clients (VS Code, JetBrains, Zed, Hermes) can drive Claude Code sessions over JSON-RPC stdio. Optional: requires `pip install agent-client-protocol` and is launched separately (`python3 ccc_acp.py`); the core stdlib-only server is unaffected.
- Added a standalone group-chat live view with chat-style reading, posting, nudging, and a searchable participant picker.
- **Kilo Code CLI engine.** Spawn headless [Kilo Code CLI](https://kilo.ai/docs/code-with-ai/platforms/cli) runs as a first-class engine alongside Claude, Codex, Cursor, and Antigravity. New `/api/sessions/spawn-kilo` and `/api/sessions/spawn-kilo/availability` endpoints, an engine entry in the spawn dropdowns, and worktree support. Override the binary with `CCC_KILO_BIN` and the default model with `CCC_KILO_MODEL`.
- **Kilo Code session ingestion.** Externally-launched Kilo Code sessions now appear on the board alongside Claude/Codex/Cursor/Antigravity. CCC reads Kilo's SQLite store (`~/.local/share/kilo/kilo.db`) read-only via `find_kilo_conversations`, surfaces each session's title, cwd, model, and live status, and renders full transcripts (user / assistant / tool calls) through `_parse_kilo_conversation`. Detection (`_is_kilo_session`) now probes the DB so historical and terminal-launched sessions route to the Kilo transcript loader.
- Added a microphone button to both the main conversation input bar and the group chat input row, enabling users to dictate speech directly into the input textareas using the Web Speech API.
- Added support for new model releases (Gemini 3.5 Pro/Flash, Claude Sonnet/Haiku 4.8, OpenAI o4/o4-mini, and StepFun/GPT-6.0) in the user interface model selection dropdown.
- Added a premium step-by-step onboarding experience to guide first-time users through agent CLI detection (Claude Code, Antigravity, Cursor, Codex), account setup, and spawning their first session.
- `/api/sessions/spawn` accepts an optional `report_to` (return address) — the dispatching session's UUID. When set, the spawned session is instructed to POST one structured completion report (STATUS, summary, file paths, failure reason) back to that session via `/api/inject-input` when it finishes. Aliases `return_to` / `reply_to` accepted; value is validated as a session-id-shaped string.
- Public stats page at https://ccc.amirfish.ai/stats backed by a new read-only `/v1/stats` endpoint on the telemetry worker. Shows live aggregates only: distinct opt-in installs, active-today, anonymous boots per day, version + platform breakdown, and per-install sessions-in-last-24h (just an 8-char install_id prefix, never the full id). Worker caches the response 5 min at the edge so the page is hammer-safe.
- Added a dedicated Token Throughput Analyzer page (`/throughput`) and corresponding `/api/throughput` endpoint to measure active LLM processing speed (TPM/TPS) and turn durations.

### Changed
- Draws a visual horizontal line indicator with a progress percentage label and a custom accent handle as the user drags horizontally to collapse/fold earlier conversation messages.

### Fixed
- UX fixes queue worker rows now show the current queue item and latest queued item, updating when annotations add new fixes.

## [4.10.0] - 2026-06-07

### Added
- "+" on a repo or object node in the Flow workspace now opens a session picker instead of immediately creating a draft. The picker shows:

- **+ Create a new draft session here** at the top (the original behavior — one click, same as before).
- **Search box** — fuzzy-matches against display name, first message, session id, repo / folder label, and engine name.
- **Include archived** checkbox (on by default per user ask).
- **Scrollable session list** — up to 200 results sorted by most recent activity; click a row to attach.

Clicking a session sets `flowNodeParents[<session-node-id>] = <parent-node-id>` so it nests under the object/repo on the next render, and pins it to the flow board so an archived session attached this way stays visible regardless of the toolbar's archive toggle.
- The Flow workspace toolbar now has a "Search sessions…" input on the left. Mirrors the main sidebar's `#convSearch` — typing in either updates the other and re-renders the flow board with the filtered set via the existing `filterConversations`. Initial value reads from `$convSearch` so a search you started in the main window carries into the popout. Esc clears the filter. 120 ms debounce so fast typing doesn't thrash the canvas innerHTML.
- Telemetry schema v2: opt-in daily ping now includes `sessions_today` (count of JSONL transcripts modified in the last 24h, capped at 100000) so we can tell opt-in-then-bounced installs from sticky installs without identifying individual sessions. The worker still accepts v1 payloads. Also added a new `POST /v1/open` anonymous open beacon that fires once per server boot with only `schema_version`, `version`, `platform` — no install_id, no engines, no identifier of any kind. The beacon is not gated on opt-in (because it carries no identity) but is still killed by `CCC_TELEMETRY_DISABLED=1`, the single user switch for the whole process. Engines list expanded to include `cursor` and `antigravity` (already detected by `server.py`). See `docs/telemetry.md` for the full contract.

### Changed
- Reverted the tight-canvas + dashed-edge + diagonal-stripe marking shipped earlier today per user pivot ("let's get rid of borders, we should just have a big canvas that's it"). Canvas is back to viewport-fill (`max(content + per-node-pad, viewport)`) with no edge outline and the original solid `var(--bg)` board background. The overlap-prevent + low-zoom font-readability fixes from the same day stay — they're independent of the border question.
- The in-app terminal panel's keyboard shortcut moved from ⌘\` to ⌘2. ⌘\` is macOS' system-level "cycle windows of active app" shortcut — CCC also exposes it via the .app shell for cycling between CCC's own windows (Sparkle release pending) — so the prior binding hijacked the cycler. ⌘2 is unused by CCC and free across all major browsers. The handler also skips while focus is in a text input / textarea / contenteditable so a literal "2" mid-draft never accidentally toggles the panel; the topbar button still works as a fallback. Tooltip on the topbar button now advertises the new shortcut.
- TTS rate knob is now restricted to 0.8 – 1.3× (was 0.5 – 2.5×). Anything outside that window pushes playback into the "robot at half-speed" or "auctioneer" zone that nobody actually wants. Tighter slider means a misclick can't yank you all the way to either extreme. Existing bounds-check in `_readPersistedTtsRate` clamps any stale localStorage value (e.g. 1.5 saved during the wider range) back to the default on next boot.

### Fixed
- `/compact` on a Claude session that's still running headlessly no longer rejects with "Wait for it to finish, then click Compact again". Now it's queued via the standard terminal-input queue — same path regular injections take when the agent is busy. The queue drains automatically the moment the headless run finishes and an interactive terminal opens, so `/compact` runs without a second user click. UI toast reads "Queued /compact until the terminal session is idle" instead of "/compact failed". Existing client wiring (`compactRequestSuccessMessage`) already handles the `data.queued === true` response shape, so this is a server-only change.
- When a Claude session's recorded cwd is gone (the folder was moved, renamed, or deleted), sending a message no longer drops the message and shows "Send failed: Session cwd is gone…". The server now queues the text via the standard terminal-input queue and returns `{ok: True, queued: True, cwd_missing: True, missing_path: …}`. The client surfaces a friendlier toast: "Queued — session folder is missing (<path>). Restore it or relocate to resume." The queue drains the moment the directory is restored on disk OR (in a follow-up) the user points CCC at the new location via the relocation cache. Either way the typed message is preserved, not lost.

Implementation: new `_maybe_queue_on_invalid_cwd(session_id, text, status, result)` wraps `resume_session_headless` calls inside `_inject_text_into_session`; when the resume's downstream call returns `code: 'invalid_cwd'`, the wrapper queues the text and returns a queued response shape the client already knows how to handle.
- Group archived Claude Flow session nodes under the real repo path when archive rows also carry a Claude project slug.
- Flow's session-attach picker (the new "+"-on-a-node modal) now actually nests the picked session under the parent. Root: the click handler tried to call `setFlowPinned(rowId, true)` but no such function exists — the real helper is `flowPinnedSessionIds.add(id)` + `persistFlowPinnedSessionIds()`. Without the pin, archived picks failed `flowIsVisibleSession` (it gates on `archived && !flowIncludeArchived && !pinnedInFlow`) and the session never rendered; only the `flowNodeParents` write survived, with no visible effect. Switched to the correct API and pin both shapes (`rowId` + `sid`) so `isFlowPinned`'s id-or-session_id lookup hits regardless of which one the row carries.
- Two flow-board fixes:

1. **Canvas no longer extends forever past content.** Previously the canvas was sized to `max(content + tiny pad, viewport)` — so on a wide viewport with sparse content, the canvas became huge and you had to pan through screens of empty grid to find the next cluster. Now the canvas hugs content + a 5-grid-cell (160px) buffer on each side, and the area past the canvas (the unreachable .flow-board background) gets a faint diagonal-stripe pattern + the canvas itself gets a dashed orange outline so the boundary reads as "this is the edge, can't pan past it".

2. **Node text stays readable when the canvas is zoomed out.** `.flow-world` applies `transform: scale(--flow-zoom)` which shrinks everything linearly with zoom — at 62% the title font dropped to ~8px on-screen and got unreadable. Each node's title / meta / kicker now uses `clamp(base, base / zoom, 2× base)` so the on-screen pixel size stays near the base when zoomed out (and never blows up to comically huge at extreme zoom-outs).
- Dropping a node on top of another node no longer leaves them overlapping. On pointer-up the drop handler now runs `_flowResolveNodeOverlap` against every non-dragged node — if the dropped position collides with anything, the dragged node is pushed in the cheapest cardinal direction (right / down / left / up) by the minimum needed to clear, with a 4 px enforced gap. Iterates up to 50 times so chained collisions resolve cleanly. Only runs when the drop landed on empty canvas — drops onto a real drop-target (reparent to repo/object, add-to-group-chat) keep their existing semantics.
- Show Flow repo work items that only exist in the Flow markdown index, even when they have no visible sessions or drafts.
- Fixed Flow repo/object clicks leaking an empty Files panel into the right sidebar.
- Attach Flow session nodes to their repo when the session row only reports `session_cwd`/`cwd`.
- Headless Claude resume/spawn prompts now strip lone UTF-16 surrogates at the final stream-JSON writer, and the regression tests no longer contain literal surrogate escape text that can poison another agent's transcript.
- Sent messages that the agent never acknowledges no longer vanish silently from the conv view. Earlier, after a 5m15s timeout, the pending `.event.user_text.pending` div was just removed — the user would see their typed text gone and assume it had been delivered. Now the timeout converts the div to a `.not-acknowledged` state with a red border, a "⚠ Not acknowledged by the agent — your message may not have been delivered" label, a **Re-send** button (re-injects via the standard `injectToSession`), and a × to dismiss. The text the user typed is preserved as the bubble's body so they can copy it if needed, even after dismiss.
- Fixed slash command picker rows so mouse and touch presses select the command instead of leaving the dropdown open.
- The terminal panel now auto-scrolls reliably as new output arrives. Root: the "is user near the bottom" check ran AFTER the new line was appended to `#termLog`, so `scrollHeight` had already inflated by the just-added height and the threshold (`< 80px from bottom`) was missed by exactly that much. Users who were glued to the bottom saw the autoscroll silently skip. Fix: capture `wasNearBottom` BEFORE the `appendChild`, decide based on that. Scrolling-up-to-read-history still pauses autoscroll — that intentional behavior is unchanged.
- The terminal panel's "Pick a repo" placeholder is now clickable. Previously the label was passive text — user had no way to actually pick a repo from inside the terminal panel and had to navigate back to the sidebar dropdown. Now the placeholder shows a pointer cursor and click opens the existing repo-picker modal. `openRepoPickerModal` is exposed on `window.cccOpenRepoPicker` so inline `<script>` blocks living outside the main app.js IIFE (like the terminal panel's script) can call it. A new `ccc-repo-changed` CustomEvent fires whenever `setArchiveFolderFilter` changes the active repo so the terminal panel refreshes its cwd display without polling.

## [4.9.0] - 2026-06-06

### Added
- Codex sessions now show a live state badge (Working / Idle / Stuck / Offline) on the conversation row and in the conversation pane, derived from the rollout log — fixing pool-model codex sessions that previously showed no activity indicator.
- Flow edges (the curved lines connecting child nodes to their parent object/repo) are now first-class objects you can manipulate:

- **Click an edge to select it.** Selected edges thicken and turn orange so they stand out from the rest of the board.
- **Backspace / Delete with an edge selected** removes the parent assignment; the child falls back to its default repo group (or no parent). Skipped automatically when focus is in a text field so the shortcut doesn't hijack typing.
- **Drag any edge to reconnect it.** Pointer-down on the line starts a reparent drag — a dashed ghost line follows the cursor, candidate parent nodes light up orange, and dropping on one re-links the child to that new parent. Drop outside any node to cancel. Cycle-prevention: a node can't become its own ancestor.
- **Click background or hit Escape** to clear the edge selection.

Edges now render as `<g class="flow-edge">` carrying a wide invisible hit path on top of the thin visible line — clicking the visible 1.6px stroke is unreasonably hard, so the hit-target widens to 14px while staying invisible.
- Group chats are now first-class nodes on the Flow workspace alongside repos and objects:

- **Render**: every entry in the existing `_gcActiveChats` cache shows up as a cyan-accented `flow-node-group-chat` card with the chat's topic, participant count, status, and last-activity timestamp.
- **"+ Group chat" toolbar button** sits next to "+ Object". Click it and the existing new-group-chat dialog (window-prompt for the name, `/api/coordinate` POST) runs; once `pollGcActive` refreshes, the new node appears on the board automatically.
- **Drag a session node onto a group-chat node** to add the session as a participant — same outcome as dragging a conv-list row onto a chat row in the sidebar. The session card snaps back to its repo cluster (sessions stay under their repo for layout purposes; the chat just registers the participation via `/api/group-chats/add-participant`).
- **Click a group-chat node** to open the chat reader through the existing `openGroupChatReader` entry point.

All three node kinds (repo / object / group-chat) participate in the Organize layout the same way — they anchor at their current position and the overlap resolver minimises movement.
- Added a button to the Flow popout's toolbar (the small split-rectangle icon) that toggles a conversation reader pane on the right side of the popout window. With the reader on, clicking a node in the flow board mounts that conversation into the right pane through the normal `selectConversation` path — same conv reader, same input bar, same TTS / Esc / Send buttons. With the reader off (the default), the flow board fills the whole popout. The toggle is persisted across popout reloads in localStorage (`ccc-flow-popout-reader`). No new conv-rendering code — just un-hides the existing main pane and splits the viewport via CSS.
- The Flow view now has its own pop-out button in the flow toolbar (next to the Expand toggle). Click it and the whole Flow board opens in its own window — a native CCC window when running inside the macOS app, a browser popup otherwise — reusing the same `window.open` + `cccNative.openPopout` + `/api/open-browser` fallback chain the conversation pop-out already uses. Boot-time detection of `?ccc_popout=flow` adds a `body.flow-popout` class, forces `ccc-session-view=flow` in localStorage so the popped-out tab lands on the board immediately, sets the window title to "Flow", and CSS hides every other surface (main pane, topbar, attention panel, conv list, kanban) so the flow board fills the viewport. The button is hidden inside the popout itself (no point popping a popout).
- Flow popout's conv reader (right pane) is now draggable: a 6px column between the flow board and the reader can be dragged left to widen the reader or right to narrow it. Width persists across reloads in `ccc-flow-reader-width`. Bounds: min 280px, max viewport width − 320px so the flow board stays usable. The CSS custom prop `--flow-reader-width` drives the .main flex-basis so the change is instant and animation-free.
- Flow adds Record mode and Organize+ for replaying recorded manual layout preferences.
- Flow repo/object nodes now open editable Markdown-backed work-item status pages in the conversation pane, with refreshable auto sections and deterministic per-work-item accent colors on the board.
- New "Group chats" modal lists every group chat with a per-row pause / unpause button. Opens from the small ⚙ button next to "+ New Group chat" in the sidebar. Each row shows the topic, current status (active / paused / closed) in colour, the participant count, and time since last activity. Pausing or resuming routes through the existing `setGroupChatPaused` API (and benefits from the optimistic local update so the row's status flips immediately). Sort: newest activity first. Esc or backdrop click closes the modal.
- Cmd+` cycles through CCC's open windows (main ↔ flow popout ↔ conversation popout), and Cmd+Shift+` cycles in reverse. Both surface as explicit "Cycle Through Windows" / "Cycle Through Windows (Reverse)" items in the Window menu, so the keystroke is bound at the menu-bar level — macOS' default Cmd+` works for AppKit apps with multiple windows, but WKWebView often swallows the keystroke before AppKit sees it, which is why pop-outs felt like dead ends. DMG users get this only via a Sparkle release (scripts/macapp/ change).
- ``` ```mermaid ``` fenced code blocks in assistant messages now render as actual SVG diagrams instead of showing raw `flowchart TD …` source. `renderCodeBlock` emits a `.mermaid-block` carrier whose `.mermaid-source` pre is the offline fallback; a lazy loader fetches `mermaid@10` from `cdn.jsdelivr.net` on first appearance and converts every pending block into an SVG. Hooked into the existing conv-view MutationObserver (the same one that tags blocks for RTL), so every render path — assistant text, stream bubbles, group-chat messages, issue bodies — picks up the rendering for free. Loader is cached after first call; if the CDN is unreachable, the fallback source pre stays visible with a `data-mermaid-error="load-failed"` marker. Diagram theme follows the dashboard theme (`dark` by default, `default` when `[data-theme=light]`).
- The status rail (right-side panel with Original ask / Activity / Files) now has a quick-close × button in its top-right corner. Click it and the rail collapses immediately and stays collapsed across reloads (writes `ccc-status-rail-collapsed=1`, same persistence as the existing topbar toggle). Previously the only way to close the rail was to drag the resizer to the edge or find the topbar toggle button — neither obvious. The × only shows when the rail is open in right-position mode, so it doesn't appear when the rail is already collapsed (the topbar restore button handles the un-collapse).
- Text-to-speech playback now has a live rate knob next to the TTS button — a thin range slider that appears while playback is active or paused, defaulting to 1.25× and tunable between 0.5× and 2.5×. Dragging it cancels the in-flight utterance and re-speaks from the most recent word boundary so the new rate kicks in within ~180ms (debounced so per-pixel drags don't stutter), instead of having to wait for the next message to hear the change. The rate is persisted to localStorage (`ccc-tts-rate`) so it sticks across sessions — set it to 1.2× once and it stays there.

### Changed
- Conversation row's live-tool pill now shows just the tool label (e.g. "Reading file" — glowing when in-flight) instead of "Reading file ...s/claude-command-center/static/app.js". The path detail was ellipsizing into unreadable suffixes and pushing the rest of the row meta (size, branch, age, action buttons) off-screen on narrower sidebars. The full file/command still appears in the hover title, so users who want to confirm exactly what's being touched can read it there.
- Shifted Cursor IDE integration from a planned full two-way chat sync to a metadata bookmark sync. Cursor's Desktop IDE compiles chat UI state into an undocumented Protobuf Merkle tree in `store.db` rather than simple JSON strings. Injecting full chat history natively carries a severe risk of corrupting user workspaces when Cursor pushes minor internal updates. CCC now safely injects only the session metadata into `store.db` so you can see your CLI sessions listed in the IDE sidebar, but the full interactive chat history remains safely decoupled in the CCC dashboard.
- Flow board background now has extra padded pan space around every edge so top-left items can be dragged toward the center of the viewport.
- The Flow toggle button (☷ icon in the sidebar header) now pops the Flow board into its own window instead of swapping the sidebar contents in-place. Reuses the existing `openFlowPopout` helper — native CCC window inside the macOS app, browser popup otherwise. When clicked from INSIDE the flow popout itself, falls back to the legacy in-sidebar swap (no point popping a popout). The "+ New session" / "+ New Group chat" panel and the conv list stay visible in the main window so the user can keep working without flipping sidebar modes.
- Flow "Organize" is now incremental — it keeps repos and objects exactly where you put them and only moves them when it absolutely has to. Per user request: "move repos and objects as least as possible. The only case we're OK moving them is if we cannot form a rectangle that includes the sessions beneath them and the object."

Previously every run bin-packed every chain from the top-left, which scrambled a board the moment you ran it. Now each chain anchors at its root's current position; if two chain bounding boxes overlap, a greedy resolver picks the worst-overlapping pair, pushes the chain that has moved less so far by the minimum right/down amount, and repeats until clean. Re-running Organize on an already-tidy board is a no-op (zero pixels moved). The toast at the end reports the total pixel displacement so you can see how much it had to nudge.

Untouched chains (first-ever Organize, root still at 0,0) seed from the legacy bin-pack cursor so a fresh board still produces a tidy initial layout. The minimum-displacement rule is now R10 in the in-source algorithm doc block.

### Fixed
- "Active Group chat" pill no longer lingers after the user stops orchestration. Two fixes in `gcShouldShowActivePill`:

1. **Hard short-circuit on paused / closed / orchestrator-off.** A chat with `status === 'paused'`, `paused === true`, or `orchestrator_timer_active === false` returns false from the show-gate immediately — the pill claims "active right now"; the moment the user clicks Stop, the pill must respect that, not coast on the trigger-freshness window.

2. **Dropped `last_mtime` from the freshness calc.** It's the chat file's stat mtime which the server bumps on metadata writes (name_map updates, polled sidecar writes), not real message arrivals. The label-side code already filtered it out for the same reason; the show/hide gate now matches.

Plus an optimistic local patch in `setGroupChatPaused` so the pill drops within one render tick of the Stop click instead of waiting for the next 15s `gcActive` poll to land.
- Annotations and any text routed through `_inject_text_into_session` are now stripped of unpaired UTF-16 surrogate code points (U+D800..U+DFFF) before they can reach an Anthropic API call. Symptom: when a pasted annotation or selected DOM text carried a lone surrogate (the browser's clipboard / selection APIs can split a surrogate pair, especially when a selection ends mid-emoji), the downstream Claude session POSTed a request body containing that surrogate and Anthropic rejected it with `API Error: 400 The request body is not valid JSON: no low surrogate in string: line 1 column N (char N)` — same root as `anthropics/claude-code#16294`. Fix: new `_strip_lone_surrogates` helper at the server boundary, called from `_annotation_text` (covers `/api/annotations` and `enqueue_annotation_ux_fixes_queue` payloads) and from `_inject_text_into_session` (covers every other inject path as belt-and-suspenders). Paired surrogates — real astral-plane characters like 😀 (U+1F600), which Python stores as a single code point — pass through unchanged; only LONE surrogates are dropped.
- Fixed Codex slash commands so CCC offers the Codex command catalog and routes `/...` commands through a live Codex terminal instead of sending them as headless prompts.
- Context percentage compact now uses a dedicated compact API instead of injecting `/compact` as ordinary text. Live Claude terminals receive the slash command directly; busy terminals queue it; dormant Claude sessions open an interactive `claude --resume` terminal and run `/compact` there, avoiding the broken headless-resume fallback.
- Cursor backfill now correctly sets the `lastUpdatedAt` field to match the most recent transcript activity, ensuring fresh sessions appear at the top of the Cursor IDE history.
- Cursor sessions that have gone idle no longer show a stale "▶ Bash /bin/zsh -c …" in-flight pill on their sidebar row. Root: cursor JSONLs don't carry per-event timestamps, so `pending_tool_ts` falls back to the JSONL file's mtime, which keeps refreshing as the file appends metadata-only lines. The codex stale-tool check compares `now - pending_tool_ts` against a 15-minute threshold and never tripped because the ts kept looking fresh. A finished cursor turn whose last event was a tool_use would therefore display the in-flight pill indefinitely.

Fix: `_cursor_activity_fields_from_tail` now checks file idleness directly — if the JSONL hasn't been written to in the configured window (default 60s, env `CCC_CURSOR_IDLE_SEC`), it returns empty activity fields regardless of the dangling pending_tool. The pill drops as soon as the session stops emitting events.
- Flow button opens or focuses the Flow popout without embedding Flow in the main sidebar.
- When creating a new flow object via the "+ Object" toolbar button, the new node now lands where the input modal was instead of stacking into a fixed top-left grid. The modal's center is captured *before* awaiting the user's OK (capturing it after would read a zeroed rect since the modal cleans up to `display:none` first), then translated into flow-canvas coordinates accounting for the current `flowZoom` and the canvas's bounding rect. The node is centered on that point. Window-prompt fallback still uses the old grid layout when the modal element isn't available.
- Flow "Organize" no longer lets one repo cluster overlay a child object that sits nested under the previous repo. Root: the placement loop advanced the row cursor by the top-level cluster's own width only — nested clusters were placed to the right of their ancestor but didn't extend the row's right edge, so the next top-level cluster slid right over them. Refactor: group clusters into chains (top-level root + every cluster transitively nested under it), simulate each chain at origin to learn its combined bounding box, then bin-pack chains as single units. The row-budget wrap check now sees the full chain width, so wide chains wrap to a new row instead of bleeding under the next one.
- Two flow-board fixes:

1. **Background pan no longer hitches every 90s.** The `archiveTimes` poller fired its `refreshArchiveData` fetch unconditionally; even though the resulting `renderArchiveList` correctly deferred itself when a sidebar drag was in progress, the queued render kicked in right after the drag ended and could clip the pan. Wrap the poller body with `deferSidebarRenderIfDragging()` so the whole tick skips while panning — the flush-after-drag hook still replays the deferred render the moment the user releases.

2. **Nested objects / repos now stack BELOW their parent, not to the right** when first added. The unplaced-nested seed used to default to `ancestor.right + NESTED_GAP_X` which placed a child repo next to its parent object; the user wanted the layout to match the "Small Projects → video-claw / usage_on_mac" stack-below shape. Seed is now `(ancestor.x, ancestor.y + ancestor.h + CLUSTER_MARGIN)`. Multiple unplaced siblings start at the same slot and the overlap resolver stacks them further down.
- Releasing a flow-board pan no longer snaps the view back to where it started before the drag. Root: `renderFlowSidebar` rewrites `$flow.innerHTML`, which wipes the element's `scrollLeft/scrollTop`. While the user was actively panning, the in-progress drag suppressed renders correctly; the moment they released, the deferred-after-drag flush ran `renderFlowSidebar` and the innerHTML write reset the scroll. Fix: capture the previous scroll position before the rewrite and restore it immediately after `applyFlowZoom` settles the canvas size. The pan now sticks where the user released.
- Flow board pan (click-and-drag the background) no longer jumps every few seconds while you hold the drag. Root: `isSidebarDragInProgress` self-heals by checking the DOM for a `.dragging`-class element — if the boolean flag is true but no node carries that class, it clears the flag (defense against stuck-true after a cancelled drag). The flow pan calls `beginSidebarDrag` but doesn't add `.dragging` to any node (it's dragging the BACKGROUND, not a node), so the self-heal cleared the flag within one render tick. Once cleared, the next periodic `liveStatus` or `liveSessionsActivity` tick passed through `_scheduleSidebarRender` → `renderSidebar` unimpeded, swapped the flow board DOM mid-pan, and the user saw nodes jump. Fix: the pan handler now sets `.is-panning` on the flow board element while the drag is active, and the self-heal selector includes `.flow-board.is-panning` — so the flag stays honestly true for the full pan duration and pollers' renders correctly defer until release.
- The "+ New session / + New Group chat" panel no longer shows up in the Flow pop-out window. It's an entry point into the main dashboard's session-creation flow and has no business in a dedicated flow board view. Added `.new-session-panel` to the `body.flow-popout` hide list alongside the conv list, search bar, topbar, etc.
- Flow wheel/trackpad zoom now defers sidebar refresh renders while the zoom gesture is active, matching the existing pan guard so periodic refreshes cannot interrupt the gesture or snap the board mid-zoom.
- "Launch in Terminal" no longer hallucinates a deleted-worktree cwd and drops the user in their home dir. Two-layer fix:

1. **Server (`find_codex_conversations`)** — `effective_cwd` used to be `tail_worktree_path or cwd`, surfacing whatever path the JSONL tail extracted from an old `cd <…>` Bash command. If that worktree was since deleted, the row carried a non-existent path. Now picks the first cwd candidate that still exists on disk via the new `_first_existing_dir` helper (tail → cwd → pinned), falling back to the literal worktree path only when nothing exists.

2. **Client (`buildResumeCommand`)** — for missing cwds that don't match the `.claude/worktrees/<branch>` recreation pattern (e.g. ad-hoc `BYM-Finie-push-reschedule-sGH1nB`), `cd '/...' && resume` would fail (no such dir) and the `&&` would block the resume. Now falls back to `currentSession.repoPath` when known; drops the `cd` entirely (runs resume from the user's terminal pwd) when no repo path is available.
- Mobile single-column layout (conv list full-width → tap a row → conv pane slides in → back button returns to list) now triggers on every phone, including iPhone landscape. Root: the breakpoint was 768px (JS `_mobileMQ`) / 720px (back button CSS) / 768px (main-overlay CSS). iPhone Pro Max landscape is 932px and even baseline iPhone landscape is 844px — both exceeded all three thresholds, so `isMobile()` returned false and `mobileShowForCurrentMode` no-op'd. The user saw the desktop dual-pane layout cramped onto a phone with no back button. Aligned all three to 950px (covers up to iPhone Pro Max landscape with a small safety margin). The wiring (`selectConversation → mobileShowForCurrentMode → mobile-show-main → translateX(0)`, back button → `mobileShowMain(false)`) was already in place; this just opens it to the right viewports.
- Mobile conversations keep a visible back-to-list button inside the conversation pane.
- Mobile conversations keep one header while preserving the back-to-list button.
- On mobile, the page now lands on the conv list instead of the auto-restored conv pane. Root: `restoreLastConversation` runs at boot and calls `selectConversation` for whichever conv was open last, which in turn triggers `mobileShowForCurrentMode` and slides the conv pane over the sidebar. The user therefore landed on a conv overlay every page load and never saw the list — opposite of the standard phone pattern. Fix: when the restore loop completes on a mobile viewport, call `mobileShowMain(false)` to slide the conv pane back off-screen. The conv stays loaded so tapping a row brings it back instantly with no fetch latency.
- Three mobile toolbar fixes in one ship:

1. **Reverted the temporary blue Annotate button** — it had served its purpose as a cache-bust probe and the user confirmed new CSS reaches the browser.

2. **Topbar now fits one row on phones** instead of wrapping into a second row that pushed the back button off-screen. Hidden at `max-width: 950px`: Update pill, Report-a-bug, Annotate / Screen / Notes, Worktrees, Stats, Terminal, Vercel / localhost deploy pills, and the status-rail position toggle. None of these are phone-friendly anyway. The breadcrumb now flexes to fill remaining space; its category chip caps at 96px so the conv title gets visible room.

3. **Right status rail (Original ask / Activity / Files) defaults to collapsed on mobile.** The mobile viewport doesn't have the spare width to host the rail, and surfacing it pinches the conv reader into a narrow column. Boot-time check in `index.html` adds `status-rail-collapsed` when the viewport is ≤950px UNLESS the user has explicitly opened it (localStorage = '0'); the desktop default behavior is unchanged.
- Organize now respects hand-placed nested objects. Previous R10 implementation only anchored the chain ROOT at its current position — every nested cluster was placed at a chain-derived offset (`ancestor.right + NESTED_GAP_X, ancestor.top`), so a nested object you'd dragged anywhere different would snap back to "right of ancestor" on every Organize run. New behavior: every cluster (root AND nested) anchors at its own parent's current `offsetLeft/offsetTop`. The overlap resolver runs over all clusters as independent units instead of as chain bounding boxes, so a nested object can stay where you put it while its repo and sibling repos stay where THEY were too. Unplaced clusters still fall back to sensible seeds — bin-pack cursor for top-level, "right of ancestor" for nested — so first-ever Organize still tidies a fresh board.
- Inline session rename no longer gets stuck in edit mode when the sidebar search box is open (or focused). Root: the rename input is itself a text input, and after Enter/blur focus is either still on it or has moved to the search box (also text) — `shouldPauseSidebarRender` returns true for either, so the post-commit `renderSidebar` call early-returned and the rename input was never swapped back for the rendered title. Same class of bug as the "Sending… pill" fix shipped earlier. Fix: the rename commit's `renderSidebar` call now passes `{ force: true }` to bypass the periodic-pause guard for user-initiated paints. The save still happens (the API call ran), it just wasn't visible.
- Sidebar search now hides active and archived group-chat rows so search results stay focused on matching sessions/issues instead of showing group-chat navigation rows in In progress or Archived.
- Command tool results now attach to the matching command and show a visible result/error label.
- User-typed messages no longer disappear from the conv view when `cleanIssuePrompt` over-strips them. Root: the conv reader runs the JSONL user_text through `cleanIssuePrompt` (which removes spawn-prompt boilerplate, session-state instructions, slash-command markup, etc.) before rendering. If the cleanup eats the entire body — possible when a regex matches too broadly or the user's prose happens to look like template plumbing — the user_text div rendered with just a "User" label and no content. Combined with the pending-echo dedupe (which removes the optimistic stub the moment a matching JSONL event arrives), the user saw their sent message silently disappear. Safety net: when `cleanIssuePrompt` returns empty but the raw `ev.text` had content, fall back to the raw text. The user's typed words never disappear from their own conv view.

## [4.8.0] - 2026-06-05

### Added
- Conversation header now carries a pop-out button (next to the size badge) for users who would rather click than drag. Tap it and the active pane's conversation opens in its own window — native CCC window when running inside the macOS app, browser popup otherwise — reusing the same `openConversationPopout` helper that already powers the drag-out-of-window gesture. The button is delegated at the document level so it survives every breadcrumb rewrite, and it's hidden inside the pop-out itself (no point popping a popout). Keyboard accessible with a visible focus ring.
- Added automatic Cursor IDE integration for all CCC-spawned and resumed Cursor CLI/agent sessions. Spawned sessions are registered in both the Cursor IDE's local workspace SQLite database (under the `composer.composerData` key) and the global storage database (under the `composer.composerHeaders` key) on macOS, Windows, and Linux, making them appear natively in the Cursor IDE's sidebar and composer history.
- The context-% badge on each conversation row is now a one-click shortcut to `/compact`. Click the percentage (e.g. "82%") and CCC asks "Context is at 82%. Compact <session title> now? (Sends /compact to the session.)" — confirm and `/compact` is injected via the existing `/api/inject-input` path. Same toast feedback as any other inject: "/compact sent" on success, "/compact failed: …" on error. The badge gets a `cursor: pointer` and a faint hover lift so it reads as an action target; keyboard-accessible via `role="button"` + Enter/Space. Clicking the badge does not also open the conversation — it's excluded from the row-click handler.

### Changed
- "Add to UX fixes queue" in the annotation editor now closes the modal immediately on click instead of leaving it open while the API call is in flight. The annotation is already persisted via `persistAnnotation` upstream — closing early is safe — and feedback (success or failure) arrives as a toast a moment later. Failure toasts surface the underlying error message ("UX fixes queue failed: …") so the user knows whether to retry. Same treatment for the in-page editor and the screen-capture editor.
- The "Session is asking a question" surface is no longer a body-level modal overlay — it now renders as an inline card mounted inside the active conversation view, pinned to the bottom via `position: sticky`. Inheriting the conv pane's font stack means the question header, prompt, options, and "Other / type your own" input all match the surrounding conversation typography instead of the previous modal-specific font. Same answer flow (single-pick / multi-pick / free-text → POST `/api/answer-question`) and the "Answer in terminal" escape hatch are preserved; only the surface changed. The card tears down the same way the modal did — on successful submit, when the session stops asking, or when the user navigates to a different conversation.

### Fixed
- Scrolling down in a conversation no longer fails to make progress while the "Earlier ask" top box blinks. Root: the conv view's pin-to-bottom MutationObserver watched `childList + subtree + characterData` on the entire view — which includes the in-view `.conv-sticky-header`. Every scroll tick, the dynamic-ask tracker rewrites the earlier-ask slot's text to mirror whichever user message is just above the threshold; the observer treated that text rewrite as new content and, when the user was anywhere near the bottom, called `scrollConversationToEnd(view)` — instantly snapping the scroll back. The visible symptom: scroll input was consumed but the position didn't move, and the top box flashed text as the tracker fought the scroll. Fix: filter mutations whose target lives inside `.conv-sticky-header` before deciding to auto-scroll; only actual conversation-content mutations re-trigger the pin-to-bottom behavior.
- Cursor conversation events no longer all show "just now". Root: cursor transcripts (`~/.cursor/projects/<slug>/agent-transcripts/<sid>/<sid>.jsonl`) record only `role` + `message` per line — no per-event timestamp — so `_parse_cursor_event` returned `ts=""` for every row, and the browser's render path fell back to `nowStamp()` (the current wall-clock time). Every event therefore claimed it had just happened, regardless of whether it was 5 minutes or 8 hours old. Since cursor's transcripts are streamed append-only, we approximate per-event time by linearly interpolating between the JSONL's birthtime (first event) and mtime (last event). Distinct, monotonic, honest-about-being-approximate — better than fabricated "now"s. Falls back to mtime-everywhere when birthtime isn't available (Linux without `st_birthtime`).
- Closing a conversation pop-out window (or the main window momentarily) in the macOS app no longer terminates the entire app and tears down every other window plus the bundled server. Root: `AppDelegate.applicationShouldTerminateAfterLastWindowClosed` was hard-coded to `true`, so any "zero visible windows" moment — closing a popout when the main was minimized or in a transient zero-window state — quit CCC. Changed to `false`, matching standard full-GUI-app behavior (Safari, Mail, etc.): closing windows leaves the app running, Cmd+Q is the explicit quit path. Added `applicationShouldHandleReopen` so a dock-click re-shows the main window when the user has closed everything. DMG users get this only via a Sparkle release (per docs/RELEASING.md — `scripts/macapp/` changes need a versioned DMG); curl / brew users get the JS/CSS side automatically on next `./run.sh`.
- Fixed an issue where some Cursor agent sessions (including manually discovered ones, and ones spawned in directories not yet opened in the IDE) were missing from the Cursor IDE sidebar. The system now automatically creates missing workspace storage folders and backfills all discovered transcripts.
- Repo paths containing `+` (e.g. `/Users/.../BYM+Finie`) no longer 400 from every API endpoint that takes `repo_path`. Root: `+` decodes to a space in a URL query string, so `/api/sessions?repo_path=/Users/.../BYM+Finie` arrived at the server as `/Users/.../BYM Finie`, which matched no real directory and bounced. Fix: `resolve_repo_path()` now treats the as-given path as the primary attempt; if it doesn't resolve, the helper tries `+`/space swap variants of the input and accepts exactly one match against the known-repos registry or a real on-disk repo. Ambiguous matches (multiple variants exist) raise an explicit error instead of guessing. Every endpoint that takes `repo_path` funnels through this validator, so one fix covers them all. Test: `test_repo_path_with_plus_resolves_when_query_decoded_to_space` creates `BYM+Finie/.git` and asserts both the exact and the `+→space` mangled form resolve.
- The "● Sending…" pill in the sidebar conversation row now lights up the instant the user hits Send instead of waiting up to a poller cycle. Root: `renderSidebar` early-returned whenever focus was in a textarea — a guard meant to keep background pollers from yanking the list around while the user types in the conv input or search box. But hitting Enter to send a message also leaves focus in the textarea, so `markSessionSending`'s `renderSidebar()` call was suppressed by the same guard. The sidebar then only refreshed on the next 5s `liveStatus` tick or later. Fix: `renderSidebar` accepts a `{force: true}` option that bypasses the periodic-pause guard (rename and drag guards are still respected); `markSessionSending` and `clearSessionSending` use it so user-initiated state changes paint synchronously.
- `GET /api/sessions?repo_path=<heavy-repo>` no longer times out on repos with many sessions whose recorded `cwd` no longer exists (deleted worktrees, moved checkouts). Root cause: `_relocate_missing_session_cwd` did a full `os.walk` (up to 8000 dirs per root, ~4 roots) every time a session's recorded cwd was missing, and the cache lived only in memory — so every server restart re-paid the full cost. On a worktree-heavy repo (BYM+Finie, 128 missing cwds) this added up to ~40s per cold scan, beyond curl's 15s default and beyond any reasonable UI patience. Fixes: (1) persist `_session_cwd_relocation_cache` to disk (`~/.claude/command-center/cwd-relocation-cache.json`, schema-versioned, lazily revalidated on read so a restored worktree gets re-resolved); (2) per-request time budget for relocation walks (env: `CCC_CWD_RELOCATION_BUDGET_S`, default 1.5s) that short-circuits remaining missing-cwd resolutions to None for the rest of the request — uncached so the next call can resume; (3) per-root visit cap dialed from 8000 → 2000 (env: `CCC_CWD_RELOCATION_VISIT_CAP`). Result on BYM+Finie: 49s → 7.3s on the truest-cold first scan, then 1.6s on every subsequent scan as the cache fills. Test: `test_find_conversations_honors_relocation_budget` seeds 200 sessions all pointing at a deleted cwd, sets the budget to 0.5s, and asserts the scan returns within 3s.

## [4.7.0] - 2026-06-03

### Added
- Annotation screenshots forwarded through `/api/annotations/ux-fixes-queue` now render inline in the conv view instead of appearing as a bare absolute path. Two changes: (1) `/api/pasted-image` sandbox extended to also allow files under `~/.claude/command-center/annotation-screenshots/`, and (2) `linkifyPastedImages` gained an `ANNOTATION_IMG_RE` regex that rewrites any matching path into an `<img class="msg-image">` pointing back at the same endpoint. Keeps the lazy-load and cache headers the existing pasted-image path already had.
- "Add to UX fixes queue" in the annotation editor now fires an immediate "Sending annotation to UX fixes queue…" toast at click time, so you don't have to wait for the network round-trip to see that something happened. The existing success/error toast still follows once the request lands.
- Implement Escape/interrupt capability for Codex sessions. Clicking the Esc button in the composer bar or pressing the physical Escape key on the keyboard sends `SIGINT` (Ctrl+C) to terminate the running Codex process (which does not natively handle Escape keystrokes), supporting both headless spawns and terminal window runs.
- Fixed a liveness detection bug where spawned Codex and Gemini sessions were not correctly identified as live by the backend, which hid the "ESC" button in the dashboard's input composer.
- Added a Codex steer action in the composer. Normal Send queues behind a running Codex turn; the steer button immediately pushes input into that active turn through Codex app-server `turn/steer` when supported.
- CCC now snapshots a session's JSONL to `~/.claude/command-center/compact-backups/<sid>-<timestamp>.jsonl` whenever a `/compact` command is detected in the inject path — before Claude Code rewrites the on-disk transcript. Claude Code's `/compact` deletes the original message history (replaces with the compacted summary), and users only discover this after the fact when scrolling back finds the pre-compact messages gone. Keeps the 10 most-recent backups per session; older ones rotate out. The in-progress banner now mentions the backup location so users know recovery is available.
- /compact now has clear in-progress and result UI. (1) When the user sends `/compact`, a sticky banner appears in the conversation view — "Compacting conversation context… Claude is summarizing the prior turns. This usually takes 1-3 minutes." with a spinner, so the user knows the request is still running during the 1-3 minute wait. (2) When the compact-resume block lands (the long "This session is being continued from a previous conversation…" user_text), it's now wrapped in a collapsible card with a click-to-expand toggle. Intro line is always visible; full body (numbered sections) is hidden by default and scrollable when expanded, so the active conversation isn't drowned by it.
- Conversation pane header now shows the JSONL transcript size next to the title (e.g. `5.2 MB`). Helps correlate slow first-paint with sheer conversation size — large sessions take noticeably longer to parse and render, and the badge makes that visible. Pulled directly from the row's `size` field; cleared when the row has no known size (issue/PR rows, new-session mode). The conv list rows already show size in their inline meta strip; this surfaces the same data in the place the user is staring at while the conversation loads.
- Conversation view now follows new content automatically when you were already at the bottom — any path that adds content (SSE events, streaming bubble, re-render, etc.) re-scrolls to the new bottom. If you scroll up to read earlier text, the auto-scroll stops; scrolling back to the bottom re-enables it. The clickable "End" button still works as the manual jump. Implemented via a per-view MutationObserver tied to a `_pinnedToBottom` flag that scroll events keep in sync.
- Added Cursor IDE agent view visibility integration. Spawned and resumed Cursor CLI sessions now automatically appear in the Cursor IDE agent view by writing workspace metadata databases directly under `~/.cursor/chats/<project_md5>/<session_id>/store.db`, with a background backfill scan at server startup to ensure existing recent sessions are also registered.
- Added Cursor as a dashboard engine alongside Claude, Codex, and Antigravity. CCC can discover Cursor agent transcripts, spawn headless `cursor-agent` runs, resume existing Cursor chats, show Cursor rows/logs in the UI, and manage Cursor spawn defaults.
- Added `scripts/cut-release.sh` — a single command that cuts a full release (changelog rollup, version bump, tag, GitHub release, notarized DMG + Sparkle appcast, and Homebrew formula bump with auto-computed sha256), with `--dry-run`, `--skip-dmg`, and `--notes-file` options.
- Flow board now remembers whether you had it expanded (full-screen) and restores that state on the next page load. Persisted via `localStorage['ccc-flow-expanded']`. The toggle (chevron in the flow toolbar) writes the new state on every click; reload picks it up at init time and applies it as soon as the flow view renders.
- New "Organize" button in the flow toolbar. Keeps every parent (repo + object) exactly where it is, then lays out each parent's session children in a tidy grid right under it — most recent session leftmost, descending recency rightward and downward. Useful after a drag-and-drop session that left children scattered.
- Flow board gains a per-session pin that's independent of the conv-list pin. Hover any session card in flow view and a 📌 button appears next to the archive icon — click to pin that session to the flow board. Pinned sessions stay visible in flow even when archived and "Include archived" is off. Stored client-side at `localStorage['ccc-flow-pinned-sessions']` so the pin survives reloads but doesn't leak into the regular conversation list.
- Flow session cards now carry a compact chip strip mirroring the regular conv list: a single lifecycle chip (uncommitted | PR # / state | pushed | committed | no edits — priority chain identical to the list), a live activity chip when the agent is mid-tool (yellow pulsing "WIP" or the tool name) or paused waiting on input (cyan "WAITING"), and a numeric "N commits" chip when the server exposes a commit count. Also bumps inter-session spacing inside a cluster (CHILD_GAP_X 14→18, CHILD_GAP_Y 10→16, SESSION_GAP_BELOW_PARENT 14→18) so chips have room to breathe.
- Flow toolbar now has its own "Annotate" button. The global topbar (where Annotate normally lives) is hidden in expanded flow view, so previously you had to collapse the flow board just to grab a note. The new button delegates to the existing `#annotationStartBtn` click handler — same behavior, just reachable from the flow toolbar.
- Group chat input now autocompletes `@` mentions against the chat's participants. Type `@` and a floating menu shows matching participant names (filter by display-name or 8-char short-id, max 8 results). Arrow keys to navigate, Enter / Tab to commit, click to pick, Esc to dismiss. Inserts `@<full-name> ` so the nudge mention-scan picks it up cleanly. Participant list comes from the reader's live `name_map` so newly-joined sessions appear without a reload.
- Group-chat messages now align chat-style: each participant stays on its own side of the available width so the conversation reads like a thread, not a flat log. Human always lands on the right (iMessage convention for "me", green accent). Agents cycle through three stable slots — left (accent), center (purple), right-agent (orange) — based on first-seen order so each agent keeps its own column and color throughout the chat. System messages stretch full-width so the lifecycle log isn't squeezed into one side. Per-side header tinting makes each participant's column distinguishable at a glance.
- Sessions spawned by agents via `/api/sessions/spawn*` (Codex, Gemini, Cursor, Antigravity, etc.) now appear in the conv list within seconds — no waiting for a manual refresh. `/api/session-status` returns `spawn_registry_count` (running count of CCC-spawned sessions); the client's 5s `refreshLiveStatus` poll compares it against the last seen value, and any growth triggers an immediate `refreshConversationList` plus tighter follow-up polls at 1.2s / 3s to catch sessions whose JSONL materializes a moment later.
- Added `/lean-commit` slash command, `scripts/lean-commit.sh`, and `CLAUDE.md` / `AGENTS.md` guidance for Tier-A path-only commits on shared `main` (parallel sessions).
- Orchestrator panel now surfaces the last-nudge timestamp inline on the "Auto-nudge: checks every Xs, nudges ≤ every Ys" row (e.g. "· **last: 2m ago**") plus the existing dedicated "Last nudge:" row gets an absolute-time tooltip on hover. Server: bumped `last_nudge_at` resolution to take `max(in_memory last_nudge, persisted last_reminder_at)` so the timestamp stays accurate across server restarts AND when the targeted per-participant Nudge button fires (which only writes to the sidecar, not to the in-memory watcher entry). The per-participant Nudge now also bumps both stores so it shows up in the panel immediately.
- Per-participant Nudge button in the orchestrator panel. Each participant card under "Participants" now has a small Nudge action that re-injects the /group-chat prompt into just that one agent's session — wakes a specific participant up without nudging the whole group. Useful when one agent has gone quiet (e.g. "spoken 3h ago, last mentioned 2m ago"). Powered by extending `_group_chat_nudge(path, chat_uuid, target_sid="")` with a `target_sid` parameter that bypasses the auto-select-last-writer logic and pings exactly that sid; the `/api/group-chat/nudge` route now accepts `target_sid` in the payload.
- Pasting an image into any composer now shows an instant thumbnail preview above the input — using a browser-side blob URL so the preview appears the moment you paste, before the upload finishes. Once the server returns the canonical path, the pending thumbnail swaps to a final version with a hover "×" remove button that strips the path token from the input value (so the send doesn't carry an orphan reference). Wired across the standard conv input, the split-pane input, the new-session modal, and the group-chat reader input. Thumbnails clear automatically when the input goes empty post-send.
- Added real-time subscription plan limits and usage popover (5-hour limit, weekly models limit, sonnet only limit, and extra usage USD credits) retrieved dynamically from Anthropic, displayed when clicking the context usage pill at the bottom of the composer.
- Answer AskUserQuestion from the dashboard. Headless sessions used to auto-decline questions instantly; now a blocking modal surfaces the question (and its options) in the UI and feeds your pick back to the agent so it continues with your answer.
- Conversation list: each repo folder header now shows the repo's git status and a **Push all** control that orchestrates a repo-wide ship for the main checkout, with a live terminal-style log feed in the "Needs your attention" panel. The flow: auto-restores dirt whose content is already preserved in git (hand-copied PR work / behind-origin files — provably non-destructive), flags junk, nudges the sessions that own remaining changes and reads their replies back (two-way), then safely integrates by fetch + fast-forward only — refusing to rebase a shared clone in place when the branch has diverged. It never auto-commits unattributed work (there's a push-to-prod behind it); instead it surfaces the remainder split into "safe to bulk-commit (docs/infra)" vs "review (app/deploy code)" vs "junk", each offered as an **Approve / Reject** action (approve commits the infra group or deletes the junk; review stays manual). The log feed persists across page refresh and restart (click the status chip to reopen it), sessions are shown by name, and repeated runs skip sessions that already replied. For Vercel repos it then polls the production deploy to READY.
- CCC now renders right-to-left scripts (Hebrew, Arabic, etc.) correctly throughout the conversation view. Each message block detects its own direction from its first strong directional character via `unicode-bidi: plaintext` + `text-align: start`, so mixed-language conversations don't need any per-message tagging — Hebrew quotes flip RTL while interleaved English text stays LTR. Code blocks and inline `<code>` are pinned back to LTR so code stays readable. The composer textarea also got `dir="auto"` so typed Hebrew/Arabic flows the right way.
- Subagent (Task tool) work now lives in its own tab inside the conversation pane instead of mixing into the master agent's stream. New `.conv-tab-strip` appears above the conversation view as soon as a subagent starts streaming; "Master" tab is the parent agent's flow, one tab per `parent_tool_use_id` for each Task delegate (label taken from the Task description). Tabs auto-close 30s after their subagent completes IF the user is on another tab when the result lands — finished work clears the strip without piling up, but stays visible if you're currently reading it. Manually closable via the × on each task tab. Switching conversations resets all tabs.
- What's New carousel now highlights the four headline items shipped today: Subagent Tabs (visualization), Headless Question Relay (orchestration), Antigravity Orphan Resume (engine), and Ship Auto-Reconcile (workflow). Each entry has a description and a styled inline mockup so users opening the carousel on next launch see what's new without leaving the dashboard.

### Changed
- "Active Group chat" pill now reflects REAL recency, not just "in the watcher backlog." Previously it showed for the full 45-minute server-side death timeout even when no trigger or file activity had landed in tens of minutes. New behavior: pill hides if no trigger / `last_nudge_at` / `last_activity` / `last_mtime` has happened within the last 10 minutes (separate `GC_ACTIVE_PILL_FRESHNESS_MS` constant, decoupled from the server's nudge-keep-alive window). Label and tooltip also annotate the recency ("Active Group chat · 3m ago" plus an absolute-time tooltip) so the pill itself answers "still active?" at a glance.
- Active group-chat pill now shows what actually triggered the activity, not just "N Active Group chats". Format: `<topic> · <reason> · <age>` (e.g. `marketing · nudged Alice, Bob · just now` or `2 chats — marketing · auto-pinged Alice +3 · 2m ago`). Picks the freshest of `orchestrator_last_nudge_at` / `orchestrator_last_trigger_at` / `last_activity` and labels with the matching reason and target names. Targets truncated at 3 to keep the pill readable. Annotation: "i doubt there is really something triggering right now — if so i want to know what it was — put it on the UI even if the pill becomes long."
- Clearer error when in-page tab capture isn't available (CCC native app shell). The previous message said only "use Screen Recording for the CCC server instead" — which left users staring at the System Settings without knowing what to click in CCC. New message points directly at the topbar "Screen" button as the working alternative and explains it uses the macOS area screenshot picker (gated by Screen Recording permission for Claude Command Center).
- Throttle all-repos archive refreshes and coalesce session scans so large local histories do not starve conversation loading.
- Active conversation breadcrumb (`claude · claude-command-center · UX fixes queue · 15.0 MB`) now lives in the global toolbar at the top row, freeing the slot below the toolbar for the sticky "original ask" panel (the "Fix the following UX issue based on this annotation: …" header). Single-pane mode: the in-pane header is CSS-hidden so the sticky panel rises into its space; the topbar mirrors the active row's category + title + size badge. Split mode: each pane still shows its own in-pane header (so you can tell panes apart) AND the topbar reflects whichever pane is active.
- Improved conversation composer typing responsiveness in the Mac app: draft saves to localStorage are debounced instead of running on every keystroke, slash-command handling no longer touches split-pane localStorage unless a `/` command is active, draft storage keys are cached per conversation, and live transcript/usage updates are deferred while the composer is focused (flushed on blur).
- In Progress list reshuffles repo groups much less aggressively. Bumped `_FOLDER_ORDER_HYSTERESIS_S` from 5 min to 60 min: when two repo groups' max-modified timestamps are within an hour of each other, the previous render order is kept regardless of which one most recently received activity. Repos dormant for more than an hour can still be promoted by fresh activity (the user's "OK to bring up from below the fold" exception). Reduces the jitter the user reported when actively cross-working on two repos.
- Cmd+F find modal is now purple — temporary visual marker so we can tell the new build is loaded while iterating on the focus-loss fix. Will revert once focus is confirmed solved.
- Dragging the empty flow-board background now pans the canvas (matches Figma/Miro). Hold Alt while dragging the background to fall back to the previous behavior — rectangular area-select. Node drag is unchanged; only the background gesture is rerouted.
- Dragging a repo or object node in the flow board now moves all of its descendants (sessions, drafts, nested objects) along with it by default — the cluster travels as one. Hold Alt while dragging to break that link and move only the parent node, leaving children in place (useful when you want to reparent or reorganize manually).
- Clicking a session card in the flow board now jumps straight to the conversation view: collapses the flow board out of full-screen and closes the conversation sidebar so the chat takes the full viewport. One click does the navigation the user previously had to do in three steps (click session → exit flow → close sidebar). Restoring the sidebar afterwards uses the existing chevron / Cmd+B.
- Flow toolbar is now grouped by topic with vertical dividers between groups (Add | Filter | Layout | Zoom | View) and the Annotate button moved to the rightmost position behind a flex spacer. Adds a new "Include archived" filter toggle that pulls completed/archived sessions back into the flow with a clear marker — translucent + green check badge in the top-right of each archived session card — so finished work is visible at a glance but doesn't compete with active sessions. Toggle state persists across reloads via `localStorage['ccc-flow-include-archived']`.
- Two group-chat fixes. (1) Cards now flush to the transcript edges — `margin-left: 0` for left-side speakers, `margin-right: 0` for right-side speakers (Human + agents on right slot), no more 6% inset. Max-width down to 72% so adjacent columns separate clearly. Agent assignment simplified to alternate left/right by first-seen order; per-agent distinct color (accent / purple / orange / cyan / red / yellow) lives on a separate `data-speaker-color` attribute so multiple agents on the same side stay distinguishable. (2) Hard-refreshing while reading a group chat now reopens the same chat — `openGroupChatReader` stamps a unified `ccc-last-view = {type: 'gc', path, id, topic, mode}` marker, and on boot if the last view was a group chat we call `openGroupChatReader` instead of `restoreLastConversation`. Conv selection writes the same marker with `type: 'conv'`, so whichever was most recent wins.
- Group chat @mentions truncate participant names over 20 chars (e.g. `@Movie i wanted for vid…`) instead of dumping the full label inline. The bare prefix in the system note still spells out the full name once, so the @mention pill no longer adds a redundant copy of the same long string. Full name + session id stay in the title tooltip.
- Group-chat messages now render as clearly-bounded cards: each `.gc-message` has a 1px border + 8px radius, the `.gc-message-meta` is a full-width tinted header bar with the speaker name + timestamp, and the body sits below with comfortable padding. Makes the separator between consecutive messages obvious at a glance. System messages get a quieter grey-tinted header so the lifecycle log doesn't compete visually with actual participant posts.
- Clicking "+ New Group chat" now prompts for the chat name up front instead of silently creating an "empty chat" you had to click ✏️ to rename afterwards. Cancel aborts (no chat created); pressing Enter with nothing typed falls back to "empty chat" so users who don't want to name it can still ship.
- Group-chat nudge mention-scan is now engine-agnostic: agent posts can also address specific participants ("@Maya let's verify that") and the nudge will wake only those addressees, not everyone. Previously the @mention scan ran only when the Human was the last author; agent messages fell through to the legacy "ping everyone except the writer" behavior even when they explicitly named someone. Now the scan runs on whichever author wrote last (Human or agent), and the writer themselves is always excluded from the nudge set (no self-mentions).
- Two group-chat nudge improvements. (1) When the Human's last message addresses multiple participants — by `@<name>` mention or `@<8hex>` short-id — ALL of them get nudged, not just the most recent prior writer. Previously a "Maya and Jordan, can you both…" message only woke whichever agent wrote immediately before the Human; now it wakes everyone the Human named. Falls back to the prior-writer single target only when no mentions are detected. (2) The nudge injection no longer inlines the last message body (~2KB) — agents re-read the chat file anyway, so that was pure token waste at every nudge. The injection now sends just the heading line ("a new post just landed — `## <ts> — <author>`") as a pointer; agents read the chat file for content.
- install.sh now defaults to **yes** on the "install as background service?" prompt (was no). Most users hit enter on prompts, and the previous default left them with a foreground server tied to the install Terminal — closing that window killed CCC and produced a frequent "where did CCC go" confusion for DMG users. Users who explicitly want the foreground path can still type `n`. Non-interactive runs (no TTY) stay in foreground, unchanged.
- DEBUG: globally pauses every `setInterval` callback while a textarea or text input is focused, so we can confirm whether timer-driven work is the source of remaining typing hitches. SSE, `requestAnimationFrame`, and one-shot `setTimeout`s are not affected.
- Organize now treats each parent + its descendants as a single cluster zone — no other parent or session may land inside that rectangle. Replaces per-node overlap avoidance with per-cluster exclusion zones (parent's bounding box expanded with its placed children + a 12px pad). When the next parent would land inside a prior cluster's rectangle, the whole parent is pushed down until it clears the zone; same for any session that would otherwise fall inside someone else's cluster.
- Organize now condenses: any repo/object node sitting outside the currently visible flow viewport gets brought back inside it, while parents already in-frame stay exactly where they are. Out-of-frame parents land in the first empty slot scanned row-by-row inside the viewport (with a session-row reservation below each so out-of-frame parents don't drop on top of an in-frame parent's session grid). After the parent pass the existing session sort-and-grid step runs, so everything ends up tidy under each parent regardless of how scattered the board was.
- Organize now guarantees zero overlaps. Replaces the old fixed-grid pass with a collision-aware placement: parents are processed top-to-bottom, pushed strictly downward until they clear every previously placed rect, then each parent's sessions are placed in a 4-column grid below, with grid cells that would overlap an occupied rect (a sibling parent, its sessions) skipped — sessions wrap to a 2nd, 3rd, Nth row as needed. Falls back to a vertical stack at the bottom of all occupied rects when the grid truly can't fit. If a parent has to move to keep things clean, the whole object moves.
- Organize gains four more rules (R5-R8) and now keeps the full rule set documented in the source above `organizeFlowSessions`: (R5) sessions placed below their parent are indented 10px to the right; (R6) connector lines always render BEHIND nodes — archived sessions switched from `opacity: 0.62` to muted background/text colors so lines no longer bleed through; (R7) nested objects (an object whose parent is another object/repo) are pinned to the right of their parent (`parent.right + 24, parent.top`) — never above or to the left, enforced via topological sort; (R8) within a cluster, non-archived sessions come before archived ones; within each group, reverse-chronological order.
- Organize rewritten as a deterministic bin-pack: clusters (parent + sessions) are precomputed with an area-minimizing column count (chosen so each cluster's width ≈ its session-area height), then packed top-left to bottom-right in rows with a 20px margin between clusters. Each row wraps to the next when the next cluster would exceed the row budget. Results: (1) idempotent — running Organize twice produces the exact same layout; (2) minimum cluster area for the given session count; (3) consistent 20px margins around every cluster; (4) tight pack with no isolated rectangles or empty space between neighbors.
- Push all now auto-reconciles a diverged branch in an isolated throwaway worktree (cherry-pick local commits onto origin, push, fast-forward the shared clone) and only hands off to a manual reconcile when there's a real conflict; loose root-level scratch files (e.g. snapshot.png) are no longer misclassified as app/deploy review.
- Push all now attributes uncommitted files to their authoring session via the local conversation index (claude-index), showing the owner+age on each handoff action and reducing over-nudging; git remains the safety authority.
- Subagent (Task tool) streaming bubbles now render visually distinct from the parent's bubbles — purple "subagent" label, left-indent + colored left border — so a busy Task-tool run no longer reads as one frantic stream-of-consciousness coming from the parent. Server: `_normalize_spawn_event` passes `parent_tool_use_id` through to the assistant_block payload (Claude Code stream-json sets it on subagent messages). Client: `ensureStreamingBubble` accepts `parentToolUseId`, tags the bubble with `stream-bubble-subagent` + a `data-parent-tool-use-id`, swaps the header label from "streaming" to "subagent". CSS variant indents 16px and switches the border/dot tint to purple.
- Telemetry opt-in banner: punchier copy that names the 5 fields up front ("version, OS, engines you have") and the explicit-not list ("Zero paths, prompts, or content"). Primary button is now "Send daily ping" instead of "Enable" so the action is obvious. Aims to lift the dismal opt-in conversion (1-of-7 DMG downloads since 2026-05-22).
- TTS button (read-message-aloud) simplified to fixed 1.25x rate with play/pause toggle. Clicks cycle play → pause → play; the rate-cycling (1.0x → 1.5x → stop) is gone. The button auto-resets when a new assistant turn lands, so the next click reads the freshly-arrived message instead of resuming the prior one mid-sentence. Paused state shows a calmer tinted button (so it reads as "click to resume" instead of "still speaking").

### Fixed
- Conversation rows now show "now" (or the freshest sidecar timestamp) when the agent is actively running — was showing "11h" for sessions actively mid-tool because the row time was anchored to `last_interacted` (the user's last UI action) and not the agent's autonomous activity. The new clock honors `sidecar_ts` when `_isAgentRunning` is true, falling back to literal "now" if no sidecar stamp is present.
- Annotate screenshots: captures the annotated DOM region in-browser (no tab prompt required), falls back to tab capture or macOS window capture, and always attempts server capture when pixels are still missing. Saves without a screenshot show a clear warning instead of failing silently.
- When an annotation creates a NEW session (UX-fixes-queue first-spawn path), the conv sidebar now refreshes immediately so the new row appears without waiting for the next poll cycle. `annOpenUxFixesQueue` previously refreshed only the archive list on success; if the server reported `action: 'spawned'`, the just-created session was invisible in the In Progress list until the next refreshConversationList tick (manual click or some other code path). Now it calls `refreshConversationList()` immediately + schedules tighter polls at 600/1500/3000ms to catch sessions that materialize just after the first refresh window.
- CCC now recognizes `.db` as a valid AGY CLI conversation state file (was checking only `.pb`). AGY writes a `.db` to `~/.gemini/antigravity-cli/conversations/<sid>.db` when it rebuilds an orphan conversation from the brain transcript on the first `--conversation <sid>` resume. Without `.db` recognition, sessions that had been resumed once stayed labeled "orphan" in the UI placeholder/tooltip even though their state was healthy on disk. The actual send path was unaffected (both routes through the same `agy --conversation -p` command), so this is a cosmetic/routing-preference fix — the placeholder now correctly says "Resume Antigravity headlessly and send..." once `.db` exists, instead of stuck on the orphan-rehydrate copy forever.
- Antigravity spawns no longer create a phantom second session row. The server was pre-writing a placeholder `transcript.jsonl` under `~/.gemini/antigravity-cli/brain/<sid>/` with our generated session_id before launching the CLI, but Antigravity CLI ignores the `--conversation <sid>` flag in this path and writes its own transcript with its own UUID — so both files showed up as separate conversations (the placeholder always with just the prompt and no replies). Drop the pre-write; let AGY CLI be the single source of truth for the transcript.
- Antigravity sessions now accept typed follow-up questions even when Antigravity isn't currently running. Previously the input bar was set to `readOnly` and the send button disabled whenever neither `can_headless_resume` nor `can_app_resume` was true — leaving the user with a dead input bar and no way to ask a follow-up without first opening Antigravity manually. The server's `resume_session_antigravity()` already falls back to opening the Antigravity app with the prompt when CLI-headless and app-resume aren't available, so the client restriction was over-cautious. Send button now reads "Send — opens Antigravity with this prompt" in that state; placeholder reads "Type a follow-up — Antigravity will open with this prompt…".
- Orphaned Antigravity sessions (transcript on disk, no CLI `.pb`, no app conversation file) can now be continued. `resume_session_antigravity` previously short-circuited to the broken app-resume path whenever the CLI `.pb` was missing, which then errored with `antigravity_app_conversation_missing` — the session was effectively read-only. The CLI print-mode code path (`agy --conversation <sid> -p <text>`) already existed for live CLI sessions; it now runs for orphans too, letting AGY rehydrate from the brain transcript and append the new turn headless (same mental model as Claude resume). App-resume is still preferred when the app conversation file exists. Input bar copy updated to "Antigravity will resume headless" / "Send — runs AGY headless on this session" (was "Antigravity will open with this prompt").
- Antigravity orphan sessions (no CLI .pb, no app conversation file) now show feedback when you hit send. Two send handlers (the main conv input bar and the split-pane composer) were early-returning with `$input.blur()` whenever `antigravityCanSend(session)` was false — which for orphans meant pressing Enter did literally nothing (no optimistic echo, no toast, no input clear). Drop the gates now that the server has a graceful headless-fallback path. The flag still drives placeholder/tooltip copy ("Resume Antigravity headlessly (orphan rehydrate)..."), it just no longer blocks the send pipeline.
- Antigravity sessions spawned by agents via `/api/sessions/spawn-antigravity` now appear in the conv list within seconds, matching the Codex/Gemini behavior. Previously the spawn-registry-count ping triggered a conv-list refresh, but the refresh found nothing because AGY's brain dir + `transcript.jsonl` take several seconds to materialize on disk. `find_antigravity_conversations` now synthesizes a stub row (display_name from spawn prompt, marked `pending_spawn`) for any live AGY spawn whose JSONL hasn't landed yet — the real row replaces it on the next scan once the transcript exists. Works for both client-initiated spawns and sibling agent spawns.
- CRITICAL: AskUserQuestion no longer auto-continues silently. The resume-queue watcher and the direct inject path now BOTH check `_pending_ask_user_question_for_session(sid)` before flushing any queued text into a live session — if a question is in flight, the input waits until the user actually answers in the UI. Previously the watcher only checked `_session_status_is_busy()` (status=busy/running), which returned False even while `sidecar_tool == "AskUserQuestion"` was pending; injecting queued text in that window made Claude Code synthesize a tool_result for the open question (treating the queued text as the user's "answer") and continue the conversation silently past the prompt.
- Fixed Cmd+F find bar losing the typing caret in the Mac app. In-conversation search no longer uses `window.find()` (which moves focus into the transcript in WebKit); matches are highlighted with the CSS Highlight API and scrolled into view while the caret stays in the find field.
- Fixed live indicator missing on codex/gemini/cursor session rows in the list. The bulk session list only checked Claude Code sidecar markers for liveness, so non-Claude engine sessions never showed the live glow even while actively working (the right-hand pane lit up but the row stayed dark). The list now also recognizes a session as live when its engine CLI is running it, via a single cached process scan shared across all rows.
- Flag stale Codex tool calls as stuck and add a one-click wake action.
- Fixed Codex steer/send replies disappearing from the conversation pane when the composer stayed focused. Stream batches were deferred during typing but the JSONL cursor still advanced, so steered answers never rendered.
- The `/api/sessions/live-activity` poll no longer pins a CPU core re-parsing whole transcripts. `_extract_codex_tail_meta` is mtime-cached, but a *live* session appends to its rollout constantly, so the cache always missed and the entire (often multi-MB) JSONL was re-read and `json.loads`-ed on every poll — for every live session, on every concurrent poll. A real 13 MB live rollout was being fully parsed several times a second. The parser now resumes from a saved byte offset (rollout JSONL is append-only) and reads only the lines appended since the last poll, folding them into the carried-forward parse state; truncation/rotation falls back to a full reparse, and a partially-written trailing line is left for the next poll. On a 12 MB rollout this drops a poll from ~131 ms to ~0.03 ms (~4000×). Resume state is in-memory only, so a restart costs one full parse per file and then rebuilds. (Gemini/Cursor/Antigravity tail extractors share the same pattern and remain a follow-up.)
- Context-pct badge no longer shows the pre-`/compact` percentage after a compact runs. Root cause: `_extract_tail_meta` captured `live_context_*` from `/context` slash command outputs and held them across the rest of the JSONL — even when a subsequent `compact_boundary` event rewrote the JSONL much smaller. The client preferred `live_context_percent` over the freshly-computed `latest_input_tokens` ratio, so the badge stayed pinned at the pre-compact value (e.g. 91%) when actual usage had dropped (e.g. 42%). Fix: zero out `live_context_*` on `compact_boundary`. Next `/context` invocation repopulates them with real post-compact numbers; until then, the badge falls back to the calc-from-tokens path which already reflects the compact. Bumped `_CONV_META_SCHEMA_VERSION` to 13 so persisted entries with stale live_context get re-extracted on next boot.
- The context-pct badge in the sidebar (e.g. "91%") no longer stays stale after a fresh assistant turn lands in the JSONL. `_extract_tail_meta` was keyed on `path.stat().st_mtime` — second resolution. Two writes inside the same wall second (common: a tool result plus the next assistant turn within ~100ms) collapsed to the same mtime and the cache kept returning the prior snapshot, so `latest_input_tokens` / `live_context_tokens` / `live_context_percent` stayed pinned on the old value until the next-second write evicted the entry. Cache key is now `(st_mtime_ns, st_size)` (same shape `_conv_parse_jsonl_mtime` already uses for the right-pane parser). Bumped `_CONV_META_SCHEMA_VERSION` to 12 so old persisted entries (mtime-only keyed) get re-extracted on first hit instead of perpetually missing the new check.
- Fixed missing WIP and in-progress tool chips (e.g. Bash command) on conversation list rows while a session is active. Live sidecar data was dropped when the sidebar re-rendered from cached archive data, and HTTP 304 archive refreshes never re-applied live fields — the list now polls `/api/sessions/live-activity` every 5s and merges that overlay into each row render.
- Conversation row time chip now reflects ALL activity on a session: user UI actions (`last_interacted`), CCC injects (touched optimistically via `markSessionSending`), AND agent JSONL writes (`modified` = mtime). Previously the chip read `last_interacted || modified` — a short-circuit OR that returned the user-action time even when the agent had been actively writing events seconds ago. Now we take `max(last_interacted, modified)` so the freshest signal wins. The "now" override when `_isAgentRunning` still applies on top.
- Sidebar conversation search (`#convSearch`) is responsive again. The RTL belt-and-suspenders MutationObserver was attached to `document.body` with `subtree: true`, so every sidebar re-render on each keystroke fired thousands of mutation records that the observer walked synchronously — starving the input thread and reading as "search stopped working". Re-scoped the observer to the conversation view containers (`#conversationsView`, `#p1ConvView`, `#p2ConvView`, `#p3ConvView`) where message HTML actually lives; sidebar mutations no longer trigger any RTL work.
- Sidebar conversation search no longer "stops working" until you click a conv-item. Root cause: the `_sidebarDragInProgress` boolean got stuck `true` whenever a dragstart fired but dragend/drop never did (mid-drag Escape, browser-cancelled drag, removed source element). With the flag stuck, every renderSidebar call was deferred — typed characters showed in the input but the list never filtered. Clicking an item happened to call a code path that cleared the flag, making search "work again" by coincidence. `isSidebarDragInProgress` now self-heals: if the boolean says we're dragging but no DOM element actually carries `.dragging`, the flag is cleared and renders resume.
- Conversations view no longer shows a spurious horizontal scrollbar at the bottom of the pane. Added `overflow-x: hidden` + `min-width: 0` to `.conversations-view` so long URLs / unbreakable tokens in a message wrap instead of pushing the pane wider than its container. Code blocks have their own inner horizontal scroll, so the clip doesn't lose any content.
- Cursor / Codex / Gemini / Antigravity rows no longer falsely light up with a Claude WIP "Shell" badge after a Claude Code hook pollutes their sidecar slot. Pattern observed: a Claude session ran `git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>"` and the hook wrote `~/.claude/command-center/live-state/<cursor_sid>.json` with `tool:"Shell", status:"active"` — keyed under the *Cursor* session id, not the Claude one. From then on `_archive_session_is_live(cursor_sid)` returned True (sidecar exists), the cursor row got `is_live: true`, and the WIP path lit up a stale "Shell" badge that survived forever (sidecar files persist until manually deleted). Defense: `_archive_session_is_live` now detects the engine first and skips the Claude-sidecar check for non-Claude sessions — their liveness comes only from the live-process scan. Root cause (which Claude hook wrote the cross-engine sidecar) is a separate hunt; this is the defensive layer.
- Fixed Cursor follow-ups that immediately fail (for example usage-limit errors) so they surface as send failures instead of leaving the conversation pane stuck on an optimistic "sending" echo. Non-live Cursor rows also stop carrying stale pending-tool state from the last transcript event.
- Live-activity polls and group-chat opens no longer trigger a full scan of the Gemini chat store per session. `_detect_session_engine` falls through to `_is_gemini_session`, which JSON-parses *every* Gemini chat file on disk to look for a matching `sessionId` — so for a Claude session (no match) it parses the whole store. With ~14 live sessions that was ~2.3s per `/api/sessions/live-activity` poll, and it also ran per-participant on every group-chat open (the "opening a group chat is slow" symptom). A session's engine is immutable, so the result is now memoised per session: a non-"claude" engine is definitive and cached forever; "claude" is cached with a 30s TTL so a just-spawned non-Claude session whose store appears a beat later is still re-checked. Engine detection drops from ~153ms (14 sessions) to ~0ms once warm; the full live-activity build drops ~206ms → ~62ms per poll.
- Cmd+F find modal really keeps focus on the input now — the prior synchronous `.focus()` restore beat WebKit's async focus move to the matched element. Defer the restore via microtask + requestAnimationFrame so we land AFTER WebKit's reflow-time focus shift instead of before it.
- Cmd+F find input now genuinely keeps focus while typing. The triple deferred restore (microtask + rAF + 60ms) still lost to WebKit's deferred focus shift in some cases. Added a 300ms `focusin` guard window after every `doFind`: any focus move off the input while the find modal is open snaps right back. Belt-and-suspenders with the existing restore chain.
- Add a `setTimeout(60)` fallback to the Cmd+F find input's focus-restore chain — microtask + requestAnimationFrame from `8a85660` weren't late enough in WebKit; focus shifted to the matched element on a later commit-phase tick. Triple-restore (microtask, rAF, 60ms) catches whichever frame the focus move lands on. Each restore no-ops if focus is already on the input.
- Clicking a session from the flow board now collapses the RIGHT status rail (the panel with the original ask, session activity, and files container) — not the left conv-list sidebar (which the prior two attempts incorrectly targeted via `setConvPanelOpen` and then `sidebar-tucked`). Reverts the sidebar-tucked plumbing (floating button + body class + index.html button), adds `body.status-rail-collapsed` + `localStorage['ccc-status-rail-collapsed']` writes to the single-click handler. The status rail's existing chevron restores it.
- Clicking a session from the flow board now actually hides the LEFT sidebar (the conv list / flow board) instead of the chat panel — the prior fix targeted `$convPanel` which is the kanban-split chat. Added a body class `sidebar-tucked` that hides `.sidebar` + `#sidebarResizer`, paired with a floating "☰ Sidebar" restore button in the top-left and Esc-to-restore. State persists across reloads via `localStorage['ccc-sidebar-tucked']`.
- Group-chat files now self-normalize trailing blank lines. Agents writing to the chat via the Edit tool routinely leave dozens of trailing blank lines at the end of a post (worst observed: 230+ blank lines under a 9-line body). The reader UI hid them but every other agent re-reading the file paid tokens for each blank — wasted context. Added `_group_chat_normalize_whitespace(real_path)` that walks the file, strips trailing blanks per post, and guarantees exactly one blank line between consecutive `## ` headers. Called from `_group_chat_post` (after each Human entry) and `_group_chat_read` (on every reader poll) so the file stays lean across multiple agent posts in a row. Idempotent — no-op when the file is already clean.
- Group chat pill no longer claims "new message · just now" minutes after the actual last message. The pill's freshness calc was using `Math.max(chat.last_activity, chat.last_mtime)`, but `last_mtime` is the chat file's stat mtime — the server bumps it on every metadata write (name_map updates, sidecar refreshes, etc.) without any real message arriving. So the file's mtime stayed pinned to "just now" while the actual last conversation event was minutes/hours old. Fix: drop `last_mtime` from the reason/age calc; only `last_activity` counts as a real message signal. `gcShouldShowActivePill` still consults `last_mtime` for the "should the pill exist at all" check (so the pill stays visible during background writes) — only the displayed reason and age tighten up.
- Conversation history search no longer crashes under concurrent requests. The server caches one read-only sqlite3 connection for the whole process, but runs behind `ThreadingHTTPServer` (a thread per request). A single sqlite3 connection can't be used from multiple threads at once — `check_same_thread=False` only silences Python's guard, it doesn't serialise access — so overlapping searches raised `sqlite3.InterfaceError: bad parameter or other API misuse` (SQLITE_MISUSE), surfacing as 500s and error log spam. Every use of the shared connection (`search_conversation_history` BM25 + semantic paths, `get_history_message`) is now serialised through a new `_history_query_lock`, and the index-reset path takes that lock before closing the handle so it can't be yanked out from under an in-flight search. Added a concurrency regression test that reproduced the exact error before the fix.
- install.sh no longer hard-exits when the `claude` CLI isn't on PATH; it now warns and continues. CCC also drives Codex / Gemini / Antigravity sessions and the dashboard is useful without any engine installed, so the old gate silently dropped curious DMG downloaders the moment they double-clicked the .app — install.sh would error in a Terminal they may have already closed, and the .app's only signal was a "didn't start in 60s" fatal alert. Required prereqs are still git and python3.
- Fixed the message echo sometimes lagging after pressing Enter. The optimistic "sending" echo now paints immediately and synchronously, before any async work (stopping in-progress text-to-speech, the inject-input request) — previously a TTS teardown was awaited first, so sends during playback felt delayed.
- When the conv input bar's send fails because the session's launch directory no longer exists on disk, the error toast now shouts the missing path ("⚠ Session cwd is gone: /path — every send will fail …") and stays up for 12 seconds instead of vanishing in 5. Previously the generic `invalid_cwd` server error message flashed by quickly and users assumed their text was being injected when it wasn't. Same longer-stay treatment applied to macOS automation-permission failures.
- Fixes intermittent "last message in a conversation disappears" bug. Both conversation caches (`_CONV_PARSE_CACHE` parsed events + `_CONV_BYTES_CACHE` serialized response bytes) used `st_mtime` (float, 1-second resolution on macOS/Linux) as their freshness key. When a final assistant event landed in the same wall-clock second as a previous cache write, a poll/re-fetch would hit the stale cache and re-render the conversation without the trailing message. Switched `_conv_parse_jsonl_mtime` to return `(st_mtime_ns, st_size)` — nanosecond precision + file size — and updated all four callers to use the tuple. Cache invalidates correctly even on sub-second appends.
- `/api/sessions/live-activity` is now coalesced, so concurrent dashboard polls no longer pile up and pin a CPU core. Every dashboard client (browser tab + desktop app) polls this endpoint, and each poll recomputed the whole live snapshot from scratch with no sharing. When polls got slow, clients fired new ones before old ones finished — a feedback loop that left many identical builds running at once, contending on the GIL. The snapshot is now cached behind a single-flight lock with a 1.5s TTL: at most one build runs at a time, and concurrent or rapid polls share its result (20 concurrent polls → 1 build; 50 rapid polls → 0). The data is inherently approximate (sidebar WIP chips), so ≤1.5s staleness is imperceptible. Combined with the codex incremental-tail and engine-detection memoisation fixes, this collapses the steady-state CPU of the poll path.
- Live tool indicator (the green "▶ Bash command /bin/zsh -c …" pill) no longer leaks into a group-chat reader from the session you came from. `openGroupChatReader` stopped the conv/spawn SSE streams and rewrote `#conversationsView`, but left `currentSession.id` pointing at the previous Claude session. The 1s `liveStatus` poller kept querying that session and `updateLiveToolStrip` kept appending a `.conv-live-tool-inline` node into the now-group-chat-rendered view. Fix: call `setCurrentSession(null, …)` on group-chat open so the poller bails (`refreshLiveStatus` short-circuits on missing id) and sweep any in-DOM live-tool nodes immediately so there's no flash.
- Dragging a conversation outside the Command Center **macOS app** window now opens another in-app window instead of launching the system browser. The dashboard also routes pop-out fallbacks through the native `.app` when installed.
- Model picker popup now flips ABOVE the pill when there isn't room below the viewport (the pill lives in the input strip near the bottom of the screen, so the default below-anchor used to clip the popup mostly off-screen). Picks below when it fits; otherwise picks whichever side has more room and clamps to keep the popup fully visible.
- Two more "no edits" false positives fixed. (1) Server: the Claude Bash parser was discarding the `signals["edit"]` flag from `_shell_command_signals`, so sessions that edited via `sed -i`, `tee`, `apply_patch`, `cat > file`, `printf > file`, `perl -pi`, etc. stayed marked as has_edit=false. Every other engine's parser already consumed this signal; the Claude branch was the lone holdout. (2) Client: `hasNoEdits(c)` now returns false for any live or just-spawned session so an in-flight session that hasn't recorded an edit yet doesn't get prematurely labeled "no edits". WIP/sending chips already take precedence at higher priority, so this just prevents the misleading verdict during the gap before they appear.
- Sessions that delegate work to subagents no longer show a misleading "no edits" lifecycle chip. `c.has_edit` only tracks Edit/Write/NotebookEdit tool_uses on the PARENT session's JSONL; when the parent spawns subagents (Task tool), the actual edits live in the subagent JSONLs and the parent's `has_edit` stays false. Both `hasNoEdits(c)` (list view) and the flow chip path now suppress "no edits" when `c.subagent_count > 0`.
- Organize no longer forgets manual parent connections (sessions dragged onto objects, nested objects). New rule R9 documented in the source. Pre-pass: every visible node's `data-flow-parent` attribute is re-synced from the persisted `flowNodeParents` map so stale render-time attributes can't shadow a fresh drop. Session bucketing reads the map FIRST (manual link) and the DOM attribute SECOND (default parent). Post-pass: a full sidebar re-render rebuilds the DOM from source-of-truth maps (`flowNodePositions` + `flowNodeParents` via `flowParentMapFor`) so connector lines and parent links survive the Organize pass — no more reliance on a future poll-driven render to fix drift.
- Pasted-image thumbnails now actually clear after sending. The post-send clear in every send path uses `el.value = ''` via direct assignment, which does NOT fire an input event — so the prior "input goes empty → clear thumbs" listener never triggered post-send and the thumbnails lingered. Hook the value setter on every composer the paste handler binds to; any assignment (user input, programmatic clear, autoresize callsite) that results in an empty value now triggers thumbnail cleanup.
- Fixed a layout reflow issue where the plan usage popover was cut off by using bottom-anchored positioning and restricting its max-height.
- After /compact lands, the view now scrolls to the resume-card boundary instead of the very bottom. Users reported the log "went back a day" after compact — actually the prior auto-scroll-to-end landed them at the post-compact END (still empty since no replies yet), so the visible area showed the LAST pre-compact message above the boundary. New behavior: detect the just-arrived compact-resume event and scrollIntoView({block: 'start'}) on its DOM node so the "Resumed from /compact summary" header sits at the top of the gaze area.
- Fixed queued outbound messages disappearing from the conversation pane after switching away and back in the conversation list.
- Ready to merge no longer shows the same PR twice. When two conversations both reference the same `tail_pr_number` (e.g. a coding session + a GitHub-issue mirror), only the most recent one is kept in the section. The convs list is already sorted by recency upstream, so first-seen wins.
- Ready-to-merge rows now prefer the real session row over the synthesized github_pr row when both reference the same PR — clicking the row jumps into the session that worked on it instead of opening the PR in a new tab. Previously, whichever row landed first in the dedup pass won; if the github_pr-source row got there first, the click was routed to `window.open(tail_pr_url)` (the source='github_pr' branch in the row click handler) and the session was unreachable from this section. Dedup now swaps in-place when a real session row shows up after a github_pr one, keeping position stable.
- Renamed sessions no longer revert to the AI-generated title after the next sidebar refresh. `_hydrate_conversation_rows` was unconditionally setting `display_name = None` for interactive sessions without a side-car name override — but `rename_session()` for dormant sessions writes the new name to the JSONL as a custom-title event, NOT the side-car. The clear was wiping the parser-derived rename on every fetch and the client fell back to `ai_title`. Now we keep whatever display_name the parser derived from the JSONL.
- RTL: added a MutationObserver that walks every `.assistant-text` / `.user-msg` and tags its block descendants with `dir="auto"` post-insertion as a backup for the renderMarkdown regex. Catches DOM inserted by alternate paths (group-chat doc, issue render, etc.) or rendered by markdown that doesn't go through the central `renderMarkdown`. Idempotent — only sets the attribute when missing.
- RTL fix v2: Hebrew/Arabic paragraphs now actually right-align (not just flip character order). The prior `unicode-bidi: plaintext` + `text-align: start` CSS-only approach had a known gotcha — `text-align: start` resolves based on the element's `direction` property, which `unicode-bidi: plaintext` doesn't change. Added `dir="auto"` directly: (a) appended to every block-level tag (`p`, `li`, `blockquote`, `h1-h6`, `td`, `th`, `dt`, `dd`) emitted by `renderMarkdown`, and (b) on every `.assistant-text` / `.user-msg` wrapper at creation time. The browser now detects direction per element from first strong directional character and aligns accordingly.
- Prevent parallel session scans from piling up and slowing the whole app.
- Sidebar search input focus-loss fix now covers the poller-driven re-renders too — the earlier `5b8c4a8` change only covered the keystroke-debounced render path, but the sidebar also re-renders every 5-15 seconds from independent pollers (liveStatus, sessions, group-chat-active). Those ticks landed mid-keystroke and blew away focus. Restore at the lowest level (right after `$convList.innerHTML = …` in `renderConversationList`) so all render paths benefit.
- Sidebar search input no longer loses focus after every keystroke in the native (WKWebView) app — capture focus + caret position before each debounced sidebar re-render and restore them right after if the search box was the active element. Only restores when the search box owned focus going in, so clicking away to a conversation between keystrokes doesn't yank focus back.
- Slash command suggestions are now ranked so name matches beat description matches. Typing `/context` no longer highlights `/compact` (whose description includes the word "context"). Scoring: exact name (after `/`) > name prefix > name substring > description substring; ties broken alphabetically.
- Slash-commands typed without arguments (`/compact`, `/context`, `/clear`, etc.) now render as visible "User typed /compact" events instead of being stripped to empty. The cleanIssuePrompt regex required all three of `<command-name>`, `<command-message>`, `<command-args>` to match; bare commands omit the args tag, so the match failed and the catch-all stripped everything — leaving the user_text blank. Users perceived this as "lots of text lost" between their last message and the SYSTEM compact marker. Now `<command-args>` is optional in the collapse pattern, and the `/compact` line surfaces between the prior conversation and the compact boundary. Also strips `<local-command-caveat>` / `<local-command-stdout>` wrappers so their verbose plumbing doesn't leak through.
- Conversation row times in the sidebar no longer get stuck at "1d" / older when the underlying JSONL keeps getting written. Root: `archiveData` (the source for the In Progress / archive lists) was only refreshed on app boot and folder-filter change. `refreshLiveSessionsActivity` patched live sidecar fields onto rows but never the time fields (`last_interacted` / `modified` / `mtime`). So a Codex session whose CLI ran intermittently — or any session whose work continued across the day — kept showing whatever row time the original archive scan captured, even hours/days later. Added a 90s `archiveTimes` poller that calls `refreshArchiveData({staleOk: true})` and re-renders. 90s sits under the server's 5min `_ARCHIVE_RESPONSE_CACHE_FRESH_TTL` so most hits are cheap cache reads; the cadence guarantees row times advance within ~90s of real activity. Paused while the tab is hidden.
- Streaming bubble content is no longer wiped when a sub-agent (Task tool) starts emitting blocks concurrently with the parent. Previously `ensureStreamingBubble` kept a singleton `_streamingBubble` keyed on `_streamingMsgId` and CLEARED it whenever a new message_id arrived — so the parent's "let me find the existing X tests…" bubble vanished the moment the subagent's first `assistant_block` landed with a different msg_id. Refactored to one bubble per msg_id (lookup-by-data-msg-id in the DOM); existing bubbles survive new sibling msg_ids landing. `clearStreamingBubble` now sweeps every `.stream-bubble` node so end-of-turn cleanup still works with multiple bubbles in flight. JSONL handoff path (`renderConversationEvents` line 19764) already removed bubbles by msg_id, so no change needed there.
- Fixed the visual jump and flicker when assistant text is streamed. Streaming text blocks are now rendered incrementally as Markdown matching the final layout, horizontally aligned with the settled conversation messages, and no longer prepended with a layout-shifting timestamp prefix.
- `test_conv_bytes_cache_misses_when_pending_input_queued` no longer leaks a fake `cache-pending-test-session` JSONL into the user's real `~/.claude/projects/-cache-pending/`. The test now mocks `PROJECTS_ROOT` to a tmp dir for its duration and cleans up in a finally block. Previously the ghost row showed up in the live CCC UI after every test run and the archive endpoint errored on it because the session had no real metadata.
- Injected sessions (UX-fixes-queue, etc.) now show the yellow WIP lifecycle chip while the agent is processing. Two changes: (1) `sessionIsOptimisticallySending(sid)` is now part of the `_isAgentRunning` calculation, so the chip appears as soon as the inject lands instead of only when the sidecar emits its first event. (2) The optimistic-sending window bumped from 60s to 5min so it bridges most agent turns. The chip clears either when real sidecar data lands or when the 5-minute window expires.
- "Shell" / tool-name WIP chip no longer sticks on idle sessions. Added a final sanity gate in the row render: if the agent's last event was a `result` (turn-finished marker) AND the row hasn't seen any new activity in the last 60s AND there's no fresh optimistic-send tag AND no pending spawn, `_isAgentRunning` is forced false — regardless of whatever stale `pending_tool` / `sidecar_tool` fields the server forgot to clear. The result-event marker is the authoritative "turn done" signal; trusting it over stale tool-name fields prevents the row from showing "Shell" indefinitely after the shell command finished.

## [4.6.0] - 2026-06-03

### Performance
A focused pass on the things that made the dashboard feel heavy. Net: the
daemon idles instead of pinning a core, and opening anything is fast.

- **The dashboard no longer pins a CPU core.** `/api/sessions/live-activity` —
  polled continuously by every open dashboard (browser tab + desktop app) —
  recomputed the entire live-session snapshot on every request, and slow builds
  made clients fire new polls before old ones finished, piling up into GIL
  contention that held a core at ~124%. The endpoint is now coalesced
  (single-flight + 1.5s TTL), so concurrent/rapid polls share one build. Steady
  state drops to idle.
- **Group-chat opens are ~40× faster** (~1485ms → ~35ms for a 6-participant
  chat). Each participant re-read and JSON-parsed the *entire* Gemini chat store
  and re-ran `ps` process scans; the Gemini `sessionId` is now cached by
  `(path, mtime)` and the `ps`-backed liveness scans share a 3s single-flight
  cache.
- **Long conversations open near-instantly.** Opening a chat used to parse the
  whole transcript (a 22MB / 6,600-line session took ~320ms). It now loads only
  the most-recent messages (windowed parse, ~17ms) and shows a "Load earlier"
  affordance that auto-loads history as you scroll up, preserving your scroll
  position. Applies to Claude, Codex, Cursor, and Antigravity transcripts.
- **Codex sessions with screenshots open instantly.** Codex tool output that
  embeds images was inlining multi-megabyte base64 blobs — a 54MB session served
  a 40MB payload that took ~1.8s just to gzip. Images are now lazy-loaded on
  demand via `/api/conv-image` (the scheme the Claude parser already used),
  collapsing the payload ~1000× (40MB → 0.04MB) and the open to ~177ms.
- **Live-session activity tracking is incremental.** The per-session tail
  extractors (Codex/Cursor/Antigravity) re-read the full rollout on every poll;
  they now resume from a saved byte offset and parse only newly-appended lines.
  Per-session engine detection is memoised (it was re-scanning the Gemini store).
- **History search no longer crashes under concurrent use.** The cached
  read-only SQLite connection was shared across worker threads without
  serialization, throwing `sqlite3.InterfaceError` in bursts; access is now
  serialized.
- **New: CCC self-health in the footer** — server CPU, live-activity build
  latency, and recent error count, via a new `/api/health` endpoint.

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

## [4.4.0] - 2026-05-31

### Added
- Bottom-left live-refresh status now shows a red "Active Group chat" pill while a group-chat orchestrator timer is active or recently triggered; clicking it opens the latest active chat.
- Add a stand-alone annotation bookmarklet at `static/bookmarklet.js` that injects a DOM picker overlay into any page (e.g. a BYM dev server at `localhost:3001`), captures element selector + nearby text + HTML excerpt, and copies a Claude-ready annotation block to the clipboard.
- Saved annotations can now open a new session draft prefilled with the annotation context without submitting it.
- Extract and display real-time token usage and context window consumption for Antigravity sessions using the local language server trajectory RPC.
- Antigravity sessions now show a per-turn `<in> | <out> | <thinking>` token chip under each assistant message, plus running session totals in the bottom context bar — mirroring the format Antigravity prints in its own UI. Pulled from the trajectory's `modelUsage` per-step counters via `GetCascadeTrajectory` (the same RPC the bottom-bar context pill already calls).
- Add active WIP (Work in Progress) indication to Antigravity sessions in the sidebar and Kanban board when a prompt is running.
- Archived sidebar section gains the same "by project / by time" toggle as In Progress and GH Issues, plus an "Expand all / Collapse all" chip that fans every project group open or shut in one click. Defaults to "by time" so existing flat-archived behavior is preserved; flipping to "by project" buckets archived sessions under collapsible folder headers (archived group chats stay flat below the project groups).
- Added a Homebrew install path alongside the curl one-liner. `brew tap amirfish1/ccc && brew install ccc` installs CCC into the Cellar, puts `ccc` on `PATH`, and pins a brew-managed Python so the formula keeps working when the system `python3` drifts. Foreground with `ccc`, background with `brew services start ccc`, or use CCC's own launchd agent via `ccc --install-service`.
- Conversation rows now smoothly slide from their old position to the new one when a session bumps to the top of its section after a fresh "last updated" time (FLIP-style animation).
- Add a hover pin action for conversation rows so pinned conversations stay at the top of the sidebar until unpinned.
- Added a macOS DMG install path alongside the curl one-liner and Homebrew tap. Drag `CCC.app` onto Applications and launch — a lightweight wrapper runs the same `scripts/install.sh` under the hood (so all three paths share one source of truth and the `claude` CLI / `python3` prereqs are still required). DMG built via `scripts/build-dmg.sh`; download from the latest GitHub release. Attribution channel `CCC_FROM=dmg`.
- Added a search box to the Files sidebar header to quickly filter files mentioned in the conversation.
- **Find in page**: Cmd-F now opens the find-in-page modal, and searching updates live as you type.
- **UX Fixes Queue**: The "Add to UX fixes queue" button now respects your selected engine (Claude, Gemini, or Antigravity) and creates discoverable sessions in your history.
- Flow view repo and object nodes can be collapsed to hide their descendants and condense the board.
- Flow view now supports drag-range selection and moving selected nodes together.
- Flow view can create repo/object-linked draft session cards that save the task locally and only spawn the real session when Play is clicked.
- Added a Flow view for in-progress sessions with recency-sorted repo/session nodes, persistent positions, persistent parent links, and custom objects.
- Flow view supports trackpad pinch zoom, zoom controls, and an expand button for a full-page board.
- Added an engine-agnostic frame-health readout to the footer: shows the worst frame interval over the last ~1.5s (green ~16ms / amber / red), so jank is visible in the Mac app where there's no devtools console. Long frames are tagged typing vs idle to localize the cause.
- The active group chat is now visually highlighted in the sidebar while its reader is open.
- Added an "Active work" section to the group-chat orchestrator panel showing exactly what is running (auto-nudge cadence, last nudge and targets, live session count) with a one-click Stop control.
- Added a per-group-chat enable/disable knob in the conversation list. Disabling halts the orchestration loop (nudges/reminders) for that chat so it stops consuming tokens, without touching the participant sessions.
- Group chat interface now highlights participant mentions with a blue styling, and introduces an orchestrator sidebar pane to track the waiting list and last spoken time of each participant.
- Group chats now populate immediately in the sidebar on page load without waiting for the local archive background scan to finish.
- Group chat files now automatically receive a `**Wake-status:**` header block tracking the live state (awake/sleeping) of all participants.
- Gzip responses for HTML / JS / CSS / JSON / SVG / manifest, with an allowlist that explicitly excludes `text/event-stream` so SSE keeps streaming. Cuts `static/app.js` from ~920 KB to ~240 KB and `/api/conversations/all` from ~2.7 MB to ~500 KB on the wire.
- Showed the template gallery in the inline new-session screen.
- Surface Claude Code's live `/context` tokens in the footer pill alongside CCC's calculated estimate, so the two numbers can be compared at a glance and 1M sessions are detected from the real limit reported by Claude.
- Added a small "● Live refresh" pill in the bottom-left corner so it's obvious when the auto-refresh tickers are firing. The pill fades out whenever a text input or textarea is focused (which is exactly when the tickers pause for smoother typing), and pulses gently otherwise.
- Polished the CCC.app shell: standard Edit menu (⌘V/⌘C/⌘X/⌘A/⌘Z/⌘⇧Z work in WKWebView), descriptive window title with full version, tagline "One inbox for all your AI agents." in the About dialog, shorter `CFBundleName` ("Command Center for Claude+") for the macOS menu bar, full descriptive `CFBundleDisplayName` ("Command Center for Claude, Codex, Antigravity") for Finder + Gatekeeper dialogs. Bundle on disk renamed to "Command Center for Claude, Codex, Antigravity.app" so the Finder DMG view and `/Applications` show the descriptive name. `scripts/build-dmg.sh` learned a `--fast` flag for adhoc-only builds (~15s vs ~3min full notarized) for development iteration.
- `/api/sessions/spawn` now accepts an optional `engine` field (`claude`, `codex`, or `antigravity`) so the bundled `ccc-orchestration` skill can launch non-Claude sibling sessions without switching endpoints; legacy `gemini` maps to `antigravity`.
- Browser-page and screen annotations can save local notes with URL, selector, viewport, surrounding text, or captured screenshot context for later agent handoff.
- Added threshold-gated perf instrumentation: slow requests log `[SLOW] <method> <path> <ms>` to the server log (tunable via `CCC_SLOW_REQ_MS`, default 500ms), and client-side poller overruns / >200ms longtasks beacon to `/api/client-log` so they surface in the same `service.out.log` — useful in the Mac app where there's no devtools console.
- Added a periodic-trigger transparency strip in the bottom-left footer: one live chip per poller showing its interval, a dot that blinks each time the trigger fires, and a "time since last fire" counter (turns amber when a trigger goes stale). Click a chip to toggle that trigger on/off. Makes the 1s/5s/15s poll cadences visible and surfaces when a trigger is paused (kill-switch, window hidden, or typing-mute).
- Persist split-pane layouts, division ratios, focused panes, and active conversations across browser refreshes and repository switches with repository-scoped isolation.
- Added support for previewing markdown files in the right-side status rail (sidebar) when clicking them in the Files Panel, replacing the sidebar contents until closed.
- Squeezed rail-action items (Launch, Vercel, Next.js, Session, etc.) into a compact horizontal wrapping row instead of a vertical stack.
- Relaxed sandbox checks on `/api/reveal-file` and `/api/read-file` to allow opening pasted images and session-referenced files even when no active repo context is available.
- Surface conversation files in a dedicated sidebar panel, sorted reverse chronologically, with thumbnail previews for images.
- Added Sparkle-powered in-app auto-update to the macOS DMG build. `CCC.app` now ships with `Sparkle.framework` 2.9.2 bundled in `Contents/Frameworks`, signed end-to-end with the project's Developer ID and notarized. A new "Check for Updates…" menu item triggers Sparkle's standard updater UI; background checks run daily against the appcast at `https://amirfish1.github.io/claude-command-center/appcast.xml` and DMGs are EdDSA-signed so a compromised mirror can't push a malicious binary. `scripts/build-dmg.sh` gained a `--fast` flag (ad-hoc sign, skip notarization) for iteration and now signs every nested Sparkle helper with hardened runtime. A companion `scripts/release-dmg.sh` builds the DMG, signs it with the Sparkle private key, and updates `docs/appcast.xml` with the new item.
- Added server-backed Spawn defaults in the Settings menu so the New session engine/model picker and ccc-orchestration share one default engine/model source when callers omit explicit spawn choices.
- Surface Claude Code Task tool spawns in two places: a purple `🤖 N` chip on the conversation-list row (with a pulsing `▶ N` suffix when any spawn is still in flight), and a "Subagents" panel in the right status rail showing the last 8 spawns with description, optional `subagent_type`, and an in-flight / done status pill. The server-side transcript parser now counts Task tool_use blocks and matches their tool_results to flip done; rows pick up `subagent_count`, `subagent_in_flight_count`, and `subagent_recent` fields without any new API endpoints.
- Added real-time word highlighting to the text-to-speech player and updated the TTS engine to run entirely in the browser using the Web Speech API. Added ability to toggle playback speed from 1.0x to 1.5x on the fly.
- Replace textual letter chips (A/G/C) and the separate blue live dot with a single unified, animated SVG engine icon at the start of each row.
- Interactive "What's New" modal showcasing key features (Antigravity engine support, Flow view, Page annotations, Group chats, Row pinning) on first load after a version bump, or manually via a "what's new" link in the sidebar brand header.
- **Worktree spawns for Codex and Antigravity.** The `🌿 worktree` toggle is now enabled for every engine in the new-session row (previously only Claude and Gemini). `/api/sessions/spawn[-codex|-antigravity]` accept a `worktree` boolean; when set, a fresh `feat/<slug>` worktree is created off the launch cwd and the engine runs there.

### Changed
- Annotation screenshots now capture a wider parent/grandparent region around the picked element instead of the bare element rect, so reviewers can see where the element sits in the page (a tight 14×14 color swatch alone is meaningless). The DOM selector and element rect in the payload still anchor to the actual annotated element.
- Annotation payloads slim down ~94%: drop `html_excerpt`, `selected_text`, `nearby_text`, and `document_rect` from both the client payload and the on-disk record. The screenshot, selector, element text, and URL are enough for the next agent to act; the raw outerHTML and surrounding paragraphs were noise that bloated `/api/annotations` responses and could end up as a multi-kilobyte session prompt. Remaining field caps: note 2000, url 1000, title 200, element.text 200, element.selector 400, element.href 400.
- Annotation "Add to UX fixes queue" now sends the note into a shared `UX-fixes-queue` Claude session for the Command Center repo, creating that session when needed instead of opening a local draft.
- Antigravity new-session model choices now use AGY's supported settings path instead of a nonexistent `--model` flag.
- Bolded the user's messages in the conversation pane and in the sticky top-bar "last user message" so they stand out from assistant text at a glance.
- The Report a bug modal now closes automatically about 2 seconds after a successful submit, instead of waiting on the Close button.
- Collapsed the Report-a-bug modal's Title and Description fields into a single "Details" field; the GitHub issue title is now derived from the first non-empty line.
- "By project" sidebar view now uses the same row padding, gap, and title styling as "by time" so rows feel identical between the two grouping modes. Previously the by-project rows were ~33% tighter (4px vertical padding + smaller gaps + tighter line-height), which made the by-project view feel cramped. Folder grouping cues (project header + tree-line indicator) are preserved.
- Coalesce background poller re-renders of the conversation list through a single animation frame, so overlapping live-status / session-list / group-chat ticks no longer trigger multiple redundant renders.
- Make the sidebar app title slightly larger with a colorful gradient treatment.
- Rename the visible app brand to Command Center for Claude, Codex, and Anti-Gravity.
- The session list poll now uses a conditional request (ETag/If-None-Match): when nothing has changed the server returns `304 Not Modified` with no body, so the ~3MB payload isn't re-transferred, re-parsed, or re-rendered every 10 seconds.
- Conversation list project grouping now gives single-session projects their own headers and connector lines, matching multi-session project groups.
- Dark mode is now the default theme for new installs (previously matched the OS setting). Existing theme preferences are unchanged.
- CCC.app inside the DMG is now a real native macOS app — a Swift `WKWebView` shell that hosts the dashboard inside its own Mac window with full menu bar (`⌘Q`, `⌘R`, zoom, etc.), its own Dock icon, and proper window state persistence. Previously the .app was a thin shell that opened the dashboard in the user's default browser. The server lifecycle is now managed by the .app: if `:8090` is already bound (launchd service or foreground `./run.sh`) it just attaches; otherwise it spawns `run.sh` as a child and shuts it down on quit. Universal binary (arm64 + x86_64), ~260 KB compiled, ~600 KB DMG.
- Dropped the gemini option from the engine select dropdowns, mapped the Gemini sparkle SVG shape to the Antigravity engine option, and fixed the double-dropdown visibility bug.
- Custom engine selection dropdowns with engine-specific SVG icons and CSS styling.
- Relocate the conversation Files Panel from the left sidebar to the right-side status rail (the conversation sidebar).
- Flow session cards now use list-view work status signals, show compact elapsed time, and include an inline archive button while hiding archived sessions.
- Flow view now starts repos collapsed, adds Collapse all/Expand all and All/1d/7d controls, hides Kanban from the list/flow toggle, and shows last-updated timing on nodes.
- The footer frame-health probe now samples only during interaction (typing/scroll/pointer) and stops when idle, instead of running a requestAnimationFrame loop every frame forever. The perpetual loop was forcing continuous render/paint and pegging CPU in the Mac app's WebKit — an observer effect that inflated "idle" readings.
- The footer frame-health readout is now clickable: click it to pause/resume all CSS animations, a one-click A/B test for whether infinite box-shadow pulse animations (which WebKit can't GPU-composite) are the source of jank.
- Group chat polish: the orchestrator panel now shows the chat's ID and filename in a "Chat" strip at the top (click to select for copy), and the "💬 N active coordinations" pill moves from the topbar to the bottom-left of the sidebar footer so it's glanceable without crowding the toolbar.
- Group chats now interleave with session rows (or folder groups) inside the In Progress list, sorted by recency, instead of always sitting at the top.
- Enlarged the font size of the group chat message headers (poster names and timestamps) for improved readability.
- Added a sticky metadata bar at the top of group chats showing the "Original Poster" and "Last Poster", mirroring the sticky headers of regular agent conversations.
- Group chat sticky header now dynamically updates the "Viewing Poster" based on the message currently visible at the top of the viewport instead of statically showing the last poster.
- Group Chat polish: orchestrator panel now resolves participant hashes to friendly names (hash dropped to a small secondary chip with the full session id on hover) in "Waiting on" and "Last spoken / mentioned"; "Waiting on" no longer wraps the label across two lines; absolute timestamps in the panel and the message stream are now shown as relative time (e.g. "2d ago") with the full timestamp on hover; lifecycle "system: removed / pinged …" entries render at smaller, muted weight so they stop drowning out real messages; and the "Install Command Center" toast is hidden while a group chat is open and stays dismissed for ~5 years instead of re-nagging every two weeks.
- Hid the template gallery from the inline new-session page while keeping the template machinery available.
- Removed the modal-based "New Session" UI in favor of a purely inline experience.
- Added an inline model picker next to the engine selector for `__new__` sessions, extending support to antigravity and other engines.
- Tune the `#convInputContext` progressive-trim breakpoints — cotenants warning now hides at ≤ 900px, SHARED CLONE chip + "in sync" status at ≤ 800px, and the repo path actively caps at 28ch (≤ 800px) / 18ch (≤ 600px) so it ellipsifies before the strip's overflow:hidden swallows the cost + model pills on the right.
- `#convInputContext` now progressively trims items as the conversation pane gets narrower instead of letting elements spill off the right edge (which was hiding the model pill in split-pane / narrow layouts). Drop order: the `⚠ N other sessions here` warning hides first (≤ 700px), then the `SHARED CLONE` / `WORKTREE` kind chip (≤ 580px); the repo path ellipsifies in place. Implemented via CSS container queries on the strip itself plus `display: inline-block` on `.wp-path` so `text-overflow: ellipsis` actually fires.
- The "Last updated" line under the sidebar title now uses mm/dd/yy formatting instead of dd/mm/yy.
- The right-rail markdown viewer can now resize up to 1600px wide (was 520) so longer files breathe, and it gained an "Open" button in its header that launches the file in the system's default app.
- Group chats now live at the top of the In Progress section instead of their own section, and the sidebar splits the single "+ New session" button into "+ New session" and "+ New Group chat" — drag a session row onto a chat row or the open chat reader to add it as a participant.
- Mobile/touch typing now feels closer to a native app: the session composers render at 16px so iOS no longer zooms the page on focus, the Return key shows a "send" hint and stops auto-capitalizing/auto-correcting commands, and the app shell tracks the on-screen keyboard (via `visualViewport` on iOS, `interactive-widget` on Chrome Android) so the input bar rides above the keyboard instead of hiding behind it. Standalone PWA mode also pads the composer past the home indicator.
- Move the model pill back to the rightmost slot in the conv input-context strip, matching the Claude Desktop convention. The chevron-cover root cause is already mitigated by the 40px right padding added in `c87c467`; the prior reorder-first-to-survive-clipping defense is reverted because the user prefers the familiar rightmost placement.
- Background pollers (session list, GH issues, group-chat status, Vercel/localhost/worktree badges, live status) now pause while the window is hidden and refresh once when you return to the tab — no wasted CPU or network in the background.
- Keep pinned row pin icons visible and show an unpin X over the pin on hover.
- Slightly reduce the size of the session SVG icons in the conversation list to 13px (from 16px) for a cleaner layout.
- Removed all path restrictions from `/api/open`, allowing files outside of the repo or log directory to be opened from the conversation pane and files sidebar.
- Removed the remaining "path outside repo/session sandbox" checks from `/api/read-file` and `/api/reveal-file` so transcript path chips outside the repo open just like they do via `/api/open`. The extension allowlist (which still blocks .app/.sh/.py for reveal) keeps RCE off the table.
- Sidebar header now leads with a 26px app icon and folds the version chip onto the title line, dropping the brand block from four rows to three.
- Tightened the sidebar header: version and "Last updated …" now share one line, and a small `git: amirfish1/claude-command-center` link sits below "check for updates".
- Replace the 502 KB `static/icon-app.png` sidebar header icon with the existing `static/icon.svg`. Drops a one-MB 1024×1024 raster that was being rendered at 26×26 in favour of the 374-byte vector — same visual identity, no scaling artifacts, and removes the largest single asset on the page. Also drops a stray `ccc-sidebar-icon.png` that was accidentally committed.
- Fresh installs now land with the status rail (Original ask / Session activity / Files) on the right side of the conversation pane instead of the top strip. Users who explicitly chose 'top' or 'right' via the toggle keep their preference — only an unset `ccc-status-pos` key promotes to the new default.
- Added Blank session and Add your own options to the template gallery.
- Replaced generic template-gallery prompts with workflow templates based on common session patterns.
- Renamed the `APPROVE?` waiting chip to `WAITING`. The sidecar's `_needs_approval.json` is set for every "Claude is waiting for your input" state (the `type` field is almost always empty), not just tool-approval prompts — so calling it `APPROVE?` was misleading. Only `AskUserQuestion` still gets the distinct `QUESTION` label.

### Fixed
- Fixed the bottom-left "Active Group chat" indicator never appearing. It now shows (with a live count) whenever an orchestration is running, so background token use is visible.
- Keep sidebar drag interactions from being interrupted by the periodic refresh, and add an annotation action for loading reports into the UX fixes queue prompt.
- Annotation context now includes the captured session ID, and Open new session saves the annotation before loading it into a fresh prompt.
- Launch on an Antigravity app session (one not in the AGY CLI conversation store) now opens Antigravity.app and drops the Terminal into a login shell at the session's cwd, instead of execing into a fresh `agy` chat with a confusing "not in conversation store" message.
- Antigravity-App send errors now surface the real cause — "language server not running, open Antigravity" — instead of always saying "no reusable model config", which only applies when the trajectory loaded but had no model picked.
- Allow the Files panel to open same-session Antigravity brain artifacts such as `task.md` without tripping the repo sandbox.
- Fixed Antigravity follow-up sends spawning parallel resume processes and made pasted-image paths available to AGY resumes.
- Fixed inline Antigravity session spawning so model choices are written to AGY CLI settings and failed launches no longer look like live WIP rows.
- Antigravity conversation panes hide internal task-completion messages that were leaking as assistant text.
- Fixed sluggish rendering (janky scrolling, idle repaints, typing lag) in the macOS app. The WKWebView was configured transparent (`drawsBackground=false`), forcing WebKit to blend the entire page against the window every frame — which is why the identical dashboard was fast in Safari and Chrome but slow only in the app. The web view is now opaque (the dashboard paints its own background, so it looks identical) and uses WebKit's fast rendering path.
- Fixed refreshes that could keep running a stale frontend bundle, leaving the session sidebar stuck on the archive loading checklist even though conversation data had finished loading.
- **AskUserQuestion** in the conversation reader now renders **all** questions
(the tool can carry up to 4 in one call — earlier we silently dropped
everything past `questions[0]`). Each question is its own accent-bordered
callout with header, question text, and bulleted options + descriptions.
The block is also kept visible when "hide tools" is toggled on, since it
is a prompt directed at the user rather than a side-effect tool call.
- **AskUserQuestion in the conversation view** no longer renders as a single
mashed line (`Question Header: Question text Options: a; b; c; ...`).
Header, question, and each option now stack on their own lines with bullets,
so multi-option prompts are scannable instead of squashed.
- Hide the bottom-bar "other sessions" warning first when session context needs more room.
- Fixed: bug-report modal was silently dropping attached screenshots — `bugSubmit` reset the form state (including the captured screenshot) right before reading it, so every "Send report" click filed an issue with no image even when the preview showed one. The reset now wipes UI chrome only and preserves the user's attached screenshot until the modal is reopened.
- Claude Desktop now gets sidebar metadata only for resumable CCC-spawned Claude Code sessions.
- Claude spawns now wait longer for large initial prompts to enter stdin and fail closed if that write still fails, preventing live-but-empty sessions.
- Populated the right-rail Files panel for Codex sessions.
- Mark CCC-spawned Codex exec threads and their repos as Codex-IDE-visible in Codex's local stores and rollout metadata, including a bounded recent-log backfill that preserves each chat's original last-updated time.
- Show `/compact` completion in the conversation pane and refresh the context pill from Claude's post-compact token count instead of leaving the pre-compact total on screen.
- Tighten the sidebar header so title metadata no longer wraps into a tall block.
- Fixed laggy typing in the message composer (most noticeable in the Mac app). The textarea autosize forced a synchronous full-page reflow on every keystroke (set height:auto, then read scrollHeight), which is cheap in Chrome but costly in WebKit on a large DOM, delaying typed characters. The resize now runs in requestAnimationFrame, off the keystroke's critical path, and coalesces bursts to one reflow per frame.
- Fixed composer typing lag in the Mac app (WebKit) by using native CSS `field-sizing: content` to auto-grow the prompt textarea instead of the JS `height:auto` + `scrollHeight` measure, which forced a synchronous full-DOM reflow on every keystroke (~45-86ms in WebKit, cheap in Chrome). The JS autosize now no-ops when field-sizing is supported (WebKit 17.4+/Chrome 123+) and falls back to JS otherwise.
- Pin the conversation background-color palette to the very bottom of the right rail in any rail-visible body mode (was relying on a `body.status-pos-right`-scoped rule that didn't fire for the `.conv-pane.has-conv-bg` layout, so the palette floated near the top of the rail instead).
- Make the conversation input caret visible: pin `caret-color` to the accent color so the blinking cursor is obvious when the input gets focus (the default near-white caret over the dark transparent input bar was almost invisible, especially in the native WKWebView app).
- Second pass on the invisible conv-input caret: the previous `caret-color: var(--accent)` fix failed inside `.conv-pane.has-conv-bg` (where `--accent` is remapped to the palette-chosen `--conv-accent`, which can land too close to the background). Switched the resting caret to `currentColor` (the text color, which is by definition readable against the background) plus an explicit `-webkit-text-fill-color: currentColor` to keep WKWebView from quietly hiding the caret. Focused state still uses the accent so the "I'm here" cue pops on click.
- Clicking anywhere in the conversation input bar now places the caret in the composer. Previously only a direct hit on the single-row textarea focused it, so clicking the surrounding padding or the bottom selector row left the bar feeling "dead" until a second click landed on the text.
- Extended conversation-list row-order hysteresis to the "by time" / flat render path, so rows no longer jitter there either (previously only the "by project" path was stabilized).
- Fixed conversation-list rows jumping around while idle. Session rows now keep their position instead of reshuffling every poll when a background-active session's timestamp ticks; a row only moves once it's meaningfully newer (5-min hysteresis), matching the existing project-group order behavior.
- Fixed the conversation list jumping/drifting on its own: idle poll re-renders no longer re-seat the scroll position, and clicking an archived conversation no longer yanks the list toward the top.
- Typing in the conversation composer no longer hitches every few seconds when the conversation list refreshes — the row-reorder animation now skips its layout-measuring snapshot while any text input is focused.
- Cut the click-to-open latency on already-viewed conversations from ~170 ms to under 1 ms by caching the serialized + gzipped response bytes for `/api/conversations/<id>?after=N` keyed on the JSONL's mtime, and by caching the per-session JSONL path resolution (was a ~125 ms walk of `~/.claude/projects/` per request). Conversation cards now also fire a debounced prefetch on hover, so the first click is usually a cache hit too.
- Merge / pin / archive row actions no longer hide behind the branch badge: the absolute-positioned `.conv-row-actions` overlay now has a solid `--surface-2` background plus a left-edge box-shadow that fully masks any chips (branch "main", context pill, etc.) sitting under the action area. Previously the merge button was visible but un-clickable on rows with a branch badge because the badge's own background sat on top of it.
- Conversation pane no longer goes silent when the SSE event stream dies without an `onerror`: added a 15s client-side watchdog that resets on every event/keepalive (the server already emits a named `keepalive` event every 5s, the client previously ignored it) and force-reconnects if no signal arrives. Also advances `streamPane.lastLine` alongside the global `convLastLine` so post-watchdog/post-error reconnects request the right offset instead of replaying from the original stream-start.
- Fixed the conversation list flickering every few seconds. The list no longer rebuilds all rows when only the relative-time and group-chat "when" labels tick; those update in place instead.
- Conversation-list auto-refresh now preserves the user's scroll position instead of jumping back to the top.
- Removed the duplicate top live-activity strip so active sessions show one in-conversation status indicator.
- Fixed sessions with no workspace or token sample leaving an empty context strip above the composer.
- Show an explicit empty state in the session activity timeline and keep it mounted in the right rail, while Earlier ask stays above the conversation pane.
- Fixed an issue where the engine selection dropdown remained visible in the input box during ongoing sessions.
- Return cached archive rows immediately after browser refresh while a fresh scan runs in the background.
- Two fixes for in-page find (Cmd+F) in the conversation view: the input no longer loses focus after every keystroke (the browser's `window.find` moves focus to the matched element — now snapped back to the find input so live-find actually feels live), and Up arrow now cycles backward through matches symmetrically to Down arrow (previously only forward had a binding).
- Flow draft sessions now keep their repo/object parent link when the draft turns into a spawned session row.
- Flow view grid now fills the available fullscreen viewport instead of stopping at the content canvas height.
- Flow view object-linked sessions now collapse with their object instead of the underlying repo group.
- Automatically expand collapsed folder groups in the conversation list when a search query is active, ensuring matching sessions are not hidden.
- Fixed extensionless absolute folder links in conversation messages so they open through Finder instead of being treated as browser routes.
- Stop the conversation list from reshuffling a few seconds after load: group-chat rows are now seeded from a local cache so they appear in the first render instead of popping in (and re-sorting the list) when the active-chats fetch lands.
- Group-chat mention pills no longer render as `@<full-uuid>` when a participant's name_map entry is itself just the session UUID. Fall back to the 8-char hash (`@32bf4a17`) for those cases and extend the mention regex to consume an optional trailing `-xxxx-xxxx-xxxx-xxxxxxxxxxxx` so the trailing hex chunk no longer dangles outside the pill.
- **Group chat reader no longer leaks events from the previous conversation.** Opening a group chat now stops the active conversation/spawn SSE streams; otherwise in-flight events kept appending below the reader's input row, looking like content "from another conversation" slipping into the bottom of the input box.
- The GH Issues section is now always present (collapsed by default) unless hidden via the Settings view preference, so it no longer pops in a few seconds after load and shifts the conversation list. The count is shown only once issues have loaded.
- Stop the group-chat transcript from rendering a tall column of empty space when the orchestrator dumps multiple "system: removed X" entries: collapse consecutive blank lines in the markdown renderer (was emitting one `<br>` per blank line, so three blanks stacked to three line-heights of empty space) and dim/tighten plain-italic `_… — system: …_` lifecycle entries in addition to the blockquote form.
- Group-chat header refresh now preserves legacy messages and system notes that appear before the first separator instead of treating them as disposable header text.
- Tighten paragraph spacing inside group-chat messages so short single-line messages no longer render with ~50px of padding-looking whitespace (browser-default `<p>` margins collide with the 1.6 line-height to make every message feel broken).
- Fixed the group-chat reader leaving the standard composer hidden when switching to New session, and aligned group-chat message typography and read-aloud controls with the regular conversation pane.
- Group chat reminders now send at most one ping for the latest chat turn, so watcher retries do not repeatedly flood the same recipient.
- Fixed the conversation sidebar getting stuck on "Loading archive…" — the group-chat rendering block was using its own variable before declaration, throwing a ReferenceError that aborted the conversation list render.
- Group chats now carry stable UUIDs, repair stale markdown headers from their sidecars, and re-activate closed chats when the human posts so the reader does not appear to switch to an empty/path-keyed chat.
- Fix 'Hide GH issues' setting to conditionally clear native backlog items in List view without forcing Kanban mode, and automatically remove the Kanban board column when hidden.
- Hide the session overflow menu when the selected engine has no overflow actions, avoiding a dead `...` button on Antigravity, Codex, Gemini, and pkood sessions.
- Stopped shortened placeholder paths and inline `/api/...` endpoint references from becoming broken clickable path links.
- Fixed inline conversation renaming so dragging inside the title editor selects text instead of dragging the row.
- Lazy-load pasted/base64 images in the conversation view. Transcripts with embedded screenshots no longer inline multi-MB base64 blobs into the parse payload — they're fetched on demand via `/api/conv-image` with native `loading="lazy"`. A real-world 11 MB transcript dropped to a 2.7 MB initial payload, so opening image-heavy conversations is much faster. Message text (including tool output) stays in the payload, so search/find still works.
- Fix list numbering restarting at 1 for split or arbitrary starting numbered lists in the conversation view.
- Refresh the context and cost pills as each assistant turn streams in, instead of leaving them stale until the next conversation reselect or page refresh. Fixes #59.
- Fixed continuous repaint cost from live-session icons. The `.conv-session-icon.is-live` pulse animated `filter: drop-shadow`, forcing WebKit to recompute a blur for every live icon on every frame — the dominant cause of the Mac app's idle/scroll/typing jank (cheap in Chrome/Safari, expensive in WKWebView). The pulse now animates only `transform: scale` (GPU-compositable) with a static drop-shadow, so it looks nearly identical with no per-frame filter recompute.
- Live AskUserQuestion cards now include Type something and Chat about this actions, and answer choices can be submitted directly from the conversation pane.
- Live AskUserQuestion status rows now show the assistant preamble, full question, answer choices, and option descriptions instead of compressing them into a truncated one-line tool label.
- Preserve assistant lead-in text when rendering rich live question cards.
- Stop the live tool-activity strip from doing per-second DOM work when no session is live. The 1s ticker now bails immediately on the idle path instead of running a document-wide `querySelectorAll` plus an `innerHTML` write on every tick, which was wasting CPU while a deep conversation sat open with nothing running.
- Fixed completed Antigravity sessions lingering as active/thinking in the UI by correctly determining process state.
- Fixed the blank model selection dropdown for new sessions when selecting the Antigravity engine by populating the options upon entering new session mode.
- Fixed UI hanging on "sending" when Claude Code CLI rejects a slash command (like `/remote-control` in headless mode). The system error message is now rendered inline as an assistant response instead of being silently swallowed.
- Allowed safe local document and media files outside repo roots to open from the Files panel.
- Fixed the localhost status pill spamming `400 Bad Request` every 15s when a dash-encoded project path leaked into the repo context; only absolute paths are sent now, and the pill falls back to "no repo" otherwise.
- Fix external links in the native macOS app: clicking a `target="_blank"` link inside assistant text (or any non-localhost URL) now opens in the user's default browser instead of being silently dropped or replacing the dashboard.
- Fix native macOS app silently dropping `window.alert` / `window.confirm` / `window.prompt`, which made flow-board buttons like "+ Object" appear broken (the dialog never showed, so the click looked like a no-op). The app now renders these as native NSAlert panels.
- Mac app now recovers a dashboard stuck on the loading overlay. A watchdog polls the page after launch; if the overlay is still up past a grace window it reloads the webview, and if that doesn't help it restarts the server (capped to avoid restart loops). Covers both a stalled server handler thread and a hung `app.js` request where the page's own safety nets never register.
- Fixed an issue where markdown images (`![alt](url)`) in conversation history were incorrectly parsed as standard links preceded by an exclamation mark, preventing generated mockups from rendering.
- PNG / JPG / etc. images in the right-rail markdown previewer now render. Adds `/api/local-image` that serves any local image file by absolute path, and rewrites `<img src>` in the rendered markdown to route through it so relative paths resolve against the markdown file's directory.
- Fixed the merge button (and other row action buttons) being unclickable when overlapping with status or branch badges by adding z-index stacking and background inheritance to the action buttons container.
- Fixed an issue where the model selection dropdown remained visible in the input box during ongoing sessions.
- Model pill in the conv input-context strip no longer gets hidden by the absolute-positioned status-rail restore chevron when the rail is collapsed (added 40px right-padding to reserve the chevron's footprint) and is now rendered first in the right-side cluster so it survives right-edge clipping when the strip overflows in narrow panes.
- Fixed the dashboard loading screen getting stuck on "Loading conversations..." due to TDZ (Temporal Dead Zone) ReferenceErrors when trying to access the newly added model selection dropdown ($nsmModelSelect) and the _defaultModelsByEngine mapping before their initialization statements.
- Enable the "Move to repo" overflow menu action for agent sessions (antigravity, gemini, pkood, codex) by resolving the target repo to a visual repo pin via the `/api/repo/pin` endpoint instead of requiring a physical JSONL move on disk.
- Restore `$nsm…` const declarations so the app boots after the new-session-modal markup was removed from `index.html`. Without them, `initCustomEngineSelect($nsmEngineSelect)` at script tail threw a ReferenceError that aborted the rest of boot — leaving the loading overlay up and conversation clicks silently no-oping. All call sites already guard with `if ($nsm…)`, so resolving the handles to `null` is enough.
- Fix transcript file links that look absolute but are actually project-relative. Clicking a path like `/growth-machine/content/landing/index.html` inside a conversation now falls back to resolving against the session cwd and repo root when no filesystem-rooted match exists, and surfaces a toast when the file still can't be opened.
- Fixed CCC orchestration spawn/list/ask correlation for headless multi-engine callers.
- Sessions renamed via Claude Code's `/rename` slash command now show their new name in CCC's archive view, matching Claude Desktop. The archive walker was only honoring `custom-title` events whose value still equaled the original spawn `agent_name` — a `/rename` diverges from that and used to be dropped silently.
- Keep pinned conversations inside their current section while sorting them to the top of that section.
- Preserve the exact sidebar scroll offset when pinning rows so the list does not shift by a row.
- Keep the conversation list from jumping when pinning or unpinning a row.
- Fixed Python 3.9 compatibility crash on startup by quoting a recently added union type hint in server.py.
- Fixed queued inputs disappearing from the conversation pane when switching between conversations. Pending messages in Codex, Gemini, Antigravity, or terminal queues are now persisted to disk (`pending-inputs.json`) and rendered with a "sending..." status upon switching back.
- Fixed long Bash heredoc commands rendering as unreadable one-line gibberish in the conversation pane.
- Conversation search now includes live Claude sessions that exist in the process registry before their transcript JSONL is created, with a non-empty detail pane while the transcript is pending.
- Fixed console 404 spam and missing thumbnails for remote image links (e.g. URLs pasted into a chat): the session file sidebar now loads `http(s)` image targets directly instead of routing them through the local-only pasted-image proxy.
- Terminal `/rename` titles now actually show in the conv list, kanban, and flow board. The client-side title precedence in `_renderRow`, `flowRowTitle`, and the kanban card builder fell through to `c.ai_title` when neither `name_overridden` nor `spawn_named` was set — a `/rename` in the terminal sets neither flag (the new title diverges from the spawn `agent_name`), so the auto `ai_title` was always winning. Treat `display_name` that diverges from `ai_title` as a user rename and surface it.
- Fixed "Add screenshot" failing with "`screencapture` not found on PATH" when CCC is launched outside a login shell — now resolves the binary directly at /usr/sbin/screencapture and surfaces a clearer "Screenshots are macOS-only" message on non-macOS platforms.
- Fixed laggy typing in the conversation search box. Each keystroke re-rendered the entire sidebar (filter + render over hundreds of rows, ~15ms) synchronously, delaying when the typed character painted. The local filter render is now debounced ~70ms, so fast typing stays responsive (measured ~15ms/keystroke → ~1-3ms) and the list filters once you pause.
- Fixed estimated session cost being inflated by the number of times a session had been resumed. Claude Code's JSONL re-records each Anthropic API response under a fresh event uuid (but the same `message.id`) every time the session is resumed or forked; CCC was summing every copy, producing 2x–8x the real cost. Token totals and the daily stats view dedupe by `message.id` now. (#60)
- Cap session-name overrides at 120 chars on both read and write so a pasted annotation context (or Codex's raw first-message `title`) can no longer end up as an 11kB row title that stretches the sidebar and bloats `/api/sessions` payloads. Existing oversized entries in `session-names.json` are truncated on next load; codex rows clamp their SQLite `title` fallback too.
- Cut `/api/sessions` warm latency in half (~2.7s → ~1.2s) by caching `_git_branch_for_cwd` per `.git/HEAD` mtime and building the Antigravity CLI-log meta map once per scan instead of re-parsing every log for every row.
- Conversation command rows use inline shell comments as readable titles before falling back to raw command text.
- Discourage CCC-spawned Claude sessions from running broad recursive grep commands that can hang on command-center FIFO logs.
- Fix files panel starting in a collapsed state by default, ensuring files are immediately visible on selection.
- Source-badge SVGs (the engine icons in session headers added in 17c3731) now stretch to fill their badge container instead of rendering at intrinsic SVG size.
- Fixed duplicate session rows appearing in the conversation list when spawning new alternative engine runs (Codex, Gemini, Antigravity) by dynamically backfilling their session IDs in the spawn registry.
- Restructured the input bar layout in the new session screen to place the text input area above the engine and model selections.
- CCC-spawned Claude sessions now keep their launch name as the card title instead of falling back to the original prompt.
- Stop the conversation SSE stream from re-reading and re-parsing the entire transcript every ~0.3s while a session is idle. The tail loop now skips the re-scan unless the JSONL's mtime changed, so leaving a large conversation open no longer pegs CPU and disk in the background. Applies to the Claude/Codex and Gemini/Antigravity stream paths.
- Fixed a phantom "waiting for answer" box that lingered after a question was declined. A dismissed AskUserQuestion (Esc in the terminal) never clears its in-flight marker, so the dashboard kept showing the prompt even after the agent moved on; the transcript is now treated as the source of truth. Also enlarged the question-box fonts for readability.
- Static-asset server now serves `.png` / `.jpg` / `.jpeg` / `.ico` (was css/js/svg/webmanifest/json only), so the new sidebar app icon at `/static/icon-app.png` actually loads.
- Hide inline images in the conversation sticky-header "ask" preview slot — they used to render at up to 220px tall inside a 170px row, blowing out the preview and burying the actual prose. The full conversation view still shows them; a small "📎 image attached" marker appears in the preview so the user knows there was an attachment.
- Strip the raw `<command-name>` / `<command-message>` / `<command-args>` markup that Claude Code injects into slash-command user messages before showing them in the sticky-header "earlier ask" slot, so the slot shows `/rename` instead of the literal XML-like tags.
- Subagent count now picks up Claude Code's legacy `"name":"Agent"` tool_use blocks in addition to the current `"name":"Task"` shape. The on-disk JSONL records the older `Agent` alias for transcript compatibility, so sessions actively spawning subagents were showing `subagent_count: 0` in CCC despite the conv view clearly displaying multiple `Agent` calls. The `🤖 N` chip and the status-rail Subagents panel now reflect reality.
- Model pill no longer shows the raw `<synthetic>` (or `<unknown>` and similar) sentinel value that Claude Code writes into the JSONL when a message was synthesized client-side (interrupts, /clear stubs, fallback errors). The pill now falls back to the engine name with a tooltip explaining the situation, and the picker remains clickable.
- Grouped tool-call headers now describe file reads, searches, folder listings, and named tools instead of falling back to "other tools."
- Collapsed tool groups name shell commands such as `git status` and `git commit` instead of saying "other tools".
- Pauses the 10s conversation-list auto-refresh while the user is typing in a text input or textarea. The full innerHTML rebuild on a long list was stalling keystrokes every ten seconds; the refresh resumes as soon as the input loses focus or the user stops typing for a tick.
- Pauses the 1-second live-tool ticker while typing, and adds `contain: layout style` to the conversations view so streaming-event reflows can't ripple into the composer. Together with the prior 10s-refresh skip, the input-box hitches should be gone.
- Conversation search now keeps direct session UUID matches at the top, even when history-index results also match the query.
- Drop the "Add to UX fixes queue:" preamble from annotations sent into the UX-fixes-queue session so the receiving Claude treats the note as a direct request to act on rather than something to route further.
- Sending an annotation to the UX-fixes-queue now optimistically marks the receiving session as "Sending…" in the conversation list, the same way a user-typed message does. Previously the inject landed in the target session's terminal but the sidebar showed no activity signal until the next sidecar tick, which read as "did anything happen?".
- Two conversation-list QA fixes:

1. `WIP` badge no longer fires just because a linked GitHub issue carries the `claude-in-progress` label — that's not a liveness signal and made idle sessions read as actively running. The GH state now renders as a separate muted `issue: in progress` chip. Also tightened the codex/gemini/antigravity "open turn" heuristic from 30 minutes to 5 so an idle session no longer flags WIP for half an hour after the last message.

2. In Progress section now shows a footer like `+ 42 older sessions hidden by 7d — show All` when the 1d/7d window is filtering rows out, so sessions outside the window don't read as "disappeared". Clicking the footer flips the window to "all".
- Cap the conversation-list WIP badge from the active-sidecar branch at 30 minutes of staleness. Previously, a session whose sidecar wedged in `active` state with `last_event_type === "assistant"` would show WIP indefinitely (the `_midTurn` and missing-`sidecar_ts` fallbacks bypassed any freshness check). Once nothing has happened for half an hour, drop WIP regardless of those signals.
- Separate "agent is actively running" from "agent paused waiting on you" in the conversation list. Sessions with `needs_approval` or a pending AskUserQuestion now render a calm cyan `WAITING` / `APPROVE?` / `QUESTION` chip (no pulse) via the new `.activity-waiting` class instead of the yellow pulsing `WIP` chip that previously made paused sessions look like they were still running.

## [3.4.0] - 2026-05-20

### Added
- Antigravity sessions now appear in the sessions view and archive, with read-only transcript rendering from `~/.gemini/antigravity/brain/` and launch support for the AGY CLI.
- Archive view replaces the opaque "Loading archive…" placeholder with a per-stage progress checklist (Scanning project folders → Reading conversation transcripts → Inferring active branches → Checking worktree status → Codex / Gemini → PR status → Issues → Group chats). Each stage shows ○ pending / ● running / ✓ done / ! error / – skipped. Server progress is exposed at `/api/archive/loading-status`; the frontend polls every 250ms while the archive endpoint is in flight.
- New chip-color toggle in the sidebar header switches folder chips between per-folder hues (default) and a uniform muted neutral. The toggle button itself shows a 4-dot color swatch that mirrors the active state — colored hues in colored mode, all grey in muted mode. Persisted under `ccc-chips-mode` and restored before paint.
- Added `scripts/open-app.sh` (and a matching `./run.sh --app` shortcut) that opens the dashboard as a chromeless, dock-pinnable browser window via Chrome/Edge/Brave/Chromium's `--app=` flag. Honours `PORT` and supports `--browser`, `--size`, `--url`, and `--dry-run`. macOS is the supported target; Linux is best-effort via PATH lookup. (#17)
- Added a 17-test kickoff suite for `_classify_attention`, covering all seven Needs-Your-Attention buckets (pending tool, sidecar waiting, pushed-with-open-issue, uncommitted edits, committed-not-pushed, needs-attention label, open backlog) plus suppression rules (archived, verified, TODO/parking, dormant-waiting-or-done, icebox, in-progress) (#55).
- Dragging a conversation row outside the Command Center window now opens that conversation in a focused pop-up pane near the drag target, with compact source/project/title headers, a conversation-only pop-up boot path, and 24 per-conversation pane background colors in the right rail.
- One-command install: pipe `scripts/install.sh` into bash to clone, verify prereqs, and launch the dashboard on port 8090.
- **Dockerfile + docker-compose.yml** for a containerized trial install. Single-service compose mounts `~/.claude` from the host so the dashboard can see your transcripts; image stays stdlib-only (no `pip install`). See `docs/docker.md` for feature gaps vs. the native `./run.sh` path on macOS. (#54)
- Space-key navigation in the group-chat reader: tap Space to jump to the top of the next message. The reader detects each message by its `## ts — hash: name` heading (rendered as `<h2.md-h>`) and scrolls the next one into view. Listener is global, gated on the reader being live and the user not typing into the reply textarea, and uses capture-phase `preventDefault` so the browser's default page-down doesn't compound the jump.
- **Static GitHub Pages demo** with seeded mock data at [amirfish1.github.io/claude-command-center](https://amirfish1.github.io/claude-command-center/). The full kanban renders with 12 fake sessions across 3 fake repos (open issues, live work, waiting-for-input, merged PRs, archived). Mutating actions show a "this is a demo — install for the real thing" banner instead of running. Implemented as a thin `fetch` wrapper in `static/app.js` (`installDemoMode`) that activates on `window.__CCC_DEMO__ = true` or `?demo=1`, routing every `/api/*` call to a hand-written JSON fixture under `docs/demo/api/`. Real-mode behavior is untouched. (#49)
- Docker-based clean-install smoke test (`tests/install-smoke/Dockerfile` + `scripts/test-install.sh`), wired into a `install-smoke` GitHub Actions workflow that pipes `scripts/install.sh` into bash on a fresh image and verifies the server boots, serves the dashboard, returns `/static/templates.json` as JSON, and persists the attribution channel.
- Prompt the user during the one-command installation process to automatically install the background daemon/service (launchd agent) so that CCC starts automatically on login/reboot.
- Provide a tip with the `./run.sh --install-service` command if the user chooses not to install the background service during setup.
- Added a conversation speaker button that reads selected text or the latest message through the local macOS `say` command and stops when new input is sent.
- Added a "localhost" pill next to the Vercel deploy pill in the topbar. Detects Next.js projects (via `package.json` deps, `next.config.*`, or any workspace under a turbo/npm-workspaces monorepo), starts a dev server on click, surfaces the running port, and opens `http://localhost:<port>` in a new tab. Right-click stops the server. Tracked per repo; SIGTERMed on CCC shutdown.
- Turbo-aware: when a `turbo.json` with a `dev` task is present at the picked dir or any ancestor, the pill runs `npx turbo run dev` from the turbo root (with `--filter=<workspace>` when the picked dir is a sub-package), matching the user's normal monorepo flow. Falls back to `npm run dev` / `pnpm dev` / `yarn dev` based on lockfile when there's no turbo setup.
- Every click states what it did via a toast — including the no-op cases ("No Next.js here", "Pick a repo first", "CCC server needs restart") — so the affordance is never silent. Failures (start_failed) surface the log tail in the pill's tooltip.
- Added a native folder picker button to the new-session CWD control so users can choose a directory without typing the path.
- Show recent repository chips in the new-session CWD picker, with the selected folder highlighted for quick switching.
- Anonymous opt-in telemetry — five-field daily ping, off by default, dismissable forever from a one-row dashboard banner. See [`docs/telemetry.md`](docs/telemetry.md) for the full contract. (#48)
- Public roadmap at [`docs/roadmap.md`](docs/roadmap.md) — Shipped / In progress / Planned / Under consideration / Out of scope, grounded in real tracking issues, with a stated voting policy. Answers "is this maintained, is the thing I want planned?" in one click. (#50)
- Dismissible PWA install banner surfaced in tab mode with platform-specific copy (Chrome/Edge get a one-click Install button wired to `beforeinstallprompt`; macOS Safari and iOS Safari get the right manual-install instructions). 14-day dismissal via `localStorage`; auto-hides if the app is installed mid-session.
- CCC is now installable as a Progressive Web App. Safari users can "Add to Dock" and Chrome/Edge surface "Install app" so CCC opens in its own window without browser chrome — the single biggest visual gap with native dashboards. Adds `/manifest.webmanifest`, a minimal root-scope `/sw.js` (passthrough fetch, no caching — CCC talks to live agent state), and `static/icon.svg` + `static/icon-maskable.svg`. The `/static/` allowlist gains `.svg` and `.webmanifest` content types. A `@media (display-mode: standalone)` polish layer in `app.css` lifts conv-row breathing, demotes chromatic workspace badges to neutral surface tones, and adds a draggable region hint where the platform supports `-webkit-app-region` — all gated on standalone so the in-browser layout is byte-identical.
- The right-side status rail is now resizable, can be collapsed by dragging its handle to the right edge, and shows a slim restore tab when hidden.
- Preserve unsent input drafts separately for each selected session, including the inline new-session prompt.
- Added a settings menu action to restart the Claude Command Center server in place, labeled with the current port.
- Added slash-command suggestions in the session composer. Typing `/` in a Claude session now opens a scrollable picker backed by Claude-advertised commands when available, plus local command files, skills, and installed plugin commands/skills from `.claude/commands`, `.claude/skills`, and `.claude/plugins/cache`, with a common-command fallback for older transcripts including `/mcp`.
- New status-position toggle in the sidebar header moves the conversation pane's "Original ask" + Session activity into a 260px right rail (or back to a top sticky panel). When the rail is active it also collects session-level controls (Launch terminal, Vercel, Update pill, Close & announce, Live badge, Session ID, ⋯ overflow). Persisted under `ccc-status-pos` and restored before paint to avoid layout flash. In top mode the rail is removed entirely so narrow viewports / mobile reclaim the full conversation width.
- Added a template gallery to the new-session modal with five starter prompts (fix-issue-and-pr, refactor-with-tests, investigate-failing-ci, review-pr, scout-codebase). Cards are driven by `static/templates.json` so users can add or edit templates without touching code, and clicking one prefills the prompt body, engine, and worktree toggle. The gallery is hidden when the modal opens pre-filled, e.g. from "edit prompt before launch". (#46)
- Added a smoke-level test that pins the New Session modal's `static/templates.json` shape (id / name / description / engine / worktree / prompt) so a future edit to the gallery JSON can't quietly break the modal. (#46 follow-up)
- Added `vscode-extension/` v0.1.0 — a Marketplace-ready VS Code extension with publisher id, MIT license, 128×128 icon, two palette commands (`CCC: Spawn session` posts the active workspace folder + a user-entered prompt to `/api/sessions/spawn`; `CCC: Open dashboard` opens the configured CCC URL in the default browser), configurable host/port (`claudeCommandCenter.host`, `claudeCommandCenter.port`), graceful non-modal toast when CCC isn't running, and a tag-driven `publish-vscode-extension` GitHub Actions workflow that calls `vsce publish` on `vscode-v*` tags using the `VSCE_PAT` secret. (#52)
- **Worktree env-setup hook.** When a session is spawned with a worktree, CCC now runs `.ccc/worktree-init` in the new worktree (if present and executable) before launching the session. `CCC_WORKTREE_PATH`, `CCC_SESSION_NAME`, and `CCC_PARENT_REPO` are exported. A failing hook is logged but does not block the spawn. See `.ccc/worktree-init.example` for a starter template. (#47)

### Changed
- Antigravity new-session launches and follow-ups now use headless AGY print mode instead of opening an external terminal.
- Codex file-read command results now render as compact code excerpts with syntax highlighting instead of raw terminal output.
- Conversation rows: time format compacted from "5h ago" to "5h" (Omnara-style); time and row-actions (merge / start / archive) now share the same right-edge slot — time at rest, actions on hover-swap. Every row gets an always-visible muted bullet at the left, becoming the live dot when the session is actively polled. Folder chips fixed-width (130px, center-aligned, ellipsis on overflow) so the column reads as a clean stack. Title font, line-height, and row padding tuned to match the Omnara pane density. The "X DAYS GAP" / "XH GAP" separators between rows now show only at the first qualifying boundary (recent → older) instead of between every-other row.
- Conversation pane typography overhaul: Inter font globally, real markdown rendering for blockquotes (italic with hairline left rule), numbered + bulleted lists, and `**Title**`-on-its-own-line treated as a pseudo-header. Header sizes bumped (h1=28, h2=22, h3=17 with weight 800) so structure reads as structure, not just emphasis. Reading width is no longer capped — text flows to the pane width.
- **GH Pages demo** now ships four distinct transcripts (`_id-1` through `_id-4`), each ~30+ events with realistic tool calls and a closing `<session-state>` block. The demo's `installDemoMode()` fetch wrapper hashes the clicked session UUID into the pool so different sidebar rows surface different conversation panes. Every transcript ends with the labeled DID / INSIGHT / NEXT STEP USER summary card the dashboard renders.
- Group-chat reader's input box now matches the conversation input: autosizing textarea (Enter sends, Shift+Enter newline, grows up to ~10 rows then scrolls), single rounded card with focus-within ring, round arrow-icon send button. Was a single-line input + rectangular "Send" button.
- Group-chat participant pings now include an advisory snapshot of the latest chat post in the `/group-chat` injection, so sessions can immediately see why they were woken up while still treating the markdown chat file as the source of truth before posting.
- Conversation input bar redesigned as a single rounded card with a soft focus-within ring (no inner-input border). The send button is now a 32px circle with an up-arrow SVG. The `<input>` was promoted to an autosizing `<textarea>` — Enter sends, Shift+Enter inserts a newline, height grows up to ~10 rows then scrolls. The workspace status strip (branch / context / cost / model) sits below the input now (was above), is forced to a single line, and dedupes the path when the worktree branch already encodes the last path segment. The Esc button is demoted to a borderless ghost; the tty label moved to a muted footer line under the card with a `⏎ send · ⇧⏎ newline` keyboard hint.
- GitHub-issue view layout: when an issue row is selected, the issue title + #N + "Open on GitHub" link are moved into the right rail's "Original ask" slot (replacing any stale leak from a previous session); the Close-as-completed / Close-as-not-planned / Close-as-duplicate buttons move into the rail's actions cluster; the `.conv-input-context` workspace strip is hidden because issues aren't sessions. Body keeps just the issue body / comments. Round-trip cleanup so opening a non-issue conv afterwards restores the regular layout.
- README and GitHub repo description now name **Claude Code**, **Codex**, and **Gemini CLI** explicitly, with a per-engine support matrix flagging where parity is partial (Codex transcript ingestion, Gemini resume/model picker). Docs-only — no behavior change. (#53)
- New-session mode now accepts typed CWD paths in addition to known folder suggestions.
- The inline new-session CWD picker now sits above the prompt composer so its folder dropdown is easier to reach before starting a session.
- Tightened the by-project conversation list spacing so grouped rows and project headers show more sessions without the oversized vertical gaps.
- PWA install metadata now includes the W3C-standard `<meta name="mobile-web-app-capable">` alongside the older `apple-mobile-web-app-capable`. Chrome, Edge, and Firefox prefer the unprefixed form; Apple's prefixed variant is deprecated but still required for older iOS Safari versions, so both ship during the transition.
- Rewrote the README hero: sharper one-liner naming Claude Code, Codex, and Gemini CLI; demo GIF, one-command install, and read-only demo link all surfaced in the first scroll; dated "Recent" timeline below the hero; Star History chart embedded just above the install footer. The Quickstart, engine support matrix, and From-source instructions are unchanged. (#51)
- Task notification payloads in the conversation pane now render as compact structured cards instead of raw `<task-notification>` XML blocks.
- Toolbar reorganized: Update pill moved to a top-left alerts strip in the sidebar header. Terminal-panel toggle, History indexing status, Worktrees, Stats, Report a bug, and font A-/A+ moved into the gear settings menu. Session-level actions (Launch, Vercel, Live, Close & announce, Session ID, ⋯) live in the right rail when active and back in their original toolbar slots when status-pos is set to top. The conv toolbar collapses to 0px when empty so it stops eating dead vertical space.
- Sidebar rows no longer show a generic `WIP`/`waiting` chip for idle live sessions whose only signal is `sidecar_status: "waiting"`; the live dot carries that state instead.

### Removed
- Removed the leftmost 48px multi-repo rail (`.ccc-repo-rail` / `<aside id="ccRepoSidebar">`). The rail's CSS, HTML element, and the `renderRepoSidebar()` function in `app.js` (plus all 5 callsites) are gone. The topbar repo dropdown remains as the way to switch between repos.

### Fixed
- Antigravity app-only sessions now show a read-only composer that points to Launch instead of attempting an AGY CLI send.
- Allow Antigravity app-only sessions to receive follow-up input through the running Antigravity app instead of requiring an AGY CLI conversation.
- Antigravity CLI-only sessions now render their CCC/AGY log details when AGY does not write a transcript JSONL.
- Antigravity headless follow-ups now target AGY CLI conversations and avoid sending app-only sessions through an unsupported resume path.
- Failed Antigravity sends now clear the optimistic Sending state and app-only sessions no longer enter the AGY CLI send path.
- Antigravity sessions now reuse the real CLI transcript row after spawn, show detected model labels when token samples are unavailable, and keep the workspace/status strip attached to the input card.
- Keep new-session placeholders visible in the All conversations archive and hand them off to the real transcript row when it appears.
- Fixed archive search results so background refresh progress no longer replaces an active keyword search's empty-results state.
- Archive startup now paints transcript rows before PR, issue, and group-chat hydration, and skips expensive effective-repo inference for sessions that never changed directories.
- Fixed archived group-chat rows opening and then immediately getting overwritten by an empty conversation selection that left the detail pane stuck on "Loading...".
- Surface Claude `AskUserQuestion` prompts in live status, transcript tool summaries, and the Waiting column.
- Fixed session input sends to live Claude background agents by routing them through the daemon PTY instead of spawning a failing `claude --resume` process.
- Hide Claude Code `<bash-...>` transcript wrapper messages from conversation panes and titles.
- Claude spawn now resolves the CLI via `CCC_CLAUDE_BIN`, common user install paths, and the launchd service's baked PATH before returning a clear setup error.
- Codex session context badges now use the latest turn's input tokens and Codex's reported context window instead of cumulative token totals or the Claude-only 1M override.
- Codex and Gemini sessions with a connected terminal now receive input in that terminal instead of silently routing through a headless resume path.
- Attach CCC pasted-image uploads to Codex spawn/resume prompts with `codex exec --image`, and allow those uploaded images to be revealed from transcript path links.
- Codex conversation panes now show the live Thinking/WIP strip when the sidebar already marks the session active.
- Treat defunct reattached Codex/Gemini resume processes as exited so follow-up prompts are not queued forever behind a stale run.
- Fixed split conversation panes losing their bottom scroll position when a second session is opened, added an End button for quick return, and kept live streaming bubbles scoped to the owning pane.
- Fixed split conversation pane focus getting out of sync with sidebar selection and restored input drafts placing the caret at the start instead of the end.
- Keep conversation search results from jumping back to the top during background refreshes.
- Demo: seed the kanban "In progress" column with live sessions, restore GH issue detail rendering by adding the `/api/issues/_id/details` fixture, and fix the empty conversation pane on archived (and every other) session by giving the transcript fixture the correct event schema (`user_text` + `assistant.blocks[].kind`).
- Fixed the GH Pages demo's empty "In Progress" column (fixture mtimes were a full year stale — 2025-05-19 vs today) and the empty transcript pane on click (Jekyll was stripping every `_id`-prefixed fixture under `docs/demo/api/`; added `docs/.nojekyll`).
- Gemini spawning now detects CLI installs in common user bin directories that are missing from the server process PATH.
- Fixed sends to live headless Claude sessions so prompts queue while a tool subprocess is still running instead of disappearing into the FIFO.
- Fix the dashboard's "In progress" list starting empty on first load by implementing a smart default fallback (7 days if active sessions exist, otherwise falls back to all).
- The session input composer now clears and shows the pending message immediately, and live-status polling skips headless Claude processes so large parallel-agent runs do not stall sends.
- Sidebar Board toggle opens the Kanban board again and stays there across archive refreshes/searches instead of immediately snapping back to the conversation list.
- Sidebar "Board" toggle is visible again — restores the only entry point to the Kanban view, which had been hidden by an unrelated CSS rule (issue #44).
- CCC's macOS service installer now registers the LaunchAgent in the per-user launchd domain with modern `bootstrap` / `enable` / `kickstart` calls, adds `./run.sh --service-status`, and documents the login-start install and update behavior in the OSS quickstart.
- Live activity chips now strip shell setup noise from Bash commands and keep command previews readable while tools are running.
- Fixed the localhost dev-server pill for Turbo workspaces: it now targets the selected session cwd, starts scoped apps with `npx turbo dev --filter=<package>`, shows the exact dev command while waiting on a stuck port, and lets a normal click restart matching Next.js processes after a CCC restart.
- Fixed `Start failed: package manager not found: npx` from the localhost pill when CCC was launched from a shell that hadn't sourced nvm. The spawn now probes nvm (`~/.nvm/versions/node/*/bin`, honoring the `default` alias), Homebrew (`/opt/homebrew/bin`, `/usr/local/bin`), and `~/.local/bin`, then prepends any of those that actually contain a `node` executable to the child's PATH — so `npx`, `npm`, `turbo`, and `next` resolve regardless of how CCC was started.
- Fixed the localhost pill choosing an unfiltered Turbo root when no session cwd is available; root-level Next.js monorepos now resolve to a concrete workspace app before starting or reporting status.
- Markdown file links in transcripts now open with the system `open` handler instead of only revealing the file in Finder.
- Fix markdown file links whose targets are wrapped in `<...>`, so local paths with spaces open correctly instead of including the angle brackets in `/api/open`.
- Fixed the new-session CWD chooser so its folder suggestions open from an explicit dropdown button while still accepting typed paths, and kept the new-session pane notice in sync with the selected CWD.
- Keep the conversation pane on the optimistic new-session prompt with a Sending indicator while the spawned session is materializing.
- Show pasted-image references as inline images in the Original ask panel instead of leaving them as links or path text.
- Queued prompts typed while Claude is busy now appear as user messages in the conversation pane instead of being skipped as transcript attachments.
- Ready to Merge rows no longer disappear between archive refreshes while pull-request metadata hydrates.
- Ready to merge no longer flashes archived or already-merged PR sessions during the archive load before GitHub status hydration completes.
- Ready to merge rows now retry transient GitHub PR-state lookup failures quickly and fall back to `gh api`, so already-merged PRs show the merged checkmark and drop out of the Ready to merge section.
- Fixed session titles that showed Claude Code local-command wrapper text instead of the first real user prompt.
- Fixed transcript file links so exact files written outside the session cwd can still open from that session, and archive-only folder slugs no longer poison the open request.
- Stop CCC's session-state reminder from being typed into terminal injections, while keeping Claude spawns on the hidden system-prompt path and rendering command-center pasted image paths inline.
- Sidebar rows now keep the yellow `WIP` chip visible for live Claude sessions that are waiting for user input or carrying a needs-approval marker, so active-but-idle group-chat participants no longer look quiet.
- Fixed spawned-session sends that could hang forever when a reattached headless agent's stdin FIFO stopped accepting writes, retiring the stale worker and resuming the session in a fresh process instead.
- Fixed finished spawned/resumed agent processes leaking log file handles, which could make session send/status requests fail after the server had been running for a long time.
- Queue sends to busy live Terminal sessions until Claude Code reports that the session is idle, instead of typing into a prompt that is not ready to accept input.
- Prevent terminal message sends from opening macOS's "Choose Application" dialog when restoring focus to Chrome app-mode windows.
- Show an actionable macOS permission message when Terminal injection times out instead of dumping the full `osascript` command into the toast.
- Strip CCC's session-state reminder from Codex session titles and user-message panels so rows do not show dashboard boilerplate as the task name.
- Recognize `app_mode_loader` in macOS permission guidance and handle screenshot clipboard images exposed through `clipboardData.files`, not just `clipboardData.items`.
- Session commit/push detection now tokenizes shell commands so searches or prose containing `git push` do not make read-only sessions look pushed; sessions that create a worktree with a relative path now update their displayed workspace branch.
- Expanded tool-call groups now wrap long shell commands, compact repeated file-edit paths, and hide routine successful edit-result boilerplate.
- Show source labels for ambiguous transcript tool calls such as Computer Use desktop-control actions.

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

[Unreleased]: https://github.com/amirfish1/claude-command-center/compare/v5.8.0...HEAD
[5.8.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v5.8.0
[5.6.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v5.6.0
[5.5.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v5.5.0
[5.4.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v5.4.0
[5.2.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v5.2.0
[5.1.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v5.1.0
[5.0.1]: https://github.com/amirfish1/claude-command-center/releases/tag/v5.0.1
[5.0.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v5.0.0
[4.11.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v4.11.0
[4.10.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v4.10.0
[4.9.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v4.9.0
[4.8.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v4.8.0
[4.7.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v4.7.0
[4.4.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v4.4.0
[0.1.3]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.3
[0.1.2]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.2
[0.1.1]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.1
[0.1.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.0
