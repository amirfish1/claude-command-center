---
name: superpowers-to-watchtower
description: Use when a superpowers plan (writing-plans / brainstorming output in docs/superpowers/plans/) is ready and you want durable, dashboard-visible tracking instead of an in-session scratch ledger — it imports the plan into a Watchtower queue as tickets, then optionally dispatches one CCC lane per ticket that closes its ticket with a summary. Bridges superpowers (ephemeral subagents, file-tracked) to Watchtower (durable queue) to CCC (fleet dashboard).
allowed-tools: Bash
---

Superpowers plans a job into bite-sized tasks and executes them with **ephemeral
same-session subagents**, tracked in a scratch ledger (`.superpowers/sdd/progress.md`).
That is great inside one session and invisible to everyone else. This skill lifts
that plan into a **durable, fleet-visible** form:

1. `wt import <plan.md>` turns the plan's tasks into Watchtower **tickets** in a
   named queue — persistent, addressable (`QUEUE-N`), and rendered on CCC's board.
2. Optionally, one CCC **lane** per ticket drains it autonomously and closes it
   with a mandatory summary (the dashboard's trust signal).

It builds on `ccc-orchestration` (spawn / inject / ask API) and the `watchtower`
skill (`wt` CLI). Read those for the full surface.

## When to use vs. not

- **Use** when a plan will outlive one session, or several sessions/people need to
  see progress, or you want to hand tasks to a fleet rather than run them inline.
- **Do not use** for a plan you will finish yourself in this session — superpowers'
  own `subagent-driven-development` with its commit-range ledger is cheaper and
  already surfaces in CCC as subagent chips on your session row. No queue needed.

## Cost

- **The import itself: 0 spawns.** `wt import` without `--apply` only previews.
- **Lane dispatch: N spawns** (one billed CCC session per ticket you dispatch).
  State the count before spawning. Dispatch a subset first if N is large.

## Setup

```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt 2>/dev/null || echo "${CCC_URL:-http://127.0.0.1:8090}")"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_URL="http://127.0.0.1:8090"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_DOWN=1
command -v wt >/dev/null 2>&1 || WT_DOWN=1
```

- Run every CCC curl with the **network sandbox disabled** (the Bash sandbox
  blocks loopback and fails spuriously even when CCC is up).
- **URL-encode `repo_path`** in query strings (`+` → `%2B`, space → `%20`).

## Step 1 — Import the plan (preview first)

`wt import` is **preview by default** — that is your dry run. It makes one
tool-free reasoning call to infer tickets from the plan and keeps each ticket's
source path + line anchor, so re-import is idempotent.

```bash
wt import docs/superpowers/plans/2026-07-16-my-feature.md -q MYFEATURE
```

Read the preview. When the ticket split looks right, apply it:

```bash
wt import docs/superpowers/plans/2026-07-16-my-feature.md -q MYFEATURE --apply --type feature
wt status -q MYFEATURE --json    # confirm depth + oldest-open age
```

The tickets now show on CCC's board. If you stop here, you have durable tracking
and any session (or person) can pick tickets up by hand. That alone is the win.

## Step 2 (optional) — Dispatch one lane per ticket

Only if you want autonomous execution. For each open ticket, spawn a lane with a
`report_to` return address and instructions to **claim, do, close** its ticket:

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Autonomous lane. 1) Read Watchtower ticket MYFEATURE-3: `wt find MYFEATURE-3 --json`. 2) Claim it: `wt claim MYFEATURE-3 --worker <your-session-id>`. 3) Do exactly the work in that ticket (and only that ticket — its source anchor points at the plan section). 4) Verify your change before believing it. 5) Close it with evidence: `wt close MYFEATURE-3 --worker <your-session-id> --summary \"<what you did + how you verified>\"`. The summary is mandatory — it is the dashboard's trust signal, so make it truthful and specific. Do not touch other tickets.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

- Omit `"model"` to use the server spawn default; if you pass `"model"`, pass
  `"engine"` alongside it (model names are validated per engine).
- Dispatch **independent** tickets in parallel; serialize tickets that touch the
  same files (superpowers' own rule — parallel writers to one file conflict).
- After spawning, **end your turn.** Lanes report back by injection; do not poll.

## Verifying before you trust "done"

A closed ticket with a summary is a *claim*, not proof. Before you believe the
queue is drained:

- `wt status -q MYFEATURE --json` — depth 0 and a recent close time, not "stuck"
  (open tickets + nothing closed in 10 min = stuck).
- Spot-check a close summary against the actual diff. A summary that does not
  match `git log`/`git diff` is the signal to reopen, not to trust.
- For a real fix, chain `pair-verify` on the change before final sign-off.

## Dry run

If the arguments contain `dry-run`: run only the preview `wt import` (no
`--apply`), print the would-be lane count and one filled-in spawn payload, and
POST nothing.

## Honest fallbacks

- **`wt` missing (`WT_DOWN=1`):** you cannot make durable tickets. Fall back to
  superpowers' `subagent-driven-development` and its `.superpowers/sdd/progress.md`
  ledger for in-session tracking. Say tracking is session-local, not fleet-visible.
- **CCC down (`CCC_DOWN=1`):** import still works (`wt` is independent). For
  execution, run tasks as in-process `Task` subagents instead of lanes — you lose
  the kanban view and cross-session visibility, nothing else. Never claim lanes
  ran when they did not.
