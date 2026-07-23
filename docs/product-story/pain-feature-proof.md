# CCC pain-to-proof inventory

Canonical source of truth for what CCC solves, what proves it, and what may be
claimed publicly. Every public page, post, or video derives its claims from
this file. If a claim is not here with status **Built**, it does not ship.

- Verified against: source at v5.7.0-dev, latest release v5.6.0 (2026-07-08).
  Family/row restructuring pass verified against source 2026-07-18 (rows 12,
  16, 19, 21, 22, 25, 27, 30 re-checked live against a running instance and
  code).
- Verification method: claims checked against `server.py` routes, `static/app.js`
  surfaces, `README.md`, `CHANGELOG.md`, feature docs under `docs/` and
  `cookbook/`, and — rows 22 and 24 — the separate, public Watchtower project
  (github.com/amirfish1/watchtower). Confirmed 2026-07-18 via `scripts/install.sh`
  (`install_watchtower`, "WT-26: bundle WT as CCC's queue engine"): the
  standard CCC installer installs Watchtower by default as CCC's queue engine;
  if it can't be found locally or `pip install` fails, install falls back to
  CCC's own built-in queue engine and the Watchtower-backed rows below do not
  apply. Claimable as a default CCC capability, not an optional add-on — but
  still Partial because of that fallback path.
- Last full audit: 2026-07-10. Family restructuring + row-level revalidation
  pass: 2026-07-18 (maintainer + AI-assisted review). F1/F4/F5 family copy and
  the Watchtower-bundling correction: 2026-07-18 (second pass, independently
  re-verified against source).

Statuses: **Built** (claimable without qualification), **Partial** (claimable
only with the listed qualification), **Experimental**, **Planned**, **Obsolete**,
**Private-only** (never in public copy).

---

## 1. Problem families

Six families. Every public claim belongs to exactly one.

| ID | Family | The moment it names |
|---|---|---|
| F1 | **Scale beyond one session** | The way you build faster is a session per workstream — the feature, its go-to-market, the next feature, research. You end up with more sessions than you realize, and a raw single-tool list doesn't scale. |
| F2 | **Stop wasting tokens, keep quality where it matters** | Without a way to route work by tier, your frontier model does trivial edits, you hit your plan's wall on the wrong work, and the hard problems get a worse model. |
| F3 | **Sessions that exchange context, ideas, and decisions — on their own** | Two sessions on one goal, each making decisions about the same code, and nothing tells either of them; without a mechanism, you're the mechanism. |
| F4 | **Workers that specialize over time** | A worker that solved this exact class of ticket last week starts from zero this week, because nothing it learned survives past one session. |
| F5 | **Find anything, from any session** | You solved this exact problem two weeks ago in some other session, but you can't find it — so you solve it again. |
| F6 | **Work from anywhere** | Your phone as a client to the fleet, and CCC on any machine you can reach — a VM, a home server — on your trusted network. |

Re-lettered 2026-07-18 (second pass, maintainer decision): the former
"IDE for your fleet" family is dissolved — its organization rows (11–15, 17)
fold into F1, because organization is part of the scale-beyond-one-session
story, and its search row (16) becomes F5's single row. Former F4→F3,
former F5→F4. Row NUMBERS are unchanged throughout.

Row 12 is F1's organization headline (Active/All/Issues/Queues). Rows 11,
13, 14, 15, and 17 are supporting proof for F1's broader claim — a board
built for the scaled state in ways a single-model tool isn't — not
point-by-point feature-parity comparisons against any one competitor.
Full family pain/solution copy (the marketing source text) lives in
`message-architecture.md` section 5.

---

## 2. Canonical pain table

The public-facing story. 30 rows, each verified. "Proof asset" IDs resolve in
the asset ledger (section 4). Row numbers are unchanged from the 2026-07-10
audit so existing asset-ledger cross-references still resolve; family
membership was reorganized 2026-07-18.

| # | Painful moment | CCC solution | User outcome | Proof asset | Status | Primary audience |
|---|---|---|---|---|---|---|
| **F1: Scale beyond one session** |||||||
| 1 | Nine terminal tabs, no idea which session is which. | One local board listing every coding-agent session on the machine. | Scan the whole fleet in seconds. | S-OVR, V-01 | Built | Anyone past 3 parallel sessions |
| 2 | You resume a session by hand in a terminal, and a dashboard that only tracks sessions it launched itself doesn't know it exists. | CCC reads each engine's on-disk state as truth; hand-launched and hand-resumed sessions appear automatically. | No workflow changes; the board follows you. | V-02, S-F1a | Built | Users burned by launch-through-me tools |
| 3 | Sessions go dormant for days while you're heads-down elsewhere; nothing resurfaces them, so you forget they exist until you stumble on the branch. | Full inventory including dormant and archived sessions, with time-gap markers. | Nothing is silently lost. | S-F1b | Built | Solo builders |
| 4 | Work split across Claude Code, Codex, Cursor, Antigravity, and Kilo Code, each with its own window. | One board across engines: spawn and monitor all five from the same UI. Claude Code is first-class; Codex, Cursor, Antigravity, and Kilo Code spawn and ingest with documented gaps (Kilo has no follow-up; Cursor sync is metadata-only). | One board for five engines — not identical support for five engines. | S-F1c, V-01 | Partial | Multi-engine users |
| 5 | You're afraid closing CCC's own board will kill the sessions running underneath it, so you leave the tab open forever. | Sessions run independently; close the board, reopen tomorrow, everything reattaches. | The dashboard is a lens, not a runtime. | V-02 | Built | Everyone |
| 6 | An agent asked "want me to proceed?" 40 minutes ago and you never saw it. | Attention detection flags sessions ending on a real question, including plain-prose ones, plus desktop notifications (macOS only). | Waiting-on-you work surfaces itself. | S-F2a, V-03 | Built | Everyone |
| 7 | A session dies mid-task because its context window silently filled. | Per-session context meter with warning and danger levels; click through to compact. | See exhaustion coming before it costs a session. | S-F2b, V-04 | Built | Heavy session users |
| 11 | Strategy sessions sink under a pile of execution sessions. | Pin sessions to the top; spawned sub-sessions nest underneath them. | Strategy stays visible while execution fans out. | S-F3a, V-05 | Built | Orchestrators |
| 12 | Thirty sessions with no way to tell what's actually on your plate today versus everything that's ever happened versus structured backlog work. | Active tab (today's focus), All tab (full history), plus Issues and Queues tabs for backlog and queue management — archive when done, no multi-stage workflow to maintain. | See what's in play right now without policing a state machine. | S-F3b, V-06 | Built | Everyone |
| 13 | Sessions belong to projects and features, not just columns. | Project tree: group sessions under nestable named objects. | A day map that matches how you actually think. | S-F3c | Built | Multi-project users |
| 14 | You want the whole operation laid out spatially, like a whiteboard. | Flow canvas: drag repos, sessions, group chats, and objects on an infinite zoomable board with edges. | The fleet becomes a picture you arrange once. | S-F3d, V-07 | Built | Visual thinkers |
| 15 | Comparing two transcripts means window Tetris. | Split pane: two conversations side by side, each with its own input bar. | Review and steer two sessions at once. | S-F3e, V-08 | Built | Reviewers |
| 17 | Auto-generated session titles are useless slugs. | One-click AI-regenerated titles, via your already-authenticated local `claude` CLI (no separate API key needed). | A board you can actually read. | S-F3g | Built | Everyone |
| **F2: Stop wasting tokens, keep quality where it matters** |||||||
| 8 | No idea how close you are to your plan's rate limits until you hit the wall mid-week. | Usage windows for Claude plans and Codex limits, with pace projections against elapsed working hours. | Budget the day instead of hitting the wall. | S-F2c | Built | Plan subscribers |
| 9 | You're routing work across multiple models to save tokens, but deciding by hand which session needs the expensive model and which doesn't. | For Claude sessions, a cheap transcript heuristic (no LLM call) suggests an up- or downgrade; it abstains rather than guessing on unclear cases, and doesn't score non-Claude engines. | A second opinion on model choice, worth a glance before you apply it. | S-F2d | Partial | Cost-conscious users |
| 10 | An automation, queue, or spawned sub-session burned through your plan while you weren't watching it — you find out from the usage number, not from anything that told you at the time. | Throughput view attributes token spend per engine, project, and session, so a spike is traceable to its source. | Leaks get a name, not just a number. | S-F2e | Built | Solo builders, leads |
| **F3: Sessions that exchange context, ideas, and decisions — on their own** |||||||
| 18 | A dormant session needs one more instruction, and first you have to find which one — reopening a terminal to hunt for it is a chore. | Type into any session from the browser; search finds the dormant one, dormant ones auto-resume to receive it (all engines except Kilo Code, which has no follow-up support — see row 4). | Steer from one place, no terminal hunting. | V-10, S-F4a | Partial | Everyone |
| 19 | Parallel sessions working related tasks have no way to reach each other except a shared file neither is watching. | Group chats give sessions a live channel: post once, every participant gets pinged and reads the thread on its own schedule. | Coordination as a conversation, not a file no one's polling. | S-F4b, V-11 | Built | Multi-agent users |
| 20 | One agent needs another agent's answer right now. | Sessions can ask a sibling synchronously over a local API and get the reply back. | Agent-to-agent Q&A becomes one call. | S-F4c | Built | Advanced orchestrators |
| 21 | Headless spawning means losing the ability to follow up — and losing track of whether the task even finished. | Dashboard-spawned headless sessions keep input open for follow-up; spawned children are instructed to report success or failure back to the session that dispatched them. | Fire-and-forget becomes fire-and-know, when the child has the tool access to send the report. | V-10 | Partial | Automation users |
| **F4: Workers that specialize over time** |||||||
| 22 | A fresh worker starts a queue session from zero, repeating mistakes prior workers on the same queue already solved. | Each worker reads that queue's learnings file at session start as a cold-start brief, and updates it once when the queue drains — not on every ticket close. Ships via Watchtower, installed by default as CCC's queue engine. | Each new worker session starts a little smarter than the last, not from zero. | S-F4e | Partial | Automation users, repeat spawners |
| 23 | Babysitting every running session defeats the point of delegating work — you want to think about the product, not supervise execution. | Work queue with claim, fix, verify, close lifecycle and bound agent workers; tickets carry live state (needs-input, unresolved, stuck) so you check in only when something needs you. | Attention goes to the product; the queue handles the execution. | S-F5a, V-12 | Built | Automation users |
| 24 | When something in the queue does stall, silence is the default — nothing distinguishes "working normally" from "stuck an hour ago." | Queue-health watcher flags stuck queues from ticket and worker ground truth; Watchtower, installed by default as CCC's queue engine, nudges stalled workers back into motion. | A stall gets noticed the same day, not discovered later. | S-F5b | Partial | Automation users |
| 25 | Filing a bug from your running app takes ten steps — leave the app, open an issue tracker, describe what you saw from memory. | The annotate API is built in; add the small cookbook widget to your own app once, and clicking an element becomes a queue ticket immediately — already wired into CCC itself and into production on a second app. | See bug, click bug, queue bug. | V-13, S-F5c | Partial | App builders |
| 26 | A GitHub issue should become a working session with one click. | Issue board: one click spawns a session on the issue; verify closes it with a commit-SHA comment. Requires `gh` authenticated. | Issues in, verified closures out. | S-F5d, V-14 | Partial | OSS maintainers |
| 27 | Parallel agents on one repo clobber each other's working tree — or, when they do need to land on the same branch, nobody wants to coordinate the merge by hand. | One-click fresh git worktrees per task for isolation; when sessions do share one checkout, "Push all" asks each session to finish and acknowledge, then validates and pushes — pausing safely for you to review on conflicts or divergence rather than forcing it. | True isolation without ceremony, and a safe hand-off when sessions do share a branch. | S-F5e, V-18 | Built | Parallel workers |
| 28 | A production deploy fails and nobody's watching for it. | Optional Vercel watch (opt-in, requires the Vercel CLI and env config): new failed prod deploys auto-spawn a session, deduped by commit, when CCC's dashboard polls that repo's deploy status. The spawned session is not yet given the actual failure logs or deploy URL — it has to investigate independently. | A failed deploy gets a session started on it, not silence — while the dashboard is checking, though it starts without the error details. | S-F5f | Partial | Vercel users |
| **F5: Find anything, from any session** |||||||
| 16 | "What did I decide about this last week?" takes minutes of grep. | Full-text search across Claude Code and Codex session history, built in — no setup, no external tool (Hermes indexing is in progress, not yet merged in). An optional deeper semantic mode (local embeddings) is available for paraphrase-level recall. | Prior decisions in seconds, out of the box, for the engines it covers today. | S-F3f, V-09 | Partial | Everyone |
| **F6: Work from anywhere** |||||||
| 29 | You step away from the desk and want to keep working — check status, answer a question, nudge a session — from your phone. | Responsive mobile UI. A phone is a separate device, so reaching it needs the same trusted-network opt-in as row 30 (`CCC_TRUST_TAILNET=1` or an explicit allowed origin) — never expose CCC to an open network to get this. | Keep working away from the desk, on a network you trust. | M-01, M-02, V-15 | Built | Everyone |
| 30 | The fleet you want to reach lives on a machine that isn't the one in front of you — a home server, a VM, a box you left running. | CCC binds to loopback only by default for safety. Opting into a trusted-network path (`CCC_TRUST_TAILNET=1` on your tailnet) lets you install it on that machine and reach it from any browser on your trusted network — never the open internet or an untrusted LAN. | Your coding power follows your trusted network, not your desk. | S-F6a | Built | Multi-machine users |

Row count: 30 public rows (20 Built, 10 Partial with stated qualifications).

Qualifications that MUST accompany the ten Partial rows:
- Row 4 (multi-engine): Claude Code is first-class. Codex, Cursor, Antigravity,
  and Kilo Code spawn and ingest with documented gaps (Kilo has no follow-up;
  Cursor sync is metadata-only by design). Say "one board for five engines,"
  never "identical support for five engines."
- Row 9 (model advisor): a cheap transcript heuristic, confirmed 2026-07-18
  per its own code comment — right ~80% of the time on clear cases, abstains
  on unclear ones rather than guessing, and returns no recommendation for
  non-Claude engines. Frame as a second opinion to review, not an automated
  optimization result.
- Row 16 (search): confirmed 2026-07-18 via a `TODO(hermes-search)` comment
  in source — Hermes transcripts are not yet merged into search results.
  Currently covers Claude Code and Codex; say so, don't claim "all history."
- Row 18 (steer dormant sessions): works for every engine except Kilo Code,
  confirmed 2026-07-18 — no `resume_session_kilo` exists anywhere in the
  resume dispatch chain. Consistent with row 4's existing "Kilo has no
  follow-up" qualifier.
- Row 26 (issue to session): requires `gh` authenticated on the machine.
- Row 21 (report-back on completion): a cooperative prompt-injected instruction
  (the spawned agent runs a `curl` to CCC's own API), not a platform-enforced
  guarantee. Requires the spawned agent to have local network access and
  shell/tool permissions; fails silently under a sandboxed or restricted
  child. Do not claim it as certain.
- Row 22 (per-queue learnings file): built and real, in the separate, public
  Watchtower project. Confirmed 2026-07-18: `scripts/install.sh` installs
  Watchtower by default as CCC's queue engine (falls back to CCC's own
  built-in queue engine only if Watchtower can't be found or fails to
  install). Claimable as a default CCC capability, not an optional add-on —
  still Partial because that fallback path exists.
- Row 24 (queue-health watcher + auto-nudge): CCC itself only flags stuck
  queues (`compute_ux_fixes_health`); confirmed 2026-07-18 that the actual
  nudge — resuming a stalled worker — is Watchtower's dispatch-on-enqueue
  path, gated on Watchtower being installed. Same default-bundled framing as
  row 22: claim it as a default CCC behavior, but note the fallback (flagging
  still works without Watchtower; auto-nudge does not).
- Row 25 (annotate mode): the API is built into CCC, but using it in your own
  app requires adding the cookbook widget yourself first — it is not
  automatic for an arbitrary running app.
- Row 28 (auto-fix deploys): opt-in, requires the Vercel CLI and env config;
  the auto-fix check fires when CCC's own dashboard polls deploy status for
  that repo, not an independent background cron; and confirmed 2026-07-18
  the spawned session gets no deploy logs or error output — it must
  investigate independently. This is a real product gap, not just a wording
  issue — worth a ticket to inject Vercel's build/error logs into the prompt.

---

## 3. Evidence ledger

Working ledger behind the table above, including capabilities NOT presented
publicly and why. Statuses per the header. "Claim risk" is the cost of getting
it wrong publicly.

| Capability | Evidence source | Status | Public-safe proof | Claim risk | Decision |
|---|---|---|---|---|---|
| Attach-not-own session discovery (on-disk state as truth) | `docs/session-attach.md`; `/api/sessions`; hooks in `~/.claude/settings.json` | Built | Demo bundle + video | Low | Lead differentiator |
| Kanban board, drag-drop, multi-select | `docs/kanban-rules.md`; app.js board renderer | Built | Demo bundle | Low | Power-user optional view mode, not the organization headline. Amir's own daily use is Active/All + Issues/Queues (row 12); kanban stays real and shippable but is not the public pitch. |
| List and board and Flow view modes | app.js `sidebarViewMode` | Built | Demo bundle | Low | Public, F1 |
| Flow canvas (zoom, pan, edges, popout) | `docs/flow-workspace.md`; `/api/flow/*` | Built | Demo bundle | Low | Public, F1 |
| Project tree / objects | `docs/objects-api.md`; `/api/objects` | Built | Demo bundle | Low | Public, F1 |
| Split-pane dual transcripts | README; app.js split pane | Built | Demo bundle | Low | Public, F1. Note: also exists on Claude Desktop — pitch as a nice supporting feature, not a differentiator. |
| Active / All / Issues / Queues tabs, archive-on-done | app.js tab state (`inprogress`/`archived`/`issues`/`queues`, `static/app.js:27155+`) | Built | Demo bundle | Low | Public, F1. Lead organization story (row 12) within the scale family, replacing kanban as the headline. |
| Context meter per session | app.js `context_pct` badge | Built | Demo bundle | Low | Public, F1 |
| Usage / rate-limit windows + pace | `/api/usage/current`, `/api/plan-usage` | Built | Screenshot (synthetic data) | Low | Public, F2 |
| Throughput analyzer (spend attribution) | `/api/throughput*` | Built | Screenshot (synthetic data) | Low | Public, F2. Framed as leak/attribution, not a weekly vanity report. |
| Attention detection incl. prose questions | `docs/attention-api.md`; server scoring | Built | Demo bundle (question-waiting rows) | Low | Public, F1 |
| Desktop notifications | `hooks/stop.py`, osascript | Built (macOS only) | Described, not captured | Medium | Public with "macOS" qualifier |
| Group chat + auto-ping | `docs/group-chat-pinging.md`; `/api/group-chat*` | Built | Demo fixtures include group chats | Low | Public, F3. Mechanism is ping/notify (wake + read + reply), not context-merging — do not claim agents "agree" or "negotiate," claim they "stay in sync." |
| Synchronous sibling ask | `/api/ask`; ccc-orchestration skill | Built | Screenshot of docs/API | Low | Public, F3 (advanced) |
| Headless spawn + browser follow-up | `/api/sessions/spawn`; session-attach doc | Built | Video on local server | Low | Public, F3. Note: input pipe dies on server restart; do not claim durability across restarts. |
| Inject into dormant session (auto-resume) | `/api/inject-input`; README | Built | Video on local server | Low | Public, F3 |
| Report-back on spawn completion (`report_to`/`return_to`/`reply_to`) | `server.py:38714+`; `_wrap_prompt_with_return_address()` | Partial | Screenshot of docs/API | Medium | Public, F3 (row 21) — confirmed 2026-07-18 the mechanism is a cooperative prompt-injected `curl` instruction, not platform-enforced. Requires the child to have network + shell tool access; silently fails otherwise. Qualify accordingly. |
| Template gallery | `/api/template-gallery/open` | Built | Screenshot | Low | Ledger only; demoted in favor of the per-queue learnings-file story (row 22), which better matches how Amir actually works. |
| Per-queue learnings file | Watchtower `workers.py` ("FIRST, read the queue's learnings file...") — separate public repo, github.com/amirfish1/watchtower, installed by default via `scripts/install.sh` | Partial | Screenshot of learnings file + CLI | Medium | Public, F4 (row 22 — the family's headline row). Confirmed 2026-07-18: Watchtower ships by default as CCC's queue engine; falls back to CCC's built-in queue engine only if missing/failed install. Claim as default CCC behavior; Partial only for that fallback edge case. Public name: "work queue." |
| Work queue (claim/fix/verify/close) | `ux_fixes_queue.py`; `/api/ux-fixes/*` | Built | Screenshot (synthetic tickets) | Low | Public, F4. Public name: "work queue." "Watchtower" is a separate product name; use sparingly. |
| Queue-health watcher + auto-nudge | `/api/ux-fixes/health`; product manifest; Watchtower dispatch-on-enqueue path for the actual nudge | Partial | Screenshot of health strip | Low | Public, F4. Confirmed 2026-07-18: CCC's own code only flags stall from ground truth; the auto-nudge is Watchtower's, installed by default (same fallback caveat as the learnings-file row above). |
| Annotate element to queue ticket | `/api/annotations*`; `cookbook/annotate-to-ux-fixes-queue.md` | Built | Video on local server | Low | Public, F4. Cookbook doc confirmed 2026-07-18; already in production use in CCC itself and in a second, separate commercial app of Amir's. Generalized per AGENTS.md ("generalize or gitignore" default) — do not name the other app in public copy unless Amir explicitly opts in. |
| Bug-report widget to GitHub issue | `/api/bug-report*`; cookbook doc | Built | Screenshot | Low | Ledger only; fold under annotate story to avoid crowding |
| GitHub issue to session to verified close | `/api/issues*`; README | Built | Demo fixtures include issues | Low | Public, F4. Requires `gh` CLI; say so on install surfaces. |
| One-click worktrees + init scripts | `/api/repo/worktrees`; `docs/worktree-init.md` | Built | Screenshot | Low | Public, F4 |
| "Push all" coordinated multi-session commit + push | `server.py:46159+`; `_run_ship_flow()`, `_ship_read_reply()` | Built | Video on local server | Medium | Public, F4 (row 27 addition). Confirmed 2026-07-18: nudges every session on a repo to commit before one coordinated push — real orchestration demo material. Completion detection is a `\bDONE\b` transcript regex on readable (Claude) transcripts only, not a strict verification step; say "nudges," not "guarantees." Also confirmed: non-Claude engines (no readable transcript) are marked "acked" the instant they're nudged, with no actual reply verification — `wait_cap` drops to a flat 15s only when every nudged session is non-Claude. Do not claim equal reliability across engines for this feature. |
| Auto-fix Vercel deploys | `/api/vercel-deploy`; `vercel_deploy_status_with_autofix()` | Partial (opt-in, needs Vercel CLI) | Screenshot | High | Public with qualification. Auto-spawn on new deploy ERROR confirmed real, but confirmed 2026-07-18 that `spawn_session("/fix-deploy", ...)` passes a bare literal string with NO deploy logs, error output, or URL injected — no `fix-deploy` command file exists in the repo. The spawned session must independently investigate the failure. This is a real product gap, not just a docs wording issue — worth a ticket to actually inject Vercel's build/error logs into the spawn prompt before this claim is made stronger than "gets a session started on it." Also confirmed the check runs only when `/api/vercel-deploy` is polled by the dashboard, not an independent cron. |
| Dev-server start/stop + ship flow | `/api/nextjs/*`, `/api/repo/ship*` | Built | Screenshot | Low | Ledger only; niche for homepage |
| AI-regenerated titles | README; title regen via haiku | Built | Before/after screenshot | Low | Public, F1 |
| Model advisor | `model_advisor.py`; `/api/model-advisor*` | Built | Screenshot | Low | Public, F2 |
| Mobile responsive UI | app.css media queries; mobile back nav | Built | Mobile screenshots | Medium | Public, F6. Confirmed 2026-07-18: reaching CCC from a phone requires the same trust-boundary opt-in as row 30 (a phone is never on loopback) — SECURITY.md doesn't call this out for mobile specifically, but the binding rule applies identically. Caveat now embedded in row 29 text directly. |
| CCC on any network-reachable machine (VM, home server) + browser access | `SECURITY.md` — binds to `127.0.0.1` by default; `CCC_TRUST_TAILNET=1` opts into tailnet-scoped reach | Built | Screenshot | Medium | Public, F6 (row 30 headline), MUST carry the loopback-by-default + trust-boundary caveat. Confirmed 2026-07-18: an earlier draft of row 30 contradicted `SECURITY.md`'s explicit "don't expose to the network/LAN/internet" warning by describing this as a plain, uncaveated feature — fixed. Not the SSH-experimental mechanism below. |
| Cross-machine handoff (atomic transfer) | `/api/federation/handoff/*` (v5.7-dev, committed) | Built | Screenshot of handoff UI | Medium | Ledger only; real but niche. F6's public headline is now general network/VM reachability (row 30), not handoff. Recapture if promoted later. |
| Federation peers / nodes modal | `federation.py`; `/api/federation/*` | Built | Screenshot | Medium | Ledger only; power-user depth, keep off homepage |
| Remote sessions over SSH | `ssh_multiplexer.py`; `CCC_SSH_HOST` | Experimental | None | High | NOT public. Revisit when stable. (Confirmed 2026-07-18: distinct from row 30's VM claim, which does not depend on this.) |
| ACP adapter (editors / external agents) | `ccc_acp.py`; README | Built (optional extra) | None planned | Medium | Ledger only; developer-docs material |
| Read-only demo with seeded data | app.js `installDemoMode`; `docs/demo/` | Built | Is itself proof | Low | Public CTA |
| Search history — keyword built-in, semantic optional | `/api/search-history`; `_history_index/` (own module, sqlite-vec + Ollama, independent of Total Recall) | Built | Demo/local capture | Low | Public, F5 (its own family as of 2026-07-18). Keyword (BM25/FTS5) works with zero setup. Semantic mode requires `ollama pull nomic-embed-text` locally and is NOT bundled — confirmed 2026-07-18 that without it, `semantic=true` silently falls back to keyword-only with no error shown. Do not imply semantic-by-default; see site-claims-to-fix item 6. |
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
| COO board sidecar | `static/coo-notes.json` gitignored | Private-only | None | High | NEVER in public copy |

### Site claims to retire or fix (found during audit, 2026-07-10; item 6 added 2026-07-18)

1. Version pill says v5.5; latest release is v5.6.0. Fix.
2. Compare table says CCC is "macOS + headless Linux" while hero and install
   show Windows. Windows native install shipped in v5.6.0. Fix the table.
3. "Source you can read in an afternoon" overstates: `app.js` is about 1 MB and
   `server.py` about 59k lines, plus helper modules (federation.py,
   model_advisor.py, ux_fixes_queue.py and others). Reframe honestly: no build
   step, no runtime dependencies, stdlib only. Do not claim
   afternoon-readability and do not claim a file count.
4. "Workers: Zero. No background jobs" sits on the same page as the Watchtower
   watcher card. Reframe: no required background jobs; the queue watcher is
   part of the optional queue feature.
5. "What's New" hand-coded at v5.5 / June 2026. Refresh and clearly date it.
6. Semantic search silently degrades to keyword-only when the local embedding
   model (`nomic-embed-text`) isn't pulled — no error surfaces to the user.
   Either require the model explicitly with a visible warning, or never imply
   semantic-by-default in copy (say "keyword search, built in; semantic is an
   optional deeper mode").

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

Asset-ID letters (S-F1x, S-F2x, etc.) reflect the ORIGINAL family lettering
at time of ID assignment (2026-07-10) and were left unchanged during the
2026-07-18 family reorganization to avoid needless churn — cross-reference by
the "Pain rows" column, not by the letter in the ID.

| Asset ID | Kind | Pain rows | What it must show | Capture setup | Status |
|---|---|---|---|---|---|
| S-OVR | Screenshot | 1 | Full dashboard, mixed live/PR/waiting rows, 3 repos | Demo bundle, board view | File exists — content match unverified |
| S-F1a | Screenshot | 2 | Board with terminal-launched and dashboard-launched rows coexisting | Demo bundle | Planned |
| S-F1b | Screenshot | 3 | Time-gap markers and dormant/archived rows | Demo bundle | Planned |
| S-F1c | Screenshot | 4 | Engine badges across Claude/Codex/Antigravity rows | Demo bundle | Planned |
| S-F2a | Screenshot | 6 | Needs-attention lane with question-waiting session | Demo bundle | File exists — content match unverified |
| S-F2b | Screenshot | 7 | Context meters incl. one in danger zone | Demo bundle | Planned |
| S-F2c | Screenshot | 8 | Usage windows and pace | Local server, synthetic | File exists — content match unverified |
| S-F2d | Screenshot | 9 | Model advisor recommendation | Local server, synthetic | File exists — content match unverified |
| S-F2e | Screenshot | 10 | Throughput/leak-attribution view, spend traced to source | Local server, synthetic | File exists — content match unverified |
| S-F3a | Screenshot | 11 | Pinned strategy rows with nested sub-sessions | Demo bundle | Planned |
| S-F3b | Screenshot | 12 | Active tab (today's focus) and All tab (archived history) | Demo bundle | Planned (does not exist; not to be confused with S-F3d, which does) |
| S-F3c | Screenshot | 13 | Project tree with objects | Demo bundle + localStorage seed | Planned |
| S-F3d | Screenshot | 14 | Flow canvas with arranged nodes and edges | Demo bundle + localStorage seed | File exists — content match unverified |
| S-F3e | Screenshot | 15 | Split pane, two transcripts | Demo bundle | Planned |
| S-F3f | Screenshot | 16 | Search results across history (keyword mode) | Demo bundle | Planned |
| S-F3g | Screenshot | 17 | Titles before/after regeneration | Composite of two captures | Planned |
| S-F4a | Screenshot | 18 | Composer typing into dormant session | Demo bundle | Planned |
| S-F4b | Screenshot | 19 | Group chat with multiple agent participants | Demo bundle | File exists — content match unverified |
| S-F4c | Screenshot | 20 | Sibling-ask flow (docs or UI) | Docs render | Planned |
| S-F4e | Screenshot | 22 | Learnings file being read by a fresh worker + queue CLI | Watchtower repo, local | Planned |
| S-F5a | Screenshot | 23 | Queue board with tickets in states | Local server, synthetic tickets | File exists — content match unverified |
| S-F5b | Screenshot | 24 | Health strip incl. stuck flag | Local server, synthetic | File exists — content match unverified |
| S-F5c | Screenshot | 25 | Annotate overlay on an app element | Local server | Planned |
| S-F5d | Screenshot | 26 | Issue cards with spawn action | Demo bundle (has issues) | Planned |
| S-F5e | Screenshot | 27 | Worktree spawn modal | Demo bundle or local | Planned |
| S-F5f | Screenshot | 28 | Deploy watch / fix-deploy session | Local server, synthetic | Planned |
| S-F6a | Screenshot | 30 | CCC reachable in a browser from a VM/home-server instance | Local server | Needs recapture — file exists but predates today's row-30 rewrite; a critic reports it currently shows the cross-machine handoff dialog (the OLD row-30 story), not network reachability |
| M-01 | Mobile shot | 29 | Session list on phone viewport | Demo bundle, 390x844 | File exists — content match unverified |
| M-02 | Mobile shot | 29 | Open conversation on phone viewport | Demo bundle, 390x844 | Planned |
| V-01 | Video | 1, 4 | Scan the fleet: list, board, engines, live rows | Demo bundle | File exists — a critic reports it shows only 3 of the 5 claimed engines; verify before public use |
| V-02 | Video | 2, 5 | Close board, sessions continue, reopen and reattach | Local server or demo narrative | Planned |
| V-03 | Video | 6 | Spot question-waiting session, open it, answer | Demo bundle | File exists — content match unverified |
| V-04 | Video | 7 | Find the session nearly out of context via meters | Demo bundle | Planned |
| V-05 | Video | 11 | Pin a strategy session, see sub-sessions nest | Demo bundle | Planned |
| V-06 | Video | 12 | Move a session from Active to archived; switch between Active/All/Issues/Queues tabs | Demo bundle | Needs recapture — an old capture exists at `assets/video/V-06-kanban-drag.mp4` showing the retired kanban-drag content; rename on recapture |
| V-07 | Video | 14 | Flow canvas pan, zoom, drag, edge | Demo bundle + seed | File exists — content match unverified |
| V-08 | Video | 15 | Drag session into split pane, both inputs live | Demo bundle | File exists — content match unverified |
| V-09 | Video | 16 | Search a phrase, land in the right session | Demo bundle | File exists — a critic reports it's a sidebar text-filter demo, not full-history/semantic search proof; verify before public use |
| V-10 | Video | 18, 21 | Type into a dormant/headless session from browser | Local server | Planned |
| V-11 | Video | 19 | Group chat thread with agents responding | Demo bundle | Planned |
| V-12 | Video | 23 | Queue: ticket claimed, fixed, verified | Local server, synthetic | Planned |
| V-13 | Video | 25 | Annotate an element, ticket appears in queue | Local server | Planned |
| V-14 | Video | 26 | Issue card to spawned session | Demo bundle where possible | File exists — a critic reports it's a demo-mode stub with a fake POST, not a real spawn; verify before public use |
| V-15 | Video | 29 | Phone-width walkthrough | Demo bundle, mobile viewport | File exists — content match unverified |
| V-17 | Video | 13 | Project tree / group by objects | Demo bundle | File exists (`V-17-by-objects.mp4`) — was missing from this ledger entirely; added 2026-07-18 |
| V-18 | Video | 27 | Two sessions on one repo; "Push all" coordinates commit + push | Local server, synthetic | Planned — was misassigned to V-16 on 2026-07-18, which collided with a pre-existing `V-16-project-tree.mp4` unrelated to this row; renumbered to a free ID |

Priority order for the ten most important videos: V-01, V-02, V-03, V-06, V-08,
V-07, V-11, V-04, V-09, V-15. The rest are stretch. (V-06 repurposed
2026-07-18 to show Active/All tabs instead of kanban; kanban is no longer the
public F3 story.)

Privacy review checklist per asset (run before an asset is marked Done):
visible text, filenames, metadata, captions, and alt text contain no real
usernames (except the public `amirfish1` GitHub handle where intentional), no
real repo names outside this project, no `/Users/...` paths, no emails, no
tokens, no private conversation content.
