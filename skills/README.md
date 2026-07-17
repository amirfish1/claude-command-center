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
| 5 | [`docs-drift`](docs-drift.md) | 1 | dev/docs | A fresh session extracts every checkable claim from a doc (fields, flags, endpoints, defaults, versions) and verifies each against the code, reporting each mismatch as a drift. Claim-by-claim, not a read-through. |
| 6 | [`handoff`](handoff.md) | 1 | dev | Package this session's state (decisions with whys, verifiable STATE, gotchas) and spawn a successor that verifies the package against git before taking over. |
| 7 | [`threat-model`](threat-model.md) | 1 | dev/security | Before shipping a trust-boundary change, a fresh session maps the abuse surface of that diff, ranks each abuse case by impact × likelihood, and hands back a ledger that feeds `/security-review`. Defensive map, no exploits. |
| 8 | [`bug-race`](bug-race.md) | 2-3 | dev | For bugs that already beat you once: one racer per root-cause hypothesis, first confirmed mechanism wins, referee stands the rest down via inject. |
| 9 | [`release-audit`](release-audit.md) | 1 | dev/release | Right before cutting `vX.Y.Z`, a fresh session audits only the JUDGMENT gates a release script can't check — changelog fidelity, semver correctness, docs caught up, nothing half-shipped — and returns GO / NO-GO. Advisory; never touches the release. |
| 10 | [`press-room`](press-room.md) | 3-4 | marketing | One release in, N channel drafts out in parallel (changelog, LinkedIn, X, optional blog), each from its own session, all fed by one source pack. Drafts only. |
| 11 | [`a-b-copy`](a-b-copy.md) | 3 | marketing | Two writers, same message architecture, different voice constraints; an independent judge scores both and picks a winner. Drafts only. |
| 12 | [`voice-guard`](voice-guard.md) | 1 | marketing | Gate ONE finished draft against a voice guide: a fresh session checks it rule by rule and flags each violation with a minimal in-voice rewrite. For an artifact that already exists, not a copy competition. |

### Choosing between the look-alikes

- Reviewing **a thing you made** (plan, diff, doc)? → `wt critique` (external
  cross-family critics) or `/code-review`.
- Re-solving **the same task fresh** to compare conclusions? → `second-opinion`.
- Proving **a fix actually fixes the bug**? → `pair-verify`.
- Hunting **an unknown root cause** with competing theories? → `bug-race`.
- Proving a **quickstart works** for a new user? → `dogfood`. Proving a
  **reference doc still matches the code**, claim by claim? → `docs-drift`.
- Mapping how a **change could be abused** before shipping? → `threat-model`
  (feeds `/security-review`, which then checks each worry against the code).
- Clearing the **human-judgment gates before cutting a release** (changelog,
  semver, docs, tree state)? → `release-audit`, then run `cut-release.sh`.
- One piece of copy, best voice? → `a-b-copy`. Enforcing a voice on **one draft
  you already wrote**? → `voice-guard`. One release, many channels? →
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

## Wave 2 — four of the cut candidates, re-scoped and shipped

Wave 1 cut six ideas. On an honest second look, four had cut reasons that a
sharper scope dissolves — those shipped as wave 2 (`docs-drift`, `voice-guard`,
`threat-model`, `release-audit`, in the table above). The point of each was to
make the overlap that got it cut go away:

- **docs-drift** was cut as "subsumed by `dogfood`." It isn't: `dogfood` is
  experiential (walk the quickstart, log where you stumble) and only sees drift
  on the steps it walks; `docs-drift` is analytical (enumerate every claim a
  reference doc makes and check each against the code), catching drift off the
  happy path. Different failure mode, different trigger.
- **voice-guard** was cut as "subsumed by the `a-b-copy` judge." That only holds
  when you're generating two drafts to compare. The common case is one finished
  draft and the question "does this match our voice?" — `voice-guard` gates that
  one artifact rule by rule; it generates nothing.
- **threat-model** is the re-scope of the cut **red-team** idea. Red-team was
  cut because attacker personas without real tooling add cost, not findings —
  true of that design. `threat-model` drops the personas and the (absent) attack
  tooling and does the one thing that was actually valuable: a defensive
  abuse-surface map of a bounded diff that feeds `/security-review`. No exploits,
  no live attacks — just the map that tells the review where to aim.
- **release-audit** is the re-scope of the cut **release-gate** idea.
  Release-gate was cut because a checklist is better as a deterministic script —
  correct, and `release-audit` refuses to re-run any of the script's mechanical
  steps. It audits only the gates a script *can't*: does the changelog honestly
  describe the diff, is the semver bump the right *kind* of bump, did the docs
  catch up, is anything half-shipped. Advisory GO/NO-GO, then you run
  `scripts/cut-release.sh`.

## Ideas evaluated and still cut

Kept out on purpose — the cut reason is a fundamental, not a matter of time:

- **estimate** (3 sessions independently estimate a plan): the spread is
  rarely actionable; a single planning agent plus `wt critique` of the plan is
  cheaper and sharper.
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
