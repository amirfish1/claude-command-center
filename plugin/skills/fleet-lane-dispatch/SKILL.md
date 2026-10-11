---
name: fleet-lane-dispatch
description: Use when dispatching a batch of autonomous CCC worker lanes with a mission brief and a report_to return address, or when receiving a lane assignment and needing to send a well-formed completion report — and, on the dispatcher side, before trusting a lane's SUCCEEDED report at face value.
allowed-tools: Bash
---

Fleet-lane dispatch is the pattern for running several independent, autonomous
CCC sessions ("lanes") against separate mission briefs, then collecting and
verifying their completion reports. It builds directly on the spawn/report_to
mechanics in the `ccc-orchestration` skill — read that first for the full
Spawn/Inject/Ask API. This skill covers the two things that skill doesn't:
how to structure a *lane's* return trip, and how a dispatcher checks a report
is true before believing it.

## 1. Dispatcher: spawning a lane

Each lane is a normal `/api/sessions/spawn` call with `report_to` set to your
own session id (see `ccc-orchestration` § 2 for the full payload). The prompt
should point at a self-contained mission brief — a file the lane can read
independently, not a paraphrase — plus the return-address block:

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Read the mission brief at /abs/path/to/brief.md and execute it completely and autonomously. Do not stop to ask questions. Report back when done (see below).",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

CCC appends the return-address footer for you (the `_wrap_prompt_with_return_address`
behavior documented in `ccc-orchestration`) — you don't need to hand-write the
`/api/inject-input` instructions yourself, but writing them explicitly (as
below) makes the brief self-contained if it's ever read outside CCC.

## 2. Lane: sending the completion report

One report, sent once, at the very end — not progress updates mid-task:

```bash
curl -s --max-time 30 -X POST "http://127.0.0.1:8090/api/inject-input" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<dispatcher-session-id>", "announced_from": "<your session name or id>", "text": "<report>"}'
```

Run this curl with the network sandbox disabled (loopback IPC) — see
`ccc-orchestration` § 1.2. The `text` must contain, in this order:

- `STATUS: SUCCEEDED` or `STATUS: FAILED`
- `SUMMARY:` 1-3 sentences of what was done
- `FILES:` paths touched/created, or `none`
- `REASON:` only if FAILED — what blocked you

JSON-escape `text` (quotes, newlines) so the payload parses.

## 3. Dispatcher: verifying a report before trusting it

**A lane's own SUCCEEDED claim is not evidence — it's a claim.** The lane
wrote its own report; it did not have an adversary checking its work. Before
marking a lane done, cross-check the `FILES` field against the actual repo
state in the lane's `repo_path`:

```bash
# Do the claimed files actually exist / were they actually touched?
git -C /abs/path/to/repo log --oneline -n 10 -- <claimed-path>
git -C /abs/path/to/repo diff --stat HEAD~5..HEAD -- <claimed-path>
ls -la /abs/path/to/repo/<claimed-path>
```

Red flags that mean the report should not be trusted as-is:
- `STATUS: SUCCEEDED` but `git log` shows no recent commit touching the
  claimed files, and `ls` shows nothing new either — the work described
  didn't land.
- `FILES: none` on a lane whose mission brief required a code or doc change —
  likely means the lane didn't actually do the work, or did it but forgot to
  commit (`git status` in that repo will show uncommitted changes if so).
- A report that never arrives — check `wt workers` / `/api/sessions/spawned`
  for whether the lane's session is still alive before assuming it finished
  silently.

If a lane reports `FAILED`, read its `REASON` and decide whether to re-dispatch
with a narrower brief, fix the blocker yourself, or escalate to the user —
don't silently retry the identical prompt and hope for a different result.

## What this is not

This skill doesn't cover the spawn/inject/ask HTTP contract itself (see
`ccc-orchestration`), and it isn't a task queue — for a durable, resumable
queue of tickets across a fleet of workers, that's WatchTower (`wt`), not CCC.
