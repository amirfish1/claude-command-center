---
name: pair-verify
description: Use after you fix a bug and before you claim it fixed — it spawns ONE skeptic sibling session via CCC that must first reproduce the ORIGINAL bug on a clean worktree at the pre-fix ref, then prove your fix actually moves it. Reproduce-first discipline, independent of your context.
allowed-tools: Bash
---

Pair-verify spawns a single **skeptic** CCC session whose job is to disprove
your fix. It reproduces the original bug on a clean worktree at the pre-fix
commit, confirms the bug is real, then checks out the fix ref and proves the
behavior actually changed — plus one short pass hunting for a regression the
fix could introduce. It builds on the spawn/report_to mechanics in the
`ccc-orchestration` skill — read that first for the full Spawn/Inject/Ask API.

## Cost

**1 spawned session.** Each spawn is a real billed session on the user's
kanban — tell the user the count before spawning. Do **not** use this for
trivial fixes that already ship with a test proving the fix (just run the test —
a green test on the fix and red on the pre-fix ref is the same evidence, for
free).

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

## Preconditions — gather these BEFORE spawning

The skeptic can only reproduce what you can describe. Have all three ready:

- **(a) Exact repro steps** for the original bug: the commands / inputs, and
  expected-vs-actual output. "It was broken" is not repro steps.
- **(b) The pre-fix ref and the fix ref** — commit shas (not "before" / "after").
- **(c) The repo path.**

If you cannot state the repro steps concretely, **you are not ready to verify** —
go get them first. A verifier with no reproducible bug verifies nothing.

## Spawning the skeptic

Resolve your session id, fill in the shas and steps, then POST one spawn:

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Your job is to prove this fix does NOT work. Be adversarial. 1) Create a clean worktree at the pre-fix ref: git worktree add /tmp/pairverify-<slug> <pre-fix-ref>. 2) Run the repro steps: <exact commands / inputs, with expected-vs-actual>. Confirm the ORIGINAL bug reproduces. If it does NOT reproduce, STOP and report VERDICT: REPRO-FAILED — that alone is a major finding. 3) In the worktree, check out <fix-ref>, run the SAME repro, and confirm it now behaves correctly. 4) Spend one short pass hunting a plausible regression the fix could introduce (adjacent callers, edge inputs, boundary cases). 5) Clean up: git worktree remove /tmp/pairverify-<slug>. Report back: VERDICT (VERIFIED / REPRO-FAILED / FIX-INCOMPLETE / REGRESSION-RISK), plus the exact commands you ran and their output as evidence.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Omit `"model"` — the server spawn default (Settings → Spawn defaults) applies;
the user can pass `"model"` explicitly to keep cost down. CCC appends the
return-address footer (`report_to`), so the skeptic injects its verdict back to
you when it finishes.

## Waiting for the report

After spawning, **end your turn.** Tell the user a skeptic is verifying the fix
and give its session id. The verdict arrives by injection (`report_to`) — do
**not** poll or sleep-loop. If it never arrives, check
`GET /api/sessions/spawned` for whether the child is still alive.

## Interpreting the verdict

- **VERIFIED** — the bug reproduced at the pre-fix ref and is gone at the fix
  ref. Ship it. If it came with a regression note, that still needs *your*
  judgment before you claim done.
- **REPRO-FAILED** — the skeptic could not reproduce the original bug. This
  means your understanding of the bug is likely wrong. **Do not ship on it** —
  figure out what the real bug was.
- **FIX-INCOMPLETE** — the bug still shows at the fix ref. Back to debugging.
- **REGRESSION-RISK** — the fix works but the skeptic found a plausible new
  break. Investigate before shipping.

## Dry run

If the arguments contain `dry-run`, print the exact payload — session count (1),
the full skeptic prompt with the shas and repro steps filled in, and the target
repo — and POST nothing.

## When CCC is down

If `CCC_DOWN=1` or the spawn curl returns connection refused (exit 7), run the
same reproduce-first protocol yourself in a fresh `git worktree`. In your final
claim, say verification was **self-run** — you know too much about the fix to be
a real skeptic (same-context bias), so flag it. Never pretend the CCC spawn ran.

## What this is not

- Not the in-process `/verify` pass (that's you driving your own change). This
  buys an **independent** skeptic with reproduce-first discipline.
- Not `/code-review` — that reads the diff. This actually runs the bug at two
  refs and compares behavior.
