# CCC pain-to-proof inventory

Canonical source of truth for what CCC solves, what proves it, and what may be
claimed publicly. Every public page, post, or video derives its claims from
this file. If a claim is not here with status **Built**, it does not ship.

- Verified against: source at v5.7.0-dev, latest release v5.6.0 (2026-07-08).
- Verification method: claims checked against `server.py` routes, `static/app.js`
  surfaces, `README.md`, `CHANGELOG.md`, and feature docs under `docs/`.
- Last full audit: 2026-07-10.

Statuses: **Built** (claimable without qualification), **Partial** (claimable
only with the listed qualification), **Experimental**, **Planned**, **Obsolete**,
**Private-only** (never in public copy).

---

## 1. Problem families

Six families. Every public claim belongs to exactly one.

| ID | Family | The moment it names |
|---|---|---|
| F1 | **See everything** | Sessions spread across terminals, engines, and repos; some forgotten entirely. |
| F2 | **Know what needs you** | An agent asked a question an hour ago, or is about to run out of context, and nothing told you. |
| F3 | **Organize work that outgrew a flat list** | Dozens of sessions with no hierarchy; strategy buried under execution. |
| F4 | **Steer many agents without orchestration code** | Getting instructions into running or dormant sessions, and getting agents to coordinate. |
| F5 | **Let work run unattended** | Queues, issues, verification, and shipping that continue while you are away. |
| F6 | **Work from anywhere** | The fleet on your desk, reachable from your phone or continued on another machine. |

---

## 2. Canonical pain table

The public-facing story. 26 rows, each verified. "Proof asset" IDs resolve in
the asset ledger (section 4).

| # | Painful moment | CCC solution | User outcome | Proof asset | Status | Primary audience |
|---|---|---|---|---|---|---|
| **F1: See everything** |||||||
| 1 | Nine terminal tabs, no idea which session is which. | One local board listing every coding-agent session on the machine. | Scan the whole fleet in seconds. | S-OVR, V-01 | Built | Anyone past 3 parallel sessions |
| 2 | You resume a session by hand and your fancy dashboard goes blind. | CCC reads each engine's on-disk state as truth; hand-launched and hand-resumed sessions appear automatically. | No workflow changes; the board follows you. | V-02, S-F1a | Built | Users burned by launch-through-me tools |
| 3 | Old sessions pile up invisibly; you rediscover forgotten work weeks later. | Full inventory including dormant and archived sessions, with time-gap markers. | Nothing is silently lost. | S-F1b | Built | Solo builders |
| 4 | Work split across Claude Code, Codex, Cursor, Antigravity, and Kilo Code, each with its own window. | One board across engines: spawn and monitor all five from the same UI. | Engines become a detail, not a workspace boundary. | S-F1c, V-01 | Partial | Multi-engine users |
| 5 | Closing the dashboard feels dangerous, so a tab stays open forever. | Sessions run independently; close the board, reopen tomorrow, everything reattaches. | The dashboard is a lens, not a runtime. | V-02 | Built | Everyone |
| **F2: Know what needs you** |||||||
| 6 | An agent asked "want me to proceed?" 40 minutes ago and you never saw it. | Attention detection flags sessions ending on a real question, including plain-prose ones, plus desktop notifications. | Waiting-on-you work surfaces itself. | S-F2a, V-03 | Built | Everyone |
| 7 | A session dies mid-task because its context window silently filled. | Per-session context meter with warning and danger levels; click through to compact. | See exhaustion coming before it costs a session. | S-F2b, V-04 | Built | Heavy session users |
| 8 | No idea how close you are to your plan's rate limits. | Usage windows for Claude plans and Codex limits, with pace projections. | Budget the day instead of hitting the wall. | S-F2c | Built | Plan subscribers |
| 9 | Every session runs the same model whether the task needs it or not. | Model advisor scores transcript reasoning load and suggests up- or downgrades. | Spend expensive tokens where they matter. | S-F2d | Built | Cost-conscious users |
| 10 | You cannot say what your agent fleet actually produced this week. | Throughput view: token-weighted output over time, per engine, week by week. | An honest production record. | S-F2e | Built | Solo builders, leads |
| **F3: Organize work that outgrew a flat list** |||||||
| 11 | Strategy sessions sink under a pile of execution sessions. | Pin sessions to the top; spawned sub-sessions nest underneath them. | Strategy stays visible while execution fans out. | S-F3a, V-05 | Built | Orchestrators |
| 12 | Thirty rows in a flat list mean nothing. | Kanban states (Working, Review, In Testing, Verified, Archived) with drag-and-drop and multi-select. | State of the whole operation at a glance. | S-F3b, V-06 | Built | Everyone |
| 13 | Sessions belong to projects and features, not just columns. | Project tree: group sessions under nestable named objects. | A day map that matches how you actually think. | S-F3c | Built | Multi-project users |
| 14 | You want the whole operation laid out spatially, like a whiteboard. | Flow canvas: drag repos, sessions, group chats, and objects on an infinite zoomable board with edges. | The fleet becomes a picture you arrange once. | S-F3d, V-07 | Built | Visual thinkers |
| 15 | Comparing two transcripts means window Tetris. | Split pane: two conversations side by side, each with its own input bar. | Review and steer two sessions at once. | S-F3e, V-08 | Built | Reviewers |
| 16 | "What did I decide about this last week?" takes minutes of grep. | Search across all session history from the sidebar. | Prior decisions in seconds. | S-F3f, V-09 | Built | Everyone |
| 17 | Auto-generated session titles are useless slugs. | One-click AI-regenerated titles. | A board you can actually read. | S-F3g | Built | Everyone |
| **F4: Steer many agents without orchestration code** |||||||
| 18 | A dormant session needs one more instruction; reopening a terminal is a chore. | Type into any session from the browser; dormant ones auto-resume to receive it. | Steer from one place, no terminal hunting. | V-10, S-F4a | Built | Everyone |
| 19 | You want agents to discuss and divide work, without writing an orchestrator. | Group chats: multiple sessions share a chat and are auto-pinged to respond in turn. | Coordination as a conversation, not code. | S-F4b, V-11 | Built | Multi-agent users |
| 20 | One agent needs another agent's answer right now. | Sessions can ask a sibling synchronously over a local API and get the reply back. | Agent-to-agent Q&A becomes one call. | S-F4c | Built | Advanced orchestrators |
| 21 | Headless spawning means losing the ability to follow up. | Dashboard-spawned headless sessions keep input open; keep typing at them from the browser. | Fire-and-forget becomes fire-and-steer. | V-10 | Built | Automation users |
| 22 | You retype the same kickoff prompt for every new session. | Template gallery for reusable spawn prompts. | Consistent kickoffs in one click. | S-F4d | Built | Repeat spawners |
| **F5: Let work run unattended** |||||||
| 23 | You want to drop tickets in a queue and have agents drain it overnight. | Work queue with claim, fix, verify, close lifecycle and bound agent workers. | Wake up to closed tickets. | S-F5a, V-12 | Built | Automation users |
| 24 | A worker got stuck an hour ago and nothing noticed. | Queue-health watcher flags stuck queues from ground truth and nudges workers automatically. | The queue polices itself. | S-F5b | Built | Automation users |
| 25 | Filing a bug from your running app takes ten steps. | Annotate mode: click an element in your app, type a note, it becomes a queue ticket. | See bug, click bug, queue bug. | V-13, S-F5c | Built | App builders |
| 26 | A GitHub issue should become a working session with one click. | Issue board: one click spawns a session on the issue; verify closes it with a commit-SHA comment. | Issues in, verified closures out. | S-F5d, V-14 | Built | OSS maintainers |
| 27 | Parallel agents on one repo clobber each other's working tree. | One-click fresh git worktrees per task, with optional repo init scripts. | True isolation without ceremony. | S-F5e | Built | Parallel workers |
| 28 | Production breaks while you sleep. | Optional Vercel watch: new failed prod deploys auto-spawn a fix session, deduped by commit. | The 3am pager becomes a morning PR. | S-F5f | Partial | Vercel users |
| **F6: Work from anywhere** |||||||
| 29 | You stepped away; the fleet kept going; your phone shows nothing. | Responsive mobile UI: monitor and steer sessions from a phone on your network. | Check the fleet from the couch. | M-01, M-02, V-15 | Built | Everyone |
| 30 | The session you need lives on the other machine. | Continue-on-another-machine: atomic handoff of a session, repo state and transcript included, to a paired peer. | Pick up exactly where the desktop left off. | S-F6a | Built | Multi-machine users |

Row count: 30 public rows (28 Built, 2 Partial with stated qualifications).

Qualifications that MUST accompany the two Partial rows:
- Row 4 (multi-engine): Claude Code is first-class. Codex, Cursor, Antigravity,
  and Kilo Code spawn and ingest with documented gaps (Kilo has no follow-up;
  Cursor sync is metadata-only by design). Say "one board for five engines,"
  never "identical support for five engines."
- Row 28 (auto-fix deploys): opt-in, requires the Vercel CLI and env config.

---

## 3. Evidence ledger

Working ledger behind the table above, including capabilities NOT presented
publicly and why. Statuses per the header. "Claim risk" is the cost of getting
it wrong publicly.

| Capability | Evidence source | Status | Public-safe proof | Claim risk | Decision |
|---|---|---|---|---|---|
| Attach-not-own session discovery (on-disk state as truth) | `docs/session-attach.md`; `/api/sessions`; hooks in `~/.claude/settings.json` | Built | Demo bundle + video | Low | Lead differentiator |
| Kanban board, drag-drop, multi-select | `docs/kanban-rules.md`; app.js board renderer | Built | Demo bundle | Low | Public, F3 |
| List and board and Flow view modes | app.js `sidebarViewMode` | Built | Demo bundle | Low | Public, F3 |
| Flow canvas (zoom, pan, edges, popout) | `docs/flow-workspace.md`; `/api/flow/*` | Built | Demo bundle | Low | Public, F3 |
| Project tree / objects | `docs/objects-api.md`; `/api/objects` | Built | Demo bundle | Low | Public, F3 |
| Split-pane dual transcripts | README; app.js split pane | Built | Demo bundle | Low | Public, F3 |
| Context meter per session | app.js `context_pct` badge | Built | Demo bundle | Low | Public, F2 |
| Usage / rate-limit windows + pace | `/api/usage/current`, `/api/plan-usage` | Built | Screenshot (synthetic data) | Low | Public, F2 |
| Throughput analyzer | `/api/throughput*` | Built | Screenshot (synthetic data) | Low | Public, F2 |
| Attention detection incl. prose questions | `docs/attention-api.md`; server scoring | Built | Demo bundle (question-waiting rows) | Low | Public, F2 |
| Desktop notifications | `hooks/stop.py`, osascript | Built (macOS only) | Described, not captured | Medium | Public with "macOS" qualifier |
| Group chat + auto-ping | `docs/group-chat-pinging.md`; `/api/group-chat*` | Built | Demo fixtures include group chats | Low | Public, F4 |
| Synchronous sibling ask | `/api/ask`; ccc-orchestration skill | Built | Screenshot of docs/API | Low | Public, F4 (advanced) |
| Headless spawn + browser follow-up | `/api/sessions/spawn`; session-attach doc | Built | Video on local server | Low | Public, F4. Note: input pipe dies on server restart; do not claim durability across restarts. |
| Inject into dormant session (auto-resume) | `/api/inject-input`; README | Built | Video on local server | Low | Public, F4 |
| Template gallery | `/api/template-gallery/open` | Built | Screenshot | Low | Public, F4 |
| Work queue (claim/fix/verify/close) | `ux_fixes_queue.py`; `/api/ux-fixes/*` | Built | Screenshot (synthetic tickets) | Low | Public, F5. Public name: "work queue." "Watchtower" is a separate product name; use sparingly. |
| Queue-health watcher + auto-nudge | `/api/ux-fixes/health`; product manifest | Built | Screenshot of health strip | Low | Public, F5 |
| Annotate element to queue ticket | `/api/annotations*`; cookbook doc | Built | Video on local server | Low | Public, F5 |
| Bug-report widget to GitHub issue | `/api/bug-report*`; cookbook doc | Built | Screenshot | Low | Ledger only; fold under annotate story to avoid crowding |
| GitHub issue to session to verified close | `/api/issues*`; README | Built | Demo fixtures include issues | Low | Public, F5. Requires `gh` CLI; say so on install surfaces. |
| One-click worktrees + init scripts | `/api/repo/worktrees`; `docs/worktree-init.md` | Built | Screenshot | Low | Public, F5 |
| Auto-fix Vercel deploys | `/api/vercel-deploy`; README | Partial (opt-in, needs Vercel CLI) | Screenshot | Medium | Public with qualification |
| Dev-server start/stop + ship flow | `/api/nextjs/*`, `/api/repo/ship*` | Built | Screenshot | Low | Ledger only; niche for homepage |
| AI-regenerated titles | README; title regen via haiku | Built | Before/after screenshot | Low | Public, F3 |
| Model advisor | `model_advisor.py`; `/api/model-advisor*` | Built | Screenshot | Low | Public, F2 |
| Mobile responsive UI | app.css media queries; mobile back nav | Built | Mobile screenshots | Low | Public, F6 |
| Cross-machine handoff | `/api/federation/handoff/*` (v5.7-dev, committed) | Built | Screenshot of handoff UI | Medium | Public, F6. New; recapture when released. |
| Federation peers / nodes modal | `federation.py`; `/api/federation/*` | Built | Screenshot | Medium | Ledger only; power-user depth, keep off homepage |
| Remote sessions over SSH | `ssh_multiplexer.py`; `CCC_SSH_HOST` | Experimental | None | High | NOT public. Revisit when stable. |
| ACP adapter (editors / external agents) | `ccc_acp.py`; README | Built (optional extra) | None planned | Medium | Ledger only; developer-docs material |
| Read-only demo with seeded data | app.js `installDemoMode`; `docs/demo/` | Built | Is itself proof | Low | Public CTA |
| Search history (+ Total Recall) | `/api/search-history`; server integration | Built (TR optional) | Demo/local capture | Low | Public, F3; do not imply Total Recall ships with CCC |
| Telemetry opt-in, off by default | `docs/telemetry.md`; `/api/telemetry/*` | Built | telemetry.md link | Low | Trust signal, keep |
| Self-update from UI | `/api/self-update` | Built | Screenshot | Low | Ledger only |
| Onboarding + in-UI Claude login | `/api/onboarding/*` | Built | Screenshot | Low | Ledger only |
| System health strip + zombie reaper | `/api/system-health*` | Built | Screenshot | Low | Ledger only |
| Jump-to-terminal, open-in-Desktop/Codex app | osascript + deep links | Built (macOS only) | Screenshot | Medium | Ledger only; macOS qualifier required |
| Windows native install (PowerShell) | CHANGELOG 5.6.0; `scripts/install.ps1`, `run.ps1` | Built (recent) | Install docs | Medium | Public on install surfaces. Fix stale compare-table cell that says macOS+Linux only. |
| Codex engine | `/api/sessions/spawn-codex`; CHANGELOG 5.6.0 | Partial | Demo shows codex rows | Medium | Public within row 4 qualification |
| Cursor engine | `/api/sessions/spawn-cursor` | Partial | Screenshot | Medium | Public within row 4 qualification |
| Antigravity engine | `/api/sessions/spawn-antigravity` | Built per README | Demo/screenshot | Medium | Public within row 4 qualification |
| Kilo Code engine | `/api/sessions/spawn-kilo` | Partial (no follow-up) | Screenshot | Medium | Public within row 4 qualification |
| Hermes engine | `/api/sessions/spawn-hermes`; composer UI | Built, undocumented | None | High | NOT public until documented in README |
| Pkood integration | `/api/pkood/*` | Experimental | None | High | NOT public |
| Scheduled / cron agent jobs | none (manifest lists as planned) | Planned | None | High | NEVER claim. Periodic triggers are internal pollers, not user cron. |
| Car mode / voice operator | `ccc-voice/` gitignored; launcher UI public | Private-only | None | High | NEVER in public copy. Public installs cannot use it. |
| Morning view planner | `morning.py` gitignored | Private-only | None | High | NEVER in public copy |
| COO board sidecar | `static/coo-notes.json` gitignored | Private-only | None | High | NEVER in public copy |

### Site claims to retire or fix (found during audit, 2026-07-10)

1. Version pill says v5.5; latest release is v5.6.0. Fix.
2. Compare table says CCC is "macOS + headless Linux" while hero and install
   show Windows. Windows native install shipped in v5.6.0. Fix the table.
3. "Source you can read in an afternoon" overstates: `app.js` is about 1 MB and
   `server.py` about 59k lines. Reframe honestly: no build step, no runtime
   dependencies, two files, stdlib only. Do not claim afternoon-readability.
4. "Workers: Zero. No background jobs" sits on the same page as the Watchtower
   watcher card. Reframe: no required background jobs; the queue watcher is
   part of the optional queue feature.
5. "What's New" hand-coded at v5.5 / June 2026. Refresh and clearly date it.

---

## 4. Asset ledger

Every public asset gets a row before capture and a completed row after.
Source of data for ALL captures: the seeded demo bundle (`docs/demo/`, fake
data, privacy-enforced by `tests/test_demo_fixtures.py`) or a synthetic-HOME
local server. Never a real working environment.

Conventions: 1440x900 desktop viewport, 390x844 mobile viewport, PNG for
stills, MP4 (H.264) + poster PNG for videos, 8 to 25 seconds, muted, cursor-led.
Files live in `docs/product-story/assets/` (`shots/`, `video/`, `mobile/`,
`crops/`). Recapture trigger: any UI change to the captured surface, or a major
release.

| Asset ID | Kind | Pain rows | What it must show | Capture setup | Status |
|---|---|---|---|---|---|
| S-OVR | Screenshot | 1 | Full dashboard, mixed live/PR/waiting rows, 3 repos | Demo bundle, board view | Planned |
| S-F1a | Screenshot | 2 | Board with terminal-launched and dashboard-launched rows coexisting | Demo bundle | Planned |
| S-F1b | Screenshot | 3 | Time-gap markers and dormant/archived rows | Demo bundle | Planned |
| S-F1c | Screenshot | 4 | Engine badges across Claude/Codex/Gemini rows | Demo bundle | Planned |
| S-F2a | Screenshot | 6 | Needs-attention lane with question-waiting session | Demo bundle | Planned |
| S-F2b | Screenshot | 7 | Context meters incl. one in danger zone | Demo bundle | Planned |
| S-F2c | Screenshot | 8 | Usage windows and pace | Local server, synthetic | Planned |
| S-F2d | Screenshot | 9 | Model advisor recommendation | Local server, synthetic | Planned |
| S-F2e | Screenshot | 10 | Throughput week view | Local server, synthetic | Planned |
| S-F3a | Screenshot | 11 | Pinned strategy rows with nested sub-sessions | Demo bundle | Planned |
| S-F3b | Screenshot | 12 | Kanban with populated columns | Demo bundle | Planned |
| S-F3c | Screenshot | 13 | Project tree with objects | Demo bundle + localStorage seed | Planned |
| S-F3d | Screenshot | 14 | Flow canvas with arranged nodes and edges | Demo bundle + localStorage seed | Planned |
| S-F3e | Screenshot | 15 | Split pane, two transcripts | Demo bundle | Planned |
| S-F3f | Screenshot | 16 | Search results across history | Demo bundle | Planned |
| S-F3g | Screenshot | 17 | Titles before/after regeneration | Composite of two captures | Planned |
| S-F4a | Screenshot | 18 | Composer typing into dormant session | Demo bundle | Planned |
| S-F4b | Screenshot | 19 | Group chat with multiple agent participants | Demo bundle | Planned |
| S-F4c | Screenshot | 20 | Sibling-ask flow (docs or UI) | Docs render | Planned |
| S-F4d | Screenshot | 22 | Template gallery | Demo bundle or local | Planned |
| S-F5a | Screenshot | 23 | Queue board with tickets in states | Local server, synthetic tickets | Planned |
| S-F5b | Screenshot | 24 | Health strip incl. stuck flag | Local server, synthetic | Planned |
| S-F5c | Screenshot | 25 | Annotate overlay on an app element | Local server | Planned |
| S-F5d | Screenshot | 26 | Issue cards with spawn action | Demo bundle (has issues) | Planned |
| S-F5e | Screenshot | 27 | Worktree spawn modal | Demo bundle or local | Planned |
| S-F5f | Screenshot | 28 | Deploy watch / fix-deploy session | Local server, synthetic | Planned |
| S-F6a | Screenshot | 30 | Handoff UI (continue on another machine) | Local server | Planned |
| M-01 | Mobile shot | 29 | Session list on phone viewport | Demo bundle, 390x844 | Planned |
| M-02 | Mobile shot | 29 | Open conversation on phone viewport | Demo bundle, 390x844 | Planned |
| V-01 | Video | 1, 4 | Scan the fleet: list, board, engines, live rows | Demo bundle | Planned |
| V-02 | Video | 2, 5 | Close board, sessions continue, reopen and reattach | Local server or demo narrative | Planned |
| V-03 | Video | 6 | Spot question-waiting session, open it, answer | Demo bundle | Planned |
| V-04 | Video | 7 | Find the session nearly out of context via meters | Demo bundle | Planned |
| V-05 | Video | 11 | Pin a strategy session, see sub-sessions nest | Demo bundle | Planned |
| V-06 | Video | 12 | Drag card across kanban states | Demo bundle | Planned |
| V-07 | Video | 14 | Flow canvas pan, zoom, drag, edge | Demo bundle + seed | Planned |
| V-08 | Video | 15 | Drag session into split pane, both inputs live | Demo bundle | Planned |
| V-09 | Video | 16 | Search a phrase, land in the right session | Demo bundle | Planned |
| V-10 | Video | 18, 21 | Type into a dormant/headless session from browser | Local server | Planned |
| V-11 | Video | 19 | Group chat thread with agents responding | Demo bundle | Planned |
| V-12 | Video | 23 | Queue: ticket claimed, fixed, verified | Local server, synthetic | Planned |
| V-13 | Video | 25 | Annotate an element, ticket appears in queue | Local server | Planned |
| V-14 | Video | 26 | Issue card to spawned session | Demo bundle where possible | Planned |
| V-15 | Video | 29 | Phone-width walkthrough | Demo bundle, mobile viewport | Planned |

Priority order for the ten most important videos: V-01, V-02, V-03, V-06, V-08,
V-07, V-11, V-04, V-09, V-15. The rest are stretch.

Privacy review checklist per asset (run before an asset is marked Done):
visible text, filenames, metadata, captions, and alt text contain no real
usernames (except the public `amirfish1` GitHub handle where intentional), no
real repo names outside this project, no `/Users/...` paths, no emails, no
tokens, no private conversation content.
