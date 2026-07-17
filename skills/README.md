# CCC orchestration skills

Skills that turn CCC's session primitives — **spawn**, **inject**, **ask**
(`/api/sessions/spawn`, `/api/inject-input`, `/api/ask`) — into concrete
workflows. Each is a self-contained playbook an agent session follows; all of
them build on the API conventions in `ccc-orchestration.md` (URL-encoding,
`report_to` return trips, no tight polling, graceful behavior when CCC is
down). Every skill states its expected session count up front — a spawn is a
real billed session, so cost honesty is part of the contract.

## The orchestration pack, ranked by usefulness

Ranking weighs how often you'll reach for it against what it costs per run.

| # | Skill | Spawns/run | For | One-liner |
|---|-------|-----------|-----|-----------|
| 1 | [`pair-verify`](pair-verify.md) | 1 | dev | After a bugfix, a skeptic session must reproduce the ORIGINAL bug on a clean worktree and prove the fix moves it. The biggest trust upgrade per dollar in the pack. |
| 2 | [`standup`](standup.md) | **0** | dev | Ask every live sibling session for a one-line status via `/api/ask`; collate a digest with blockers on top. The free one. |
| 3 | [`second-opinion`](second-opinion.md) | 1 | dev | One fresh session, zero shared context, same task stated anchor-free — then diff the answers. Disagreement marks your blind spots. |
| 4 | [`dogfood`](dogfood.md) | 1 | dev/docs | A session that has never seen the project follows your README cold and reports every stumble with severity + suggested doc patch. |
| 5 | [`handoff`](handoff.md) | 1 | dev | Package this session's state (decisions with whys, verifiable STATE, gotchas) and spawn a successor that verifies the package against git before taking over. |
| 6 | [`bug-race`](bug-race.md) | 2-3 | dev | For bugs that already beat you once: one racer per root-cause hypothesis, first confirmed mechanism wins, referee stands the rest down via inject. |
| 7 | [`press-room`](press-room.md) | 3-4 | marketing | One release in, N channel drafts out in parallel (changelog, LinkedIn, X, optional blog), each from its own session, all fed by one source pack. Drafts only. |
| 8 | [`a-b-copy`](a-b-copy.md) | 3 | marketing | Two writers, same message architecture, different voice constraints; an independent judge scores both and picks a winner. Drafts only. |

### Ecosystem bridges

Skills that connect CCC's fleet primitives to other skill packs you already use
(superpowers, gstack browse, Watchtower). These are installed on startup
alongside `ccc-orchestration`, so the integrations work out of the box.

| Skill | Spawns/run | Bridges | One-liner |
|-------|-----------|---------|-----------|
| [`superpowers-to-watchtower`](superpowers-to-watchtower.md) | 0-N | superpowers → Watchtower → CCC | Lift a superpowers plan out of its in-session scratch ledger: `wt import` it into a durable, board-visible queue, then optionally dispatch one lane per ticket that closes with a summary. |
| [`fleet-verify`](fleet-verify.md) | 1 | CCC + gstack browse | Spawn a lane that drives a real headless browser against the running app, verifies the exact change you describe, and reports a visual verdict with a screenshot. The piece `/code-review` and `pair-verify` miss. |

### Choosing between the look-alikes

- Reviewing **a thing you made** (plan, diff, doc)? → `wt critique` (external
  cross-family critics) or `/code-review`.
- Re-solving **the same task fresh** to compare conclusions? → `second-opinion`.
- Proving **a fix actually fixes the bug**? → `pair-verify`.
- Hunting **an unknown root cause** with competing theories? → `bug-race`.
- One piece of copy, best voice? → `a-b-copy`. One release, many channels? →
  `press-room`.

## Other skills in this directory

- [`ccc-orchestration.md`](ccc-orchestration.md) — the API bible: spawn /
  inject / ask / group chats / federation. Read this before any skill above.
- [`fleet-lane-dispatch.md`](fleet-lane-dispatch.md) — dispatching batches of
  autonomous worker lanes with mission briefs, and verifying their completion
  reports before believing them.
- [`group-chat-checkin.md`](group-chat-checkin.md) — participating in
  multi-session group chats without loops or ghost posts.
- [`spawn-ux-worker.md`](spawn-ux-worker.md) — spawn a repo-scoped worker that
  drains one repo's UX-fixes queue.

## Ideas evaluated and cut

Kept out on purpose — recorded so they don't get re-proposed cold:

- **red-team** (2 attacker-persona sessions): overlaps `wt critique` with an
  adversarial goal string plus the built-in security review; personas without
  real attack tooling add cost, not findings.
- **estimate** (3 sessions independently estimate a plan): the spread is
  rarely actionable; a single planning agent plus `wt critique` of the plan is
  cheaper and sharper.
- **docs-drift** (diff docs against code behavior): subsumed by `dogfood`,
  which catches drift the moment the docs stop matching reality.
- **voice-guard** (check drafts against a voice guide): subsumed by the
  `a-b-copy` judge, which scores voice consistency as part of its rubric.
- **release-gate** (a session runs the release checklist cold): a checklist is
  better as a deterministic script; spawning a session adds nondeterminism to
  the one place you want none.
- **migration-sweep** (fan out a mechanical migration): that's
  `fleet-lane-dispatch` with a per-file brief — already covered.

## Conventions every pack skill follows

1. **Cost first.** The expected spawn count is stated before anything spawns.
2. **`report_to` return trips, never polling.** Spawn, end the turn, let the
   report arrive by injection. `/api/ask` (blocking) is the only sync path.
3. **Dry run.** Pass `dry-run` in the arguments to see exact payloads and
   prompts without POSTing anything.
4. **Honest failure.** If CCC is down, each skill names its fallback (usually
   the built-in Task tool, losing only kanban visibility) — it never pretends
   the orchestrated version ran.
5. **Drafts only** for the marketing skills: nothing is posted or published;
   publishing is a human decision.
