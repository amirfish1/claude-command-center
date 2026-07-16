---
name: a-b-copy
description: Use when you want a marketing-copy A/B — spawns two writer sibling sessions via CCC that draft the SAME message architecture in DIFFERENT voices, then a judge session (which saw neither draft happen) scores both and picks a winner.
allowed-tools: Bash
---

A-B-copy runs a clean copy comparison: two writer sessions get the identical
message architecture but different voice constraints, then a third session — a
judge who wrote neither draft — scores both against the architecture and reports
a winner plus a steal list. It builds on the spawn/report_to mechanics in the
`ccc-orchestration` skill — read that first for the full Spawn/Inject/Ask API.

## Cost

**3 spawned sessions** (2 writers + 1 judge). Each spawn is a real billed session
on the user's kanban — tell the user the count before spawning.

Cheaper variant: **2 spawns and you judge** — but a judge who wrote neither draft
is the point (no authorship bias), so state the tradeoff before dropping it. Not
for routine copy tweaks — if you just need one line of copy, write it yourself.

## Setup

```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt 2>/dev/null || echo "${CCC_URL:-http://127.0.0.1:8090}")"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_URL="http://127.0.0.1:8090"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_DOWN=1
```

Notes: (a) run every CCC curl with the network sandbox **disabled** — the Bash
sandbox blocks loopback and fails spuriously even when CCC is up. (b) URL-encode
`repo_path` in query strings (`+` → `%2B`, space → `%20`). (c) Resolve your own
session id from `$CLAUDE_SESSION_ID`, else the newest `*.jsonl` under
`~/.claude/projects/<slugified-cwd>/` (see `ccc-orchestration`).

## Step 1 (you): assemble the message architecture

Before spawning anything, fix the architecture — it's shared, only the voice
varies, and that's what makes the comparison clean:

- **AUDIENCE** — who this is for.
- **CLAIM** — the ONE key claim.
- **PROOF POINTS** — real and checkable; writers may use only these.
- **CTA** — the single action.
- **CHANNEL + LENGTH LIMIT** — where it runs and how long it can be.

If the user didn't supply these, derive them from context and confirm before
spawning.

## Step 2: spawn writers A and B in parallel

Both with `report_to` set to your own session id. Omit `"model"` so the server
spawn default applies — the user can pass `"model"` explicitly to keep cost down.

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Write <deliverable> for <channel>. Message architecture — do not violate it: AUDIENCE: … / CLAIM: … / PROOF POINTS: … / CTA: … / LENGTH LIMIT: …. Your voice constraint: <VOICE>. Use ONLY claims from the proof list — invent nothing. Report the draft text only, no commentary.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Spawn twice, changing only `<VOICE>`. Example voices:
- **A** = "plainspoken founder: first person, concrete, zero hype words".
- **B** = "sharp product marketing: benefit-led, punchy, pattern-interrupt opener".

## Waiting for the report(s)

After spawning the writers, **end your turn** and tell the user both drafts are
being written. Drafts arrive by injection (the `report_to` footer). Never poll or
sleep-loop. If a draft never arrives, check `GET /api/sessions/spawned` for
whether that writer is still alive.

## Step 3: spawn the judge

Only once **both** drafts have arrived, spawn the judge with both drafts inline
(again `report_to` you):

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Score two anonymous drafts against this message architecture: <architecture>. DRAFT 1: <A text>. DRAFT 2: <B text>. Rubric, 1-5 each: claim fidelity (any invented claim = automatic fail), clarity of the first two lines, proof integration, CTA strength, voice consistency. Report: a scores table, the winner, and a steal list — what the loser did better that should be grafted onto the winner.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Then end your turn again and wait for the judge's report the same way.

## Output to the user

Winner draft + the judge's scores table + the steal list. **Drafts only** —
nothing gets posted or published; publishing is a human decision.

## Dry run

If the arguments contain `dry-run`, print the session count (writers + judge), the
message architecture, each writer's voice constraint and prompt, and the target
repo — then POST nothing.

## When CCC is down

If `CCC_DOWN=1` or a curl returns connection refused (exit 7): do not pretend it
ran. Fall back to two `Task`-tool subagents for the writers and one for the judge
— same independence, less visibility — and tell the user CCC was unreachable.

## What this is not

Not asking one session for "two versions" — same voice DNA, so they converge and
you learn nothing. The independence of separately-spawned writers is the product.
Not `press-room` (that fans ONE release out to many channels; this compares TWO
voices on ONE piece).
