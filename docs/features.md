# What you can do with CCC

[Back to the README](../README.md) · [Engine support](engine-support.md)

## Your sessions, however you started them

CCC reads each engine's own on-disk state. Sessions started in a terminal,
headless processes, and sessions launched from the dashboard all appear in
the same list. Closing the dashboard does not stop your agents. If you resume
a session by hand, its row updates.

A session is a live or dormant conversation. Attach means reading the
engine's transcript and, for Claude Code, sidecar state written by hooks you
approve. You do not configure attachment for each session. See
[Session attach](session-attach.md) and [Architecture](architecture.md).

## Organize and read work

- **Fleet list:** see which session needs you, its remaining context, and cost
  tier without opening every conversation. Pin strategy sessions and nest
  workers beneath them.
- **Flow canvas and Project tree:** group sessions under named, nestable Flow
  objects. The By objects view keeps Current sessions (the last five hours)
  above a Project tree. Resize the divider, collapse branches, or drag objects
  to rearrange your work.
- **Split conversations:** drag a sidebar session to the right or bottom edge
  of an open conversation. Each pane keeps its input bar; the view collapses
  below 900px or closes back to one pane with a click.
- **Board view:** an optional drag-and-drop view of session state, with
  multi-select and column colors. GitHub issues and TODO.md entries can appear
  as backlog cards. The list remains the primary view. See [Board rules](kanban-rules.md).
- **Full-text search:** find previous work across Claude Code and Codex
  sessions. Optional semantic search helps when you cannot remember the words;
  it is not enabled by default. Claude Code removes old conversations after
  30 days by default (`cleanupPeriodDays`); CCC offers an opt-in longer history.

## Start and steer agents

- **Eight spawnable engines:** see the [support matrix](engine-support.md).
  Kimi Code has guided CLI setup, login, and a smoke-test spawn in Settings.
- **Headless spawn:** launch an agent and keep talking from the browser.
- **Resume-on-demand:** sending to a dormant session starts a headless resume.
- **Cost-aware composer:** for a large, stale session, choose cheaper routes
  such as continuing fresh or searching history. A full resume remains a
  priced option. Question-shaped text favors search; task-shaped text favors
  a fresh continuation.
- **Fresh worktree spawns:** launch in `<repo>-wt/<slug>/` on `feat/<slug>`.
  Optional [worktree init scripts](worktree-init.md) can copy local environment
  files or install dependencies before the agent starts.
- **Permission prompts:** answer Claude Code approval prompts inline. CCC
  does not interrupt a possibly mid-turn session without your approval.
- **Desktop handoff:** on macOS, Jump to terminal focuses an attached session
  by TTY. Open in Claude Desktop uses the `claude://resume` deep link.
- **AI-assisted titles:** regenerate a session title through `claude -p`, using
  Haiku by default.
- **Compaction card:** see stages, a live clock, and tokens freed instead of
  an ambiguous spinner.

## Keep work moving

[WatchTower](https://github.com/amirfish1/watchtower) queues hold work beyond
one session's lifetime. Workers can drain them in parallel. Each reads the
queue's shared learnings before starting and writes back at the end. See
[Queues](QUEUES.md) and [installation requirements](install.md#watchtower-queue-engine).

- **Queue inbox:** see tickets needing a decision, per-queue AI briefs,
  GitHub-backed queues, and one-click queue creation from a session.
- **Plan-to-fleet:** import a plan, preview the tickets, approve them, and
  optionally start a worker. See [Plan to fleet](plan-to-fleet.md).
- **Workers tab:** Compact, Cozy, and Detailed densities, fixed columns, a
  WORKING NOW strip, and rows visible before engine sessions exist.
- **Self-attaching agents:** external headless sessions discover parents
  through process ancestry; CLI spawns and queue lanes appear as they start.
- **GitHub integration:** start from an issue with an in-progress label and
  self-assignment. Verify closes the issue with a commit-SHA comment; Archive
  closes it as not planned. Issue bodies and comments render inside CCC.
- **Auto-fix deploys:** optional Vercel polling starts a fix session for a new
  production error, deduplicated by commit SHA.
- **Decision Inbox:** choose what to do about stalled work and token waste.
  See [Decision Inbox](decision-inbox.md).
- **System status:** fleet health, restart-all controls, process cleanup, and
  delivery receipts.

## Costs, free runs, and settings

[Free models](free-models.md) use a managed local freellmapi router. Choose
**Free ($0)** for a supported session, see its $0 badge and savings, and choose
whether to continue free when a paid session reaches its limit. Provider
privacy and rate limits still apply.

Usage tracking shows pace against plan limits, cache-adjusted rankings, and
per-session cost cards. The allocated subscription dollars and API list-price
equivalent are separate estimates. See [Usage database](usage-db.md).

The model policy at `~/.claude/command-center/model-policy.json` applies across
pickers, spawns, and queues; WatchTower workers honor it too. Settings supports
search (Cmd/Ctrl+,), keyboard navigation, and per-section reset. The FIRST
FLIGHT tour is replayable from Settings. [Onboarding](onboarding.md) covers
the free first-task setup.

## Phone access and voice

[Phone access](phone-access.md) connects your browser over a trusted network.
CCC binds to loopback by default, not the open internet. Settings walks through
Tailscale setup, a QR code, and a live connection check.

[Realtime voice](realtime-voice.md) is experimental. Browser mic and speakers
connect to a local Codex voice host over WebRTC. Voice reads sessions and
queues; changes require a confirmation card. It uses your ChatGPT subscription;
a BYOK OpenAI key enables an optional billed fallback.

## Agents working together

Group chats ping each participant when you post, so you do not relay messages
between terminals. The [orchestration API](orchestration.md) also lets one
session spawn work, send messages, or ask a sibling for a reply.

The [12-skill pack](../skills/README.md) builds workflows such as pair-verify,
standup, second-opinion, bug-race, docs-drift, and release-audit on that API.
Each describes cost, dry-run behavior, and its fallback when CCC is unavailable.

CCC also works alongside other skill packs:

- Superpowers Task subagents appear as a chip and live status on their parent.
- `superpowers-to-watchtower` turns a plan into durable queue tickets and can
  dispatch sessions to complete them.
- `fleet-verify` runs a browser-check lane and reports a screenshot and verdict.
- `GET /api/skills` inventories bundled and installed skills with
  `spawns_subagents`, `fleet_aware`, `drives_browser`, and `ccc_synergy` flags.
  The inventory is stdlib-only and cached by modification time.

Bundled skills require [approval](agent-config-consent.md).
`CCC_SKIP_SKILL_INSTALL=1` disables their installation. See the
[skills ecosystem](skills-ecosystem.html) and [inventory](skills-ecosystem-inventory.md)
for implemented features versus proposals.

The optional [ACP adapter](https://agentclientprotocol.com) lets editors and
clients drive Claude Code over JSON-RPC stdio. It runs separately as
`python3 ccc_acp.py` and uses the `acp` extra; the core server remains stdlib-only.

## Integrate your own app

The [cookbook](../cookbook/README.md) contains copy-paste integration prompts:

- [Annotate an element and file work in a UX-fixes queue](../cookbook/annotate-to-ux-fixes-queue.md).
- [Create GitHub issues from an in-app bug report widget](../cookbook/bug-report-widget-github-issues.md),
  including screenshots and a label-driven customer status view.

## How it compares

CCC attaches to work you already have rather than requiring every agent to
start through it. It can also create worktrees, but a worktree per task is not
required to make sessions visible. Routers supply model backends; CCC supplies
the session view and coordination around those agents.

The earlier README comparison was an April to August 2026 survey, not a live
compatibility guarantee. Tools change frequently. Existing public comparisons:
[landing page](index.html) and [heavyweight IDEs](vs-heavyweight-ides.html).
For current limits, use the [engine support guide](engine-support.md),
[platform installation guide](install.md), and [security policy](../SECURITY.md).
