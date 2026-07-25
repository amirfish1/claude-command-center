---
name: bug-race
description: Use when a bug has already survived at least one debugging attempt and you have 2-3 genuinely distinct root-cause hypotheses — spawns one racer sibling session per hypothesis via CCC, referees the first CONFIRMED root cause with reproducible evidence, and stands the others down via inject.
allowed-tools: Bash
---

Bug-race is the pattern for hunting a stubborn root cause in parallel: you spawn
one autonomous CCC session ("racer") per distinct hypothesis, each diagnosing
ONLY its assigned line, and you act as referee — the first racer to CONFIRM a
root cause with reproducible evidence wins, and you stand the rest down
mid-flight. It builds on the spawn/report_to mechanics in the
`ccc-orchestration` skill — read that first for the full Spawn/Inject/Ask API.

## Cost

**2-3 spawned sessions** — one per hypothesis, capped at 3. Each spawn is a real
billed session on the user's kanban — tell the user the count before spawning.

Do NOT use this for first-pass debugging. The race is for bugs that already beat
you once; a fresh bug gets normal systematic debugging first (see
`superpowers:systematic-debugging`). If you have only one hypothesis, don't race
— just debug it. Racing near-identical hypotheses burns sessions for nothing.

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

## Step 1 (you): enumerate the hypotheses

DISTINCT means a different subsystem or mechanism, not variations of one idea
(e.g. "race in the writer" vs "stale cache" vs "wrong env var" — not "off-by-one
here" vs "off-by-one there"). Cap at 3. Write each as one falsifiable sentence.
If you can't name two genuinely distinct hypotheses, stop — this isn't a race.

## Step 2: spawn one racer per hypothesis

All with `report_to` set to your own session id. Omit `"model"` so the server
spawn default (Settings → Spawn defaults) applies — the user can pass `"model"`
explicitly to keep cost down, with `"engine"` alongside it, since model names
are validated against the target engine (a bare `"model"` fails if the server
default engine differs).

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Bug: <symptom + exact repro steps>. Your assigned hypothesis: <H>. Work ONLY this hypothesis. Try to CONFIRM it with reproducible evidence (instrument, add logging, bisect, trace — evidence a peer could re-run), or ELIMINATE it (state exactly what rules it out). Do NOT fix anything; diagnose only. Report: VERDICT: CONFIRMED (evidence + the exact failure mechanism) / ELIMINATED (the disproof) / INCONCLUSIVE (what would be needed to decide).",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Repeat once per hypothesis, changing only the assigned `<H>`.

## Waiting for the report(s)

After spawning, **end your turn** and tell the user which hypotheses are racing
and in which sessions. Verdicts arrive by injection (the `report_to` footer CCC
appends). Never poll or sleep-loop (see the no-tight-polling rule in
`ccc-orchestration`). If a report never arrives, check `GET
/api/sessions/spawned` for whether that racer's session is still alive before
assuming it finished silently.

## Step 3 (referee = you): pick the winner

The first CONFIRMED verdict **whose evidence you can re-run yourself** wins.
Immediately stand the other racers down via `POST /api/inject-input`:

```bash
curl -s -X POST "$CCC_URL/api/inject-input" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "session_id": "<other-racer-session-id>",
  "text": "Root cause confirmed elsewhere: <mechanism>. Stop your line of investigation. If you saw evidence that CONTRADICTS this mechanism, report it now; otherwise report ELIMINATED and stand down."
}
JSON
```

If **two racers both report CONFIRMED**, do not pick a favorite — either the bug
is two bugs, or one evidence chain is weak. Reconcile the two mechanisms before
you fix anything.

**Record the eliminations** (an issue comment or the fix commit message). Dead
ends are the expensive half of debugging, and the race just bought them cheaply
— throwing that away means the next person re-runs the same dead ends.

## Dry run

If the arguments contain `dry-run`, print the session count, each hypothesis
sentence, the exact spawn payload per racer, and the target repo — then POST
nothing.

## When CCC is down

If `CCC_DOWN=1` or a curl returns connection refused (exit 7): do not pretend the
race ran. Fall back to testing the hypotheses yourself sequentially,
cheapest-to-test first, and tell the user CCC was unreachable.

## What this is not

Parallel one-shot `Task` subagents could explore hypotheses too, but racers are
visible, long-running sessions you can stand down **mid-flight** via inject — the
referee move is the whole point. This is not `wt critique` (that reviews a thing
you already made; this hunts a root cause).
