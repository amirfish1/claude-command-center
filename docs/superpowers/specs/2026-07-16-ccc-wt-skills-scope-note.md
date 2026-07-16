# CCC + Watchtower skills — scope note (W39)

## What existed (audited)

**CCC** (`claude-command-center/skills/`): `ccc-orchestration.md` (spawn /
inject / ask / group-chat / federation API), `spawn-ux-worker.md` (spawn a
repo-scoped UX-fixes worker), `group-chat-checkin.md` (multi-session chat
protocol). All three are complete, not stubs — grounded in real endpoints,
with worked examples and error-triage sections.

**Watchtower** (`watchtower/watchtower/skills/`): `watchtower/SKILL.md`
(ticket lookup, `wt status`, file/claim/close), `critique/SKILL.md`
(`wt critique` — spawn 2 cross-family critics), `group-chat-checkin/SKILL.md`
(same protocol as CCC's, WT-flavored with `wt chat post`). Also complete, not
stubs.

Note the mission brief's stated path for CCC skills
(`.claude/skills/ccc-orchestration.md`) doesn't exist — the real location is
`skills/` at the repo root (verified via `find`).

## Gaps found

Two gaps stood out as genuinely missing and high-leverage, validated against
this very dispatch (Fable-5 batch, wave 5, lane W39):

1. **Fleet-lane dispatch + evidence verification (CCC).** `ccc-orchestration`
   documents the raw `report_to`/`/api/inject-input` mechanics, but nothing
   documents the *pattern* this batch is actually running: a dispatcher
   spawning N autonomous lanes against mission briefs, and — critically —
   how the dispatcher should verify a lane's `STATUS: SUCCEEDED` report
   against real repo state (`git log`, `git diff --stat`, `ls`) instead of
   trusting the lane's self-report at face value. This is exactly the kind
   of gap that produces false-positive "done" reports at fleet scale.

2. **Triage a stuck WatchTower queue.** The existing `watchtower` skill
   covers happy-path ticket lookup/file/claim/close and `wt status`'s stuck
   flag, but stops at diagnosis. It doesn't say what to *do* about a stuck
   queue: unblock a parked ticket (`wt answer`), release an abandoned claim
   (`wt release`), clear duplicate tickets (`wt dedup`), turn on auto-drain
   (`wt drain on`), or wait for it to clear (`wt wait`). All of these
   commands exist and were verified against `wt <command> --help` output;
   none were previously documented as a single triage workflow.

**Candidates considered and set aside:** a "federation handoff" skill —
`ccc-orchestration` § 2.5 already covers global refs, ownership handoff, and
the typed error taxonomy in reasonable depth; a standalone skill would mostly
duplicate it. A generic "spawn one ad-hoc WT agent" skill — `critique/SKILL.md`
already documents `wt spawn` as the primitive it builds on; splitting it out
added a third skill without a clear new audience. Kept the set to 2 rather
than padding to 4 with lower-signal duplicates.

## What was added

- `claude-command-center/skills/fleet-lane-dispatch.md` — dispatcher-side
  spawn pattern + report format contract + evidence-verification steps
  (git log/diff/ls against claimed `FILES`).
- `watchtower/watchtower/skills/wt-triage-queue/SKILL.md` — diagnose →
  unstick → confirm-drained workflow for a stuck queue, plus an optional
  standing `wt monitor` health check.

Every command/endpoint referenced in both skills was verified live before
being documented: CCC skill against `server.py` (`report_to` normalization,
`/api/inject-input` handler, `/api/sessions/spawn` payload shape) and the
running CCC's actual behavior described in `ccc-orchestration.md`; WT skill
against `wt <command> --help` for every subcommand's real flags (`status`,
`ls`, `blocked`, `answer`, `release`, `dedup`, `drain`, `wait`, `monitor`).
No invented flags.
