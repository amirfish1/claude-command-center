# CCC introduction — bound coverage transcript

This file is the narration spoken in `docs/intro-video/out/ccc-introduction.mp4`.
Each scene is one slide. Visual sources are the real CCC UI on seeded demo
fixtures (`docs/demo/`, `scripts/story-capture/`) or brand/HTML cards.
Timestamps are filled from the rendered per-slide durations after compose.

## Scene map

| # | scene | start | end | visual source | required moment |
|---|---|---|---|---|---|
| 00 | identity | 0:00 | 0:12 | brand title card | identity: local dashboard, attaches, needs you |
| 01 | fleet | 0:12 | 0:31 | V-01-fleet-scan.mp4 | fleet/list with multiple engines |
| 02 | engines | 0:31 | 1:07 | engine support matrix card | eight spawnable plus three read-only, with qualifications |
| 03 | attention | 1:07 | 1:21 | V-03-attention.mp4 | needs-you / attention moment |
| 04 | flow | 1:21 | 1:37 | V-07-flow-canvas.mp4 | Flow canvas or project tree |
| 05 | split | 1:37 | 1:45 | V-08-split-pane.mp4 | split conversations |
| 06 | kanban | 1:45 | 1:55 | V-06-kanban-drag.mp4 | optional kanban board view |
| 07 | spawn | 1:55 | 2:13 | V-14-issue-to-session.mp4 | spawn or steer from the dashboard |
| 08 | steer | 2:13 | 2:55 | feature grid: attach, desktop, titles, permissions, composer | attach, resume-on-demand, titles, permission prompts |
| 09 | cost | 2:55 | 3:17 | usage and deploy callouts | usage tracking, auto-fix deploys |
| 10 | group-chat | 3:17 | 3:38 | V-16-group-chat.mp4 | group chat or queues/workers |
| 11 | inbox | 3:38 | 4:00 | Decision Inbox card (synthetic demo copy) | Decision Inbox |
| 12 | queues | 4:00 | 4:16 | V-17-queues.mp4 | group chat or queues/workers |
| 13 | workers | 4:16 | 4:36 | V-18-queue-workers.mp4 | queues/workers |
| 14 | search | 4:36 | 4:50 | V-09-search.mp4 | search |
| 15 | mobile | 4:50 | 5:00 | V-15-mobile.mp4 | mobile or Simple Mode |
| 16 | simple-mode | 5:00 | 5:09 | docs/simple-mode/assets/01-home.png | mobile or Simple Mode |
| 17 | settings | 5:09 | 5:40 | settings, tour, system status, ACP | FIRST FLIGHT, Settings modal, System status, ACP adapter |
| 18 | cli | 5:40 | 6:12 | ccc CLI command panel | ccc CLI |
| 19 | install | 6:12 | 6:32 | install paths and live demo | demo and install |

Required visual moments (criterion 3) map to these sources:

- fleet/list with multiple engines: `V-01-fleet-scan.mp4`
- needs-you / attention: `V-03-attention.mp4`
- spawn or steer from the dashboard: `V-14-issue-to-session.mp4`
- Flow canvas or project tree: `V-07-flow-canvas.mp4`
- group chat or queues/workers: `V-16-group-chat.mp4`, `V-17-queues.mp4`, `V-18-queue-workers.mp4`
- search: `V-09-search.mp4`
- mobile or Simple Mode: `V-15-mobile.mp4`, `docs/simple-mode/assets/01-home.png`

## Narration

### 00 identity

CCC is a local dashboard that attaches to coding-agent sessions on your machine, however they were launched, and shows you which one needs you. It is a lens, not a runtime. Close the board, and the sessions keep running.

### 01 fleet

One board, eight engines. Every Claude Code, Codex, Cursor, Antigravity, Kilo Code, Kimi Code, OpenCode, and Devin session lands here. CCC reads each engine's on-disk state, so even a session you started by hand in a terminal shows up. Spawn, monitor, and review them from the same dashboard.

### 02 engines

Spawn from the dashboard works for all eight. Follow-up works on seven. Kilo Code is fire-and-forget: no resume wiring yet. Cursor IDE sync is metadata-only by design, so this is one board for eight engines, not the same support on every engine. Three more engines are ingested read-only today: GitHub Copilot CLI, VS Code Copilot Chat, and Grok CLI. They appear on the board with their transcripts, but you cannot spawn or steer them from the dashboard yet. Kimi Code has a guided setup flow in Settings, Engines, that detects the CLI, walks through install and login, and verifies with a smoke-test spawn.

### 03 attention

When an agent is waiting on you, the row flags it. Attention detection picks up a real question, including a plain-prose one, and on macOS it can fire a desktop notification. You see the needs-you moment instead of discovering it forty minutes later.

### 04 flow

When a flat list is not enough, the Flow canvas lays repos, sessions, group chats, and objects on an infinite zoomable board with edges. The sidebar Project tree groups sessions under nestable Flow objects you name, drag, and reparent, with a live Current sessions band on top.

### 05 split

Drag any sidebar session onto the right or bottom edge of an open conversation to view two transcripts side by side, each with its own input bar.

### 06 kanban

Board view is optional. Drag-drop columns derived from session state, with rubber-band multi-select. The list is the primary surface. The board is an opt-in lens on the same state.

### 07 spawn

Start a session from a GitHub issue with one click. Verify closes the issue with a commit-SHA comment. That needs the gh CLI signed in. Toggle worktree mode to launch in a fresh worktree on a feature branch, with optional init scripts. Headless spawn keeps an in-browser input bar so you can keep talking, no terminal needed.

### 08 steer

Terminal Claude processes show up automatically. Jump to terminal focuses them by TTY. On macOS, Open in Claude Desktop resumes the current CLI session inside the Desktop app. Messaging a dormant session is resume-on-demand: CCC auto-spawns a headless resume to deliver it. Click the sparkle on any card for AI-assisted titles, regenerated via Claude Haiku. Claude Code permission prompts surface inline. CCC never interrupts a possibly-mid-turn session without your Approve. When a session is large and stale, the cost-aware cold-session composer replaces Send with ranked cheaper routes: continue fresh on a lower tier, or search history, instead of a blind expensive resume.

### 09 cost

Usage tracking shows your pace against plan limits, per engine, with cache-adjusted token rankings, before you hit the wall. Throughput attributes a spend spike to the session or automation that caused it. Auto-fix deploys is opt-in: it polls Vercel and spawns a fix-deploy session on new production errors, deduped by commit. The spawned session still has to investigate. It does not arrive with the failure logs.

### 10 group-chat

Group chats keep two sessions on one goal in sync. Post once, and every participant is pinged. Sessions can also ask a sibling synchronously over a local API. CCC ships an orchestration skill so one Claude session can spawn, inject into, and ask sibling sessions over plain HTTP, plus a twelve-skill pack for concrete workflows.

### 11 inbox

Stalled work should not become your problem again. Decision Inbox scans once an hour for what is stuck: a strategy board, WatchTower queues, and idle sessions. A cheap analyst leaves you a three-option card. You click one. CCC spawns or steers the follow-through. The token governor flags sessions burning tokens for nothing, with one-click Nudge, Pause, or Kill.

### 12 queues

File work into named WatchTower queues instead of remembering what to ask which session. Tickets survive closed sessions. The queue inbox shows what needs you. More queue tooling: per-queue AI status briefs, GitHub-backed queues synced from issues, and one-click create a queue for this session.

### 13 workers

Workers drain queues in parallel. Each worker reads that queue's shared learnings file before it starts and writes back when it ends, so a queue handling the same kind of ticket keeps getting faster, not just busier. Plan-to-fleet imports a plan or mission brief into a WatchTower queue from the dashboard, previews the tickets, files them on confirm, and can drain them with a worker.

### 14 search

Full-text search across session history is built in, with zero setup. It covers Claude Code and Codex today. An optional deeper semantic mode, local embeddings, is available when you cannot remember the words you used. Semantic search is not on by default.

### 15 mobile

The whole fleet on your phone: monitor sessions, answer agents, and steer from a browser on your trusted network. CCC binds to loopback by default. It is never meant for the open internet.

### 16 simple-mode

Simple Mode gives your phone a plain-language Home screen: New conversation, Needs you, and Your conversations, without the advanced dashboard vocabulary.

### 17 settings

The FIRST FLIGHT tour is a spotlight walkthrough of the dashboard on first run, replayable any time from Settings. The Settings modal opens with instant search, Command or Control comma, keyboard navigation, and per-section reset. System status is a health modal over the whole fleet: restart-all, spawned-process cleanup, and delivery receipts. An optional ACP adapter exposes CCC over the Agent Client Protocol so editors and ACP clients can drive Claude Code sessions. It runs as a separate process. The core server stays stdlib-only.

### 18 cli

Once CCC is running, the ccc CLI answers what sessions exist and what they are doing, with no dashboard needed. ccc sessions is the live census. ccc models lists every engine's models and effort ladders. ccc quota shows weekly quota left. ccc doctor reports per-engine CLI, auth, and key health, dry-run only. ccc spawn starts a session. ccc send messages a running session. ccc ask blocks for the reply.

### 19 install

Try the live demo first, the full dashboard with seeded fake data, no install required, at ccc.amirfish.ai/demo. To install: one curl line, or brew install ccc, or download the macOS DMG. Git and Python 3.9 are enough to start the dashboard. WatchTower comes with it as the queue engine.
