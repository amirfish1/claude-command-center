---
name: docs-drift
description: Use to catch documentation that has silently gone stale — it spawns ONE fresh sibling session via CCC that extracts every checkable factual claim from a doc (API fields, endpoint names, CLI flags, config keys, file paths, version numbers, example commands) and verifies each one against the actual code, reporting each mismatch as a drift with severity. A claim-by-claim audit, not a read-through.
allowed-tools: Bash
---

Docs-drift spawns a single fresh CCC session that treats a doc as a list of
**factual assertions about the code** and checks each one against the source. It
does not follow the docs as a user would — it enumerates the claims (this
endpoint exists, this flag is `--foo`, this response has field `bar`, this
default is `127.0.0.1`, this version is `X.Y.Z`) and greps/reads the code to
confirm or refute each. The output is a drift ledger. It builds on the
spawn/report_to mechanics in the `ccc-orchestration` skill — read that first for
the full Spawn/Inject/Ask API.

## Cost

**1 spawned session.** Each spawn is a real billed session on the user's
kanban — tell the user the count before spawning. Point it at a **bounded** set
of docs (one file, or a section) — "audit all the docs" is a fleet job
(`fleet-lane-dispatch` with a per-doc brief), not one session.

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

- **(a) The exact doc(s)** to audit — file paths or a named section. One file or
  one section per run; the more bounded, the sharper the ledger.
- **(b) The source of truth** — which code the claims should match (a module, a
  set of endpoints, a config schema). If you don't tell it where the truth
  lives, it wastes the run hunting.
- **(c) The claim types that matter** for this doc — API response fields, CLI
  flags, endpoint paths, config keys/defaults, file paths, version strings,
  copy-paste example commands. Naming them focuses the extraction.

## Spawning the auditor

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Audit this doc for drift against the code. DOC: <path or section>. SOURCE OF TRUTH: <module / endpoints / config to check against>. Method: 1) Extract EVERY checkable factual claim the doc makes about the code — endpoint paths, request/response field names, CLI flags, config keys and their stated defaults, file paths, version strings, and any copy-paste example command. Ignore prose, motivation, and opinion; only claims a reader could act on and be wrong. 2) For EACH claim, verify it against the actual code (grep / read the source — do NOT trust the doc). 3) Classify each: MATCH (doc agrees with code), DRIFT (doc contradicts code — wrong field name, renamed flag, changed default, dead endpoint, stale version), or UNVERIFIABLE (could not locate the truth — say why). Report ONLY the DRIFT and UNVERIFIABLE rows as a ledger; give a MATCH count as one summary number. Each drift row: the exact doc quote + line, what the code actually says (with file:line evidence), severity (BROKEN = a reader following this will fail, e.g. dead endpoint or wrong flag / MISLEADING = works but the doc is wrong, e.g. stale default / COSMETIC = typo-level), and the one-line doc patch. Finish with a verdict: is this doc safe to trust as-is, yes or no?",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Omit `"model"` — the server spawn default (Settings → Spawn defaults) applies;
the user can pass `"model"` explicitly to keep cost down — and pass `"engine"`
alongside it, since model names are validated against the target engine (a bare
`"model"` fails if the server default engine differs). CCC appends the
return-address footer (`report_to`), so the auditor injects its ledger back to
you when it finishes.

## Waiting for the report

After spawning, **end your turn.** Tell the user an auditor is checking the doc
against the code and give its session id. The ledger arrives by injection
(`report_to`) — do **not** poll or sleep-loop. If it never arrives, check
`GET /api/sessions/spawned` for whether the child is still alive.

## When the report arrives

Turn the ledger into doc fixes:

- **BROKEN** drift is a doc bug that will actively mislead — fix before the next
  reader hits it (a dead `/api/*` endpoint, a renamed flag, a wrong required
  field). For a public repo these ship the moment you push, so treat them like
  code bugs.
- **MISLEADING** — the reader gets through but on wrong information (a stale
  default, an old version string); correct the wording.
- **COSMETIC** — polish when convenient.
- **UNVERIFIABLE** rows are their own signal — either the doc is describing
  something that no longer exists, or the truth moved; investigate each.

## Dry run

If the arguments contain `dry-run`, print the exact payload — session count (1),
the full auditor prompt with the doc path and source-of-truth filled in, and the
target repo — and POST nothing.

## When CCC is down

If `CCC_DOWN=1` or the spawn curl returns connection refused (exit 7), fall back
to the built-in `Task` tool subagent with the same claim-extraction prompt —
fresh context matters less here than for `dogfood`, because the value is the
systematic cross-check, not naïve eyes. You lose only kanban visibility. Never
pretend the CCC spawn ran.

## What this is not

- Not `dogfood` — that follows the getting-started path as a user and reports
  where it stumbles; it only catches drift on the exact steps it walks.
  Docs-drift ignores the experience and checks every claim in the doc against the
  code, including claims off the happy path. Run `dogfood` to prove the
  quickstart works; run `docs-drift` to prove a reference doc is not lying.
- Not `/code-review` — that judges whether the code is right. This assumes the
  code is the truth and judges whether the **doc** still matches it.
