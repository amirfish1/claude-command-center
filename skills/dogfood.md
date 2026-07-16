---
name: dogfood
description: Use for docs QA — it spawns ONE fresh sibling session via CCC that has never seen the feature, has it follow the README / quickstart / docs cold in a scratch directory, and reports every stumble, ambiguity, and broken step as a doc bug. Fresh eyes are the whole point.
allowed-tools: Bash
---

Dogfood spawns a single fresh CCC session that has **never seen the project** and
makes it follow your docs cold — README, quickstart, whatever path you point it
at — doing exactly what they say and logging every stumble. The docs are treated
as an executable program; the stumbles are the bug report. It builds on the
spawn/report_to mechanics in the `ccc-orchestration` skill — read that first for
the full Spawn/Inject/Ask API.

## Cost

**1 spawned session.** Each spawn is a real billed session on the user's
kanban — tell the user the count before spawning. Do **not** use this for code
correctness — that's code review and tests. Dogfood only tells you whether the
**docs** get a new user through, not whether the code underneath is right.

## Setup

```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt 2>/dev/null || echo "${CCC_URL:-http://127.0.0.1:8090}")"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_URL="http://127.0.0.1:8090"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_DOWN=1
```

- (a) Run every CCC curl with the **network sandbox disabled** — the Bash
  sandbox blocks loopback and fails spuriously even when CCC is up.
- (b) **URL-encode `repo_path`** in query strings (`+` → `%2B`, space → `%20`).
- (c) Resolve your own session id: `$CLAUDE_SESSION_ID`, else the newest
  `*.jsonl` under `~/.claude/projects/<slugified-cwd>/` (see `ccc-orchestration`).

## Spawning the cold reader

Point it at the exact docs (file paths or URLs) and the order to read them in,
then POST one spawn:

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "You have never seen this project. Follow ONLY these docs, in order: <doc paths or URLs>. Do exactly what they say, in a fresh scratch directory. Do NOT read source code unless the docs tell you to; if you get stuck, that is data — record the stumble FIRST, and only then dig. Log EVERY stumble: an ambiguous instruction, a missing prerequisite, a command that fails, output that does not match what the docs promise, or any step where you had to guess. For each stumble report: severity (BLOCKER = could not continue / FRICTION = got through but should not have had to guess / NIT), the exact doc file and line, what happened, and a suggested doc patch. Finish with an overall verdict: would a brand-new user get through this, yes or no? Then clean up your scratch directory.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Omit `"model"` — the server spawn default (Settings → Spawn defaults) applies;
the user can pass `"model"` explicitly to keep cost down. CCC appends the
return-address footer (`report_to`), so the cold reader injects its stumble log
back to you when it finishes.

## Waiting for the report

After spawning, **end your turn.** Tell the user a cold reader is walking the
docs and give its session id. The stumble log arrives by injection
(`report_to`) — do **not** poll or sleep-loop. If it never arrives, check
`GET /api/sessions/spawned` for whether the child is still alive.

## When the report arrives

Turn the stumble list into doc fixes:

- **BLOCKERs are release blockers** — a new user literally could not continue.
  Fix these before shipping the docs.
- **FRICTION** — the docs let the user through but made them guess; tighten the
  wording.
- **NIT** — polish when convenient.

After fixing, **re-run dogfood** to confirm the path is now clean — a fix that
introduces a new stumble is common.

## Dry run

If the arguments contain `dry-run`, print the exact payload — session count (1),
the full cold-reader prompt with the doc paths filled in, and the target repo —
and POST nothing.

## When CCC is down

If `CCC_DOWN=1` or the spawn curl returns connection refused (exit 7), fall back
to the built-in `Task` tool subagent with the same cold-reader prompt. It's
nearly as good here — fresh context is the entire point of dogfooding — you lose
only kanban visibility. Never pretend the CCC spawn ran.

## What this is not

- Not `/review` or `/code-review` — those check code correctness. Dogfood
  ignores the code and checks whether the **docs** work.
- Not a general "try the app" — it's a disciplined cold read that treats the
  docs as an executable program and runs them step by step.
