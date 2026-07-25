# Skills ecosystem inventory

An honest map of the Claude Code skill packs a CCC / Watchtower fleet runs
alongside: what each does, how CCC sessions interact with it today, and where it
shines or breaks when many sessions share one machine. This is the research
behind the [CCC works with your skills](skills-ecosystem.html) page. Where a
claim is a proxy or unverified, it says so.

Generated for W86 (2026-07-17). Live counts change; treat numbers as of that
date.

## How CCC relates to a skill, mechanically

Three seams matter, because they decide whether a pack cooperates with a fleet or
just runs blind inside one session:

1. **Does it spawn subagents?** Superpowers and other orchestration packs fan out
   work to `Task`-tool subagents. CCC already detects those: it counts `Task` /
   `Agent` tool-use blocks in the parent transcript and renders a subagent chip
   plus a status rail on the parent session row (`subagent_count`,
   `subagent_in_flight_count`, `subagent_recent` in `server.py`). So a pack that
   spawns subagents is **visible in CCC today** without any change to the pack.
2. **Is it fleet-aware (cross-session)?** Total Recall and Watchtower persist
   state that outlives one session. Those are the packs CCC can lean on for shared
   memory and durable work state.
3. **Does it drive a browser?** gstack browse and chrome-devtools MCP open a real
   browser. Those are the verification muscle a spawned CCC lane can borrow.

CCC's own contribution is the layer none of them own: **persistent sibling
sessions** you can spawn, inject into, and ask (`/api/sessions/spawn`,
`/api/inject-input`, `/api/ask`), rendered on a board. A new read-only endpoint,
`/api/skills`, inventories the installed packs with these honest flags so the
dashboard and docs can tell the truth about what is wired up.

## Local packs

### Superpowers (obra / claude-plugins-official) — installed, v6.1.1

An agentic skills framework and software-development methodology. 14 skills:
brainstorming, writing-plans, executing-plans, subagent-driven-development,
dispatching-parallel-agents, using-git-worktrees, finishing-a-development-branch,
requesting-code-review, receiving-code-review, systematic-debugging,
test-driven-development, verification-before-completion, writing-skills,
using-superpowers.

- **How it spawns:** every "agent" is an in-harness `Task`-tool subagent, same
  session, ephemeral. Parallelism is literally "issue multiple Task dispatches in
  one response." Tracking is files on disk: `subagent-driven-development` keeps a
  ledger at `.superpowers/sdd/progress.md` with per-task commit ranges;
  `requesting-code-review` fills a `code-reviewer.md` template. Worktrees come from
  a native worktree tool if present, else `git worktree add`.
- **How CCC interacts today:** those subagents surface as the chip and status rail
  on the spawning session's row. No coupling to `wt` or CCC exists in the pack.
- **Where it shines in a fleet:** disciplined, self-verifying single-session work.
- **Where it breaks in a fleet:** the tracking is session-local. Close the session
  or hand the plan to someone else and the `.superpowers/sdd/progress.md` ledger is
  invisible to the rest of the fleet. That is the gap
  [`superpowers-to-watchtower`](../skills/superpowers-to-watchtower.md) fills:
  `wt import` the plan into a durable, board-visible queue.
- **Public footprint:** obra/superpowers reports ~256k GitHub stars (higher than
  claude-code's own repo, so sanity-check before quoting externally). Multi-harness
  (Claude Code, Codex, Cursor, Copilot CLI, and more). No install/usage counts
  exist for any pack; stars are the only proxy.

### gstack browse + open-gstack-browser — installed (symlinked)

Headless Chromium QA / screenshots via a compiled CLI (`$B`): `goto`, `text`,
`click`, `fill`, `screenshot`, `js`, `snapshot`, `responsive`, `viewport`. State
(cookies, tabs, login) persists across calls **within a session**. First call
auto-starts Chromium (~3s), then ~100ms/command. open-gstack-browser is the
visible-window / demo variant.

- **How CCC interacts today:** none directly, but browse checks an orchestrator
  env marker and prints `SPAWNED_SESSION: true` when launched by an orchestrator,
  so a spawned CCC lane can drive it non-interactively.
- **Fleet fit:** the verification muscle. A spawned lane opens the app and *looks*,
  which a diff read and a unit test cannot. That is
  [`fleet-verify`](../skills/fleet-verify.md).
- **Caveat:** the `dist/browse` binary may need a one-time build before first use.
  CCC also ships its own puppeteer `snapshot.js` as a fallback driver.

### Total Recall (recall / recall-knowledge / dashboard-recall + siblings) — installed

Cross-session, cross-agent persistent memory. `brain search --query ... --deep`,
`brain remember "<fact>"`, `total-recall ingest <path>`, and a local dashboard at
`localhost:24824`. Indexes many sessions and cross-agent context by design.

- **How CCC interacts today:** CCC already queries Total Recall for the sidebar
  session search (`/api/search-recall-sessions`) and detects its state dir to
  offer a dashboard launcher.
- **Fleet fit:** the shared-memory backbone. If every lane writes its outcome with
  `brain remember` tagged by queue/ticket, siblings can recall what was already
  tried. This is real but **not yet systematized** in CCC — it is on the roadmap,
  not claimed as done.

### Token Optimizer (alexgreensh) — installed, v5.11.17

Audit / fix / monitor context-window usage. Skills: token-optimizer,
fleet-auditor (audits token waste across agent systems: Claude Code, Codex,
OpenClaw, and more), token-coach, token-dashboard; plus health / quick commands.

- **Fleet fit:** `fleet-auditor` reads across agent systems, a natural companion to
  CCC's own health strip for cross-session cost. Read/analyze pack; installs hooks;
  does not spawn subagents. Integration is roadmap, not wired today.

### Watchtower (`wt`) — installed (symlinked), first-class in CCC

Local, stdlib-only CLI tracking tickets in named queues and the workers draining
them. `wt find <ref> --json`, `wt status --json`, `wt add`, `wt claim`,
`wt close --summary` (summary mandatory — the dashboard trust signal),
`wt import plan.md -q QUEUE`.

- **How CCC interacts today:** deeply. CCC imports `watchtower.queue` as its
  primary queue engine (falling back to a bundled queue when absent), reads
  `~/.watchtower` state for the board, and can route inject/ask through `wt send` /
  `wt ask`. Migration state is tracked in `docs/watchtower-migration-state.md`.
- **Scope boundary (stated by the pack):** `wt` does **not** message, spawn, or
  inject into sessions. A bare session UUID is a **CCC** object; session
  orchestration is CCC's job (`ccc-orchestration`). So `wt` is the durable
  work-state layer, CCC is the live-session layer. They compose cleanly.

### PostHog (claude-plugins-official) — installed, v1.1.51

Large analytics pack (~122 skills) over an OAuth-gated MCP server: product
analytics, feature flags, experiments, error tracking, signals scouts. Not wired
into CCC; listed for completeness. Roadmap at best.

### Others present

chrome-devtools-mcp (real-Chrome driver, an alternative verification lane),
babysitter, claude-md-management, commit-commands, frontend-design, supabase,
claude-watch, video-claw, and CCC's own recall/visual-* skills. All detectable via
`/api/skills`; none are wired into the fleet beyond being listed.

## Public ecosystem (context, not installed here)

Star counts pulled live 2026-07-17; treat as proxies, not usage.

- **anthropics/skills** (~162k stars): the official Agent Skills open standard
  (agentskills.io). Marketplace plugins: document-skills, example-skills,
  claude-api.
- **anthropics/claude-plugins-official** (~32k stars): 256 plugins live, most in
  "development" (110) and "productivity" (45). Third-party submission with
  quality/security review.
- **Curated lists:** hesreallyhim/awesome-claude-code (has a Multi-Agent
  Orchestration section), ComposioHQ/awesome-claude-skills, travisvn/
  awesome-claude-skills. Notably, superpowers is **not** listed in the hesreallyhim
  README (unverified whether oversight or curation).
- **Fleet-oriented packs referenced by those lists:** Agent Collab Skills
  (task splitter, reconciler, adversarial debate, shared memory, acceptance gate),
  gstack (open-source software factory), fable-mode (multi-stage planning +
  subagent delegation + self-verification), Vibeyard (desktop IDE with swarm mode),
  and niche worktree-fleet coordinators.
- **Anthropic writing / tooling on multi-agent:** "How We Built Our Multi-Agent
  Research System", "Building Effective Agents", and Dynamic Workflows (Claude Code
  research preview: model-authored JS orchestration, up to 1,000 subagents per
  run, 16 concurrent, results kept out of chat context).

## The honest read

- Superpowers and CCC/Watchtower **do not overlap** — superpowers has no notion of
  a persistent worker, a queue, or a fleet dashboard, and CCC has no opinion on how
  you write a plan or run TDD. They compose: superpowers plans and executes inside a
  session; CCC and `wt` give that work a durable, visible, multi-session home.
- The highest-leverage integration gaps, and what W86 built for each:
  1. Superpowers' plan tracking is session-local →
     [`superpowers-to-watchtower`](../skills/superpowers-to-watchtower.md) lifts it
     into a durable, board-visible queue.
  2. No independent *visual* verification lane →
     [`fleet-verify`](../skills/fleet-verify.md) spawns one driving gstack browse.
  3. CCC had no way to see which packs are installed or how they relate →
     `/api/skills` inventories them with honest fleet-synergy flags.
- Left explicitly on the roadmap (not claimed as working): systematized Total
  Recall fleet memory, Token Optimizer's fleet-auditor in the health strip, and
  PostHog. Naming them as roadmap is the point of an honest inventory.
