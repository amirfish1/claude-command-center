---
name: voice-guard
description: Use to check ONE already-written piece of copy against a voice guide before it ships — it spawns ONE fresh sibling session via CCC that scores the existing draft against the stated voice rules, flags every violation with the offending quote and a minimal in-voice rewrite, and gives a ship / revise verdict. For an artifact that already exists, not a copy competition.
allowed-tools: Bash
---

Voice-guard spawns a single fresh CCC session that holds an existing draft up
against an explicit voice guide and reports where it breaks voice. It is a
one-artifact audit: you already wrote the README section / landing headline /
changelog entry / launch post, and you want an independent read of whether it
sounds like it should before it goes out. It builds on the spawn/report_to
mechanics in the `ccc-orchestration` skill — read that first for the full
Spawn/Inject/Ask API.

## Cost

**1 spawned session.** Each spawn is a real billed session on the user's
kanban — tell the user the count before spawning. For a one-line tweak you can
already see the fix on, just fix it — this earns its cost on a paragraph or
more, or when the voice guide has enough rules that a human misses some.

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

## Preconditions — assemble these BEFORE spawning

The guard is only as good as the guide you hand it. Have both ready:

- **(a) The draft** — the exact text to check, inline in the prompt (not "the
  headline on the landing page" — paste the words).
- **(b) The voice guide** — the rules as concrete do/don't statements, not a
  vibe. Pull them from the repo's style source if one exists (e.g. a CONTRIBUTING
  voice section, an output-style file, or documented copy rules) rather than
  inventing them; a made-up guide produces made-up violations. If a rule is a
  hard ban (e.g. "never use em-dashes", "no exclamation marks"), say so — those
  become automatic-fail checks.

If you cannot state the voice as checkable rules, **you are not ready** — the
result would be one session's taste, which is what `a-b-copy`'s judge already
gives you. Get the guide first.

## Spawning the guard

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Check ONE draft against a voice guide. Do not rewrite it wholesale and do not judge the ideas — only whether it obeys the voice. VOICE GUIDE (the rules; hard bans are automatic-fail): <do/don't rules, with any hard bans marked>. DRAFT: <exact text>. Method: 1) Go rule by rule. For each rule the draft breaks, report a violation row: the rule, the exact offending quote from the draft, why it breaks the rule, and a MINIMAL in-voice rewrite of just that span (change as little as possible — you are correcting voice, not rephrasing the message). 2) Check every hard ban explicitly and report PASS or FAIL for each by name, even the ones that pass. 3) Do not invent violations to look thorough — if a rule is obeyed throughout, say so in one line and move on. Finish with a verdict: SHIP (clean or cosmetic only), REVISE (real voice breaks, listed), or OFF-VOICE (the piece fights the guide throughout and needs a rewrite, not patches). Report the violation list, the hard-ban checklist, and the verdict — nothing gets published, this is advisory.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Omit `"model"` — the server spawn default (Settings → Spawn defaults) applies;
the user can pass `"model"` explicitly to keep cost down — and pass `"engine"`
alongside it, since model names are validated against the target engine (a bare
`"model"` fails if the server default engine differs). CCC appends the
return-address footer (`report_to`), so the guard injects its findings back to
you when it finishes.

## Waiting for the report

After spawning, **end your turn.** Tell the user a voice check is running and
give its session id. The findings arrive by injection (`report_to`) — do **not**
poll or sleep-loop. If they never arrive, check `GET /api/sessions/spawned` for
whether the child is still alive.

## Interpreting the verdict

- **SHIP** — no real voice breaks (or cosmetic only). Apply any nits and go.
- **REVISE** — apply the listed minimal rewrites; each violation comes with the
  exact span and its in-voice replacement, so you patch rather than rewrite.
- **OFF-VOICE** — the draft fights the guide top to bottom; a patch list won't
  save it. Rewrite from the message architecture (that's `a-b-copy` territory if
  you want competing takes).

**Drafts only** — voice-guard never posts or publishes; applying the fixes and
shipping is a human decision.

## Dry run

If the arguments contain `dry-run`, print the exact payload — session count (1),
the voice guide rules (with hard bans marked), the draft, and the target repo —
and POST nothing.

## When CCC is down

If `CCC_DOWN=1` or the spawn curl returns connection refused (exit 7), fall back
to the built-in `Task` tool subagent with the same rule-by-rule prompt — an
independent read of one artifact against a fixed guide does not need kanban
visibility to work. Never pretend the CCC spawn ran.

## What this is not

- Not `a-b-copy` — that spawns two writers to draft the SAME message in
  DIFFERENT voices and has a judge pick a winner; its input is a brief and its
  output is new copy. Voice-guard takes ONE piece that already exists and checks
  it against a fixed guide; its output is a violation list, not a new draft.
  Use `a-b-copy` to choose a voice; use `voice-guard` to enforce one you already
  chose.
- Not `/code-review` or a fact check — it does not verify the claims in the copy
  are true (that's `docs-drift` for docs, or your own review). It only checks how
  the words sound.
