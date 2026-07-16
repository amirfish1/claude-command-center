---
name: second-opinion
description: Use when, before committing to a non-trivial approach, diagnosis, or answer, you want an independent check — it spawns ONE fresh sibling session via CCC (no shared context) to solve the same task from scratch, then diffs its answer against yours. Disagreement is the product.
allowed-tools: Bash
---

Second-opinion spawns a single fresh CCC session with **no shared context** to
solve the same task you're about to answer, independently, and then diffs the
two answers. Where you and the fresh session agree, the answer is probably
solid; where you disagree, you've found a blind spot to investigate before you
commit. It builds on the spawn/report_to mechanics in the `ccc-orchestration`
skill — read that first for the full Spawn/Inject/Ask API.

## Cost

**1 spawned session.** Each spawn is a real billed session on the user's
kanban — tell the user the count before spawning. Do **not** use this for
trivial questions (just answer them) or one-shot factual lookups (use the
built-in `Task` tool — it's a subagent, not a billed kanban session).

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

## The one rule: the prompt must be anchor-free

**Write the task statement WITHOUT your own conclusion, hypothesis, or preferred
approach.** The whole value of a second opinion is that it's independent —
leak your answer into the prompt and you get an echo, not a check. State the
task and what "good" looks like; say nothing about what you think the answer is.

```
# BAD — anchored (leaks your conclusion, guarantees an echo)
"I think the slow endpoint is the per-row ps fork in _discover_live_session_ids.
 Confirm that's the bottleneck and that caching it fixes the latency."

# GOOD — neutral (states task + what good looks like, no answer)
"The /api/sessions list is slow on repos with 1000+ transcripts. Find what makes
 it slow and what would make it fast. 'Good' = a specific code path with evidence,
 not a guess."
```

## Spawning

Resolve your session id, then POST one spawn with the neutral task baked in:

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "You are giving an independent second opinion. Task: <neutral task statement, with the relevant file paths and what 'good' looks like — NO hint of what the answer might be>. Investigate from scratch in the repo at /abs/path/to/repo. Do not ask questions; make reasonable assumptions and state them. Report back in this shape: ANSWER (your conclusion), KEY EVIDENCE (files/lines you relied on), CONFIDENCE (high/medium/low), ASSUMPTIONS (anything you had to guess).",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Omit `"model"` — the server spawn default (Settings → Spawn defaults) applies.
The user can pass `"model"` explicitly to keep cost down. CCC appends the
return-address footer for you (`report_to`), so the fresh session injects its
report back to you automatically when it finishes.

## Waiting for the report

After spawning, **end your turn.** Tell the user a second opinion is running and
its session id. The report arrives by injection (`report_to`) — do **not** poll
or sleep-loop. If a report never arrives, check `GET /api/sessions/spawned` to
see whether the child is still alive before assuming it finished silently.

## When the report arrives: three-way diff

Do not just pick your own answer. Produce a comparison for the user:

- **Where both agree** — likely solid; proceed with more confidence.
- **Where you disagree** — a blind spot. Investigate the disagreement before
  acting; the fresh session had no context to bias it, so its dissent is worth
  real weight.
- **What each side saw that the other missed** — evidence, files, or edge
  cases one of you didn't consider.

## Dry run

If the arguments contain `dry-run`, print the exact payload — session count (1),
the full spawn prompt with the neutral task filled in, and the target repo — and
POST nothing.

## When CCC is down

If `CCC_DOWN=1` or the spawn curl returns connection refused (exit 7), fall back
to the built-in `Task` tool (general-purpose agent) with the **same neutral,
anchor-free prompt**. Fresh context is preserved — you lose only kanban
visibility. Never pretend the CCC spawn ran.

## What this is not

- Not `/critique` (`wt critique` spawns two *cross-family* critics that score a
  thing you already made). This is **one fresh same-family solve of the same
  task**, diffed against yours — the artifact is the disagreement, not a score.
- Not `/code-review` — that reads your diff. This re-solves the problem cold.
