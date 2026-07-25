---
name: threat-model
description: Use before shipping a change that touches a trust boundary (a new endpoint, input parser, file-path handler, subprocess call, or auth check) — it spawns ONE fresh sibling session via CCC that maps the abuse surface of that specific diff, ranks each abuse case by impact and likelihood, and hands back a prioritized ledger that feeds /security-review. A defensive threat map, not an exploit run.
allowed-tools: Bash
---

Threat-model spawns a single fresh CCC session that looks at **one bounded
change** — a diff, a new endpoint, a new input path — and asks the one question
a feature author is worst at answering about their own work: *how would someone
misuse this?* It enumerates abuse cases against the change, ranks them by impact
and likelihood, notes the mitigation already present and the gap that remains,
and hands you a ledger you route into `/security-review` and code fixes. It
builds on the spawn/report_to mechanics in the `ccc-orchestration` skill — read
that first for the full Spawn/Inject/Ask API.

This is a **defensive** map: the session describes attack paths and the defenses
that close them. It does **not** write working exploits, run attacks against live
services, or produce weaponized payloads — the deliverable is the threat model
that tells you where to look, feeding the existing security review and your own
fixes.

## Cost

**1 spawned session.** Each spawn is a real billed session on the user's
kanban — tell the user the count before spawning. Scope it to a bounded change
(one endpoint, one parser, one diff) — "threat-model the whole app" is not one
session's job, and an unbounded map is a shallow one. Skip it for changes that
touch no trust boundary at all (pure UI copy, an internal refactor with no new
input) — there's no abuse surface to map.

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

## Preconditions — decide these BEFORE spawning

- **(a) The change scope** — the exact diff, ref range, endpoint, or files to
  model. The tighter the scope, the deeper the map.
- **(b) The trust boundary it touches** — which of these the change adds or
  moves: a network-reachable endpoint, an untrusted input parser, a file-path or
  glob handler, a subprocess/exec call, an auth or origin check, a
  deserialization step, a secret or credential path. Naming it focuses the
  session on the surface that matters.
- **(c) The threat context** — who could reach this (any localhost process? a
  same-origin page? a remote client? another user?) and what "bad" means here
  (data exfiltration, path escape, RCE, DoS, auth bypass, secret leak). A threat
  model without an adversary is just a code read.

## Spawning the modeler

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Threat-model ONE bounded change. This is a DEFENSIVE exercise: map the abuse surface and the defenses that close it. Do NOT write working exploits, do NOT run attacks against any live service, do NOT produce weaponized payloads — describe attack paths at the design level and the mitigation for each. SCOPE: <diff / ref range / endpoint / files>. TRUST BOUNDARY: <network endpoint / input parser / path handler / subprocess / auth-origin check / deserialization / secret path>. THREAT CONTEXT: <who can reach it, and what 'bad' means here>. Method: 1) Read the change and the code it touches. 2) Enumerate abuse cases: for each, state the adversary (who), the entry point (what they control), the malicious input or action, and the failure it triggers. Cover the boundary types in scope — e.g. for an input handler: injection, path traversal, oversized/malformed input, encoding tricks; for an endpoint: missing auth/origin check, CSRF, parameter tampering, DoS; for a subprocess: argument injection, unsanitized interpolation. 3) For EACH abuse case, record: impact (CRITICAL / HIGH / MEDIUM / LOW), likelihood (how reachable — is the precondition already met in normal deployment?), the mitigation ALREADY in the code (quote it with file:line, or 'none found'), and the GAP that remains. 4) Rank the ledger by impact x likelihood, worst first. Finish with: the single highest-risk gap, and a short list of exactly what /security-review or a code fix should check next. Report the ranked abuse-case ledger and that shortlist — no exploit code.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Omit `"model"` — the server spawn default (Settings → Spawn defaults) applies;
the user can pass `"model"` explicitly to keep cost down — and pass `"engine"`
alongside it, since model names are validated against the target engine (a bare
`"model"` fails if the server default engine differs). CCC appends the
return-address footer (`report_to`), so the modeler injects its ledger back to
you when it finishes.

## Waiting for the report

After spawning, **end your turn.** Tell the user a threat modeler is mapping the
change and give its session id. The ledger arrives by injection (`report_to`) —
do **not** poll or sleep-loop. If it never arrives, check
`GET /api/sessions/spawned` for whether the child is still alive.

## When the report arrives

The ledger is a to-do list ranked for you, not a verdict:

- **CRITICAL / HIGH gaps** — route each into `/security-review` (point it at the
  exact file:line and abuse case) and fix before shipping. For a public repo
  these ship the moment you push, so treat an unmitigated HIGH like a release
  blocker.
- **MEDIUM** — fix or consciously accept with a comment saying why.
- **LOW** — note and move on.
- A gap with an **existing mitigation** the modeler quoted is not a finding —
  confirm the mitigation actually covers the case, then close it.

Before touching `server.py` network binding, origin checks, or path validation
off the back of this, re-read `SECURITY.md` — the ledger tells you where to
look, that file tells you what the intended posture is.

## Dry run

If the arguments contain `dry-run`, print the exact payload — session count (1),
the full modeler prompt with the scope, boundary, and threat context filled in,
and the target repo — and POST nothing.

## When CCC is down

If `CCC_DOWN=1` or the spawn curl returns connection refused (exit 7), fall back
to the built-in `Task` tool subagent with the same defensive threat-model
prompt. The value here is the independent adversarial framing — you designed the
feature, so you rationalize its safety — and a subagent supplies that just as
well; you lose only kanban visibility. Never pretend the CCC spawn ran.

## What this is not

- Not `/security-review` — that scans code for known vulnerability classes and
  reports concrete findings in what exists. Threat-model runs *before* it and
  *feeds* it: it maps the abuse surface of a change so the review knows where to
  aim. Run threat-model to decide what to worry about; run `/security-review` to
  confirm each worry against the code.
- Not `wt critique` or `/code-review` — those judge whether the change is
  correct and well-built. This assumes it works and asks how it's abused.
- Not a red-team with attack tooling — it writes no exploits and runs nothing
  against a live target. It's a design-level threat map. If a gap needs a live
  proof-of-concept, that's a separate, explicitly authorized exercise, not this.
