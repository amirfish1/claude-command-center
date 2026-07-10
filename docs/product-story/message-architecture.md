# CCC message architecture

The durable messaging system for Claude Command Center. Companion to
`pain-feature-proof.md`, which owns the claims; this file owns how they are
said. Applies to the product site, README hero, and all outbound collateral.

Last revised: 2026-07-10, against v5.6.0.

---

## 1. Primary audience and the moment of pain

**Who:** a developer already running several coding-agent sessions in parallel.
Claude Code first; often Codex, Cursor, Antigravity, or Kilo Code alongside.
Usually a solo product builder, agency lead, research engineer, or OSS
maintainer. They do not need convincing that agents work. They need help
supervising them.

**The moment:** mid-afternoon, five terminals open. One agent finished an hour
ago and nobody noticed. One is silently waiting on a question. One is about to
run out of context mid-refactor. They realize the bottleneck is no longer the
model. It is them.

Real users say it in these words (verbatim, verified 2026-07-10): "Parallel
agents vanish" (public issue title, openai/codex); "parallel sessions
currently conflict on the working tree" (public issue title,
anthropics/claude-code). And the founder's own words: "I lost track of which
were blocked on me." "tmux stopped scaling."

## 2. Category sentence

> CCC is a local dashboard that attaches to every coding-agent session on your
> machine, however you launched it.

One sentence, no category jargon. "Attaches" is the load-bearing word.

## 3. Hero promise

> Your coding agents outgrew your terminal.
> CCC puts every session on one local board and tells you which one needs you.

Pain first, promise second, both concrete. The promise makes two verifiable
claims (one board for everything; attention surfacing), both Built.

## 4. Supporting sentence

> Start the next while Claude builds the first.

The locked tagline survives as the supporting line. It earns its place: it
describes the workflow outcome, not the software.

## 5. Problem families

Defined canonically in `pain-feature-proof.md` section 1. For copy purposes:

1. **See everything.** Every session, every engine, one board. Even the ones
   you launched from a terminal.
2. **Know what needs you.** Questions waiting, context running out, limits
   approaching. Surfaced, not discovered.
3. **Organize work that outgrew a flat list.** Pin strategy, nest execution,
   arrange the fleet on a canvas.
4. **Steer many agents without orchestration code.** Type into any session.
   Let agents talk in group chats.
5. **Let work run unattended.** Queues that drain, issues that close verified,
   deploys that fix themselves.
6. **Work from anywhere.** Phone in hand or a different machine entirely. The
   fleet does not care where you are.

## 6. Three strongest differentiators

1. **Attach, don't own.** CCC reads each engine's on-disk state as the source
   of truth, so it sees sessions it did not spawn and survives its own
   shutdown. Every competitor that owns execution goes blind the moment you
   touch a terminal. This is the moat; lead with it everywhere.
2. **Trusts ground truth, not agent self-reports.** Attention detection reads
   the actual transcript. Queue health is judged from ticket and worker state,
   not from an agent saying "done." Verification closes issues with a commit
   SHA, not a claim.
3. **Local, readable, dependency-free.** A stdlib-only Python server and a
   no-build vanilla-JS UI, no accounts, no cloud, MIT. Trust by inspection.
   (Never say "read it in an afternoon"; the honest form is "no runtime
   dependencies, nothing hidden.")

## 7. Claim hierarchy

**Essential (above the fold):** one board for every session and engine;
attach-not-own; know what needs you; local and open source.

**Core (problem families, homepage):** the 30 rows in the pain table, grouped
by family, each with proof.

**Advanced (discoverable depth):** sibling ask API, ACP adapter, federation
peers, worktree init scripts, telemetry design, security posture.

**Never claim:** scheduled/cron agent jobs (planned, not built); car mode or
voice control (private); Morning view (private); Hermes engine (undocumented);
SSH remote sessions (experimental); afternoon-readable source; "zero
background jobs" absolutism; identical support across all five engines.

## 8. Objections and truthful answers

| Objection | Truthful answer |
|---|---|
| "Why would I run 30 sessions?" | You probably won't. Most users run 3 to 8. CCC starts paying off at 3, when the first session silently blocks on you. |
| "tmux + claude-squad already does this." | They own execution. Resume a session by hand and their board goes blind. CCC attaches to on-disk state, so it sees sessions you launched any way at all. |
| "Just a wrapper around Claude Code?" | It never wraps the engine. It reads state the engines already write and adds the operations layer: board, attention, queues, GitHub, worktrees. |
| "Why no auth?" | It binds to 127.0.0.1 by default and is a single-user local tool. Exposing it wider is explicit opt-in with a printed warning. See SECURITY.md. |
| "Why one giant HTML file and a stdlib server?" | So you can inspect what runs on your machine without a build system. No dependencies to audit, no bundler between you and the source. |
| "Does it phone home?" | No. Telemetry is opt-in, off by default, five bounded anonymous fields, and the worker source is in the repo. |
| "macOS only?" | macOS is first-class (DMG, notifications, terminal focus). Linux runs headless. Native Windows install shipped in v5.6.0. Some conveniences remain macOS-only; the compare table says which. |
| "Which engines really work?" | Claude Code is first-class. Codex, Cursor, Antigravity, and Kilo Code spawn, appear, and ingest, each with a documented gap (for example Kilo is fire-and-forget). The README matrix is the honest source. |

## 9. CTAs

| Intent | CTA copy | Target |
|---|---|---|
| Try without installing | "Tour the live demo. All data fake, nothing to install." | /demo |
| Install | "Install in 60 seconds" plus the copyable curl line | #install |
| Watch | "Watch a 20-second flow" on each family's video | in-page video |
| Explore | "Read the source" / "Star on GitHub" | GitHub repo |
| Subscribe/updates | "Changelog" and Sparkle auto-update mention | CHANGELOG / appcast |

One primary CTA per screenful. Demo outranks install for cold visitors;
install outranks demo once a pain section has landed.

## 10. Vocabulary

**Use:** session, board, fleet, attach, on-disk state, ground truth, steer,
queue, verify, worktree, local, engine, dormant, waiting on you, context left.

**Avoid:** AI orchestration, agentic workflows, revolutionary, supercharge,
10x, blazingly fast, seamless, magic, copilot for X, mission-critical,
enterprise-grade, "the future of." Also avoid em-dashes in all public copy;
use periods, commas, or colons instead.

**Tone:** an experienced builder showing a working system. Short sentences.
Concrete nouns. Numbers only when verified. Admit limits in the same breath as
strengths; the honest cell in a comparison table converts better than a
boastful one.

## 11. Copy rules for pain-first announcements

1. First sentence names the painful moment in second person or first person
   experience. Never "CCC now has X."
2. Second beat: why the obvious workaround fails (more tabs, more tmux, more
   discipline).
3. Third beat: what CCC does about it, in one sentence, with the proof asset
   (screenshot or video) doing the heavy lifting.
4. One CTA, matched to reader awareness: cold readers get the demo, warm
   readers get the install line.
5. Qualify any Partial claim inline, not in a footnote.
6. Channel adaptation is mandatory: Reddit gets the story and invites
   discussion without a link push; LinkedIn gets the operational lesson; X
   gets the single sharpest frame with the clip.
7. Every visual is a real capture from seeded data. No mockups presented as
   product.
