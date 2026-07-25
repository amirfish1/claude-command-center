---
name: release-audit
description: Use right before cutting a release — it spawns ONE fresh sibling session via CCC that audits only the JUDGMENT gates a release script cannot check (does the changelog actually describe the diff, does the version bump match semver for what changed, do the docs mention the new user-visible feature, is anything uncommitted or half-shipped) and returns a GO / NO-GO with blockers. Advisory only; it never touches the release.
allowed-tools: Bash
---

Release-audit spawns a single fresh CCC session that reviews whether a pending
release is *ready* — but only on the gates that need judgment, not the ones a
script already enforces. The deterministic mechanics (version bump in lockstep,
tag, build, notarize, appcast, brew formula) belong to `scripts/cut-release.sh`
and should stay there; adding a session to re-run them just adds nondeterminism
to the one place you want none. What a script can't judge is whether the
changelog honestly describes the diff, whether the bump is the right *kind* of
bump, and whether the docs caught up. That's this skill. It builds on the
spawn/report_to mechanics in the `ccc-orchestration` skill — read that first for
the full Spawn/Inject/Ask API.

## Cost

**1 spawned session.** Each spawn is a real billed session on the user's
kanban — tell the user the count before spawning. This is a pre-release gate, not
a per-commit check — run it once when you're about to cut `vX.Y.Z`, not on every
push.

## What it audits — and what it must NOT do

The auditor is **read-only and advisory**. It returns a report; it changes
nothing. Say this in the prompt explicitly, because a helpful session will
otherwise try to "just fix it":

- It **must not** run `cut-release.sh` or any release script, bump versions, edit
  `CHANGELOG.md`, create tags, push, or build artifacts.
- The deterministic gates (are the two version strings in lockstep, does the tag
  not already exist, does the build pass) are the **script's** job — the auditor
  only flags them if it happens to notice, it does not own them.

The judgment gates it DOES own:

- **Changelog fidelity** — do the `changelog.d/` snippets (or the drafted
  release notes) actually cover the user-visible changes in the diff since the
  last tag? Anything shipped but undocumented, or documented but not shipped?
- **Semver correctness** — does the proposed bump match the *nature* of the
  changes? A new `/api/*` field is a minor; a renamed/removed field or changed
  CLI contract is a major; bugfixes are a patch. A patch bump hiding a breaking
  change is a NO-GO.
- **Docs caught up** — do the README / public docs mention new user-visible
  features and flags introduced since the last tag?
- **Clean tree / nothing half-shipped** — uncommitted changes, a feature half in
  the diff, a `TODO`/`FIXME` on a shipping path, debug code left in.

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

- **(a) The last released tag** and the **proposed new version** (`vX.Y.Z`) —
  the auditor diffs `<last-tag>..HEAD` to see what's shipping.
- **(b) Where the changelog and version live** — this repo: `changelog.d/`
  snippets roll into `CHANGELOG.md`; version bumps in lockstep in
  `pyproject.toml` and `server.py`. Point the auditor at these so it checks the
  right files.
- **(c) The public API / CLI surface** whose contract defines a major bump — for
  this repo, `/api/*` responses and `run.sh` / env-var flags.

## Spawning the auditor

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Audit release readiness. You are READ-ONLY and ADVISORY: do NOT run any release script, do NOT bump versions, edit changelogs, tag, push, or build — return a report only. LAST TAG: <vX.Y.(Z-1)>. PROPOSED VERSION: <vX.Y.Z>. Diff the range <last-tag>..HEAD and audit ONLY these judgment gates: 1) CHANGELOG FIDELITY — list the user-visible changes in the diff, then check the changelog snippets (<changelog location>) cover them; flag anything shipped-but-undocumented or documented-but-not-shipped. 2) SEMVER — decide the bump the diff actually warrants (patch = bugfix only; minor = additive feature or new API field; major = renamed/removed API field, changed response shape, or changed CLI/env contract on <API surface>) and compare to the PROPOSED version; a bump smaller than the change requires is a blocker. 3) DOCS CAUGHT UP — do the README / public docs mention new user-visible features and flags from the diff? 4) NOTHING HALF-SHIPPED — uncommitted changes, a feature only partly in the diff, debug code or TODO/FIXME on a shipping path. For each gate report PASS or a specific finding with file:line evidence and the exact fix the human must make. Do NOT audit the deterministic mechanics (version-string lockstep, tag collision, build/notarize) — those are the release script's job. Finish with a verdict: GO (all judgment gates pass) or NO-GO with the ordered blocker list.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Omit `"model"` — the server spawn default (Settings → Spawn defaults) applies;
the user can pass `"model"` explicitly to keep cost down — and pass `"engine"`
alongside it, since model names are validated against the target engine (a bare
`"model"` fails if the server default engine differs). CCC appends the
return-address footer (`report_to`), so the auditor injects its GO/NO-GO back to
you when it finishes.

## Waiting for the report

After spawning, **end your turn.** Tell the user a release auditor is running and
give its session id. The verdict arrives by injection (`report_to`) — do **not**
poll or sleep-loop. If it never arrives, check `GET /api/sessions/spawned` for
whether the child is still alive.

## Interpreting the verdict

- **GO** — the judgment gates pass. You still run the deterministic release
  through `cut-release.sh` (`--dry-run` first) — the audit clears the human
  gates, the script owns the mechanical ones. GO is not "release is done."
- **NO-GO** — fix the ordered blockers first. A semver blocker (patch hiding a
  breaking change) is the most expensive to get wrong once external tooling has
  bound to the release, so treat it as hard.

## Dry run

If the arguments contain `dry-run`, print the exact payload — session count (1),
the tag range and proposed version, the changelog/version/API locations it will
check, and the target repo — and POST nothing.

## When CCC is down

If `CCC_DOWN=1` or the spawn curl returns connection refused (exit 7), fall back
to the built-in `Task` tool subagent with the same read-only audit prompt — the
value is the independent judgment pass, which a subagent gives you minus kanban
visibility. Never pretend the CCC spawn ran, and never let the fallback start
touching the release either.

## What this is not

- Not `cut-release.sh` and not a replacement for it — the script owns every
  deterministic step and this skill deliberately does not re-run any of them.
  This clears the *human-judgment* gates the script can't; run the audit, then
  run the script.
- Not `/code-review` — that judges whether the diff is correct. This assumes the
  diff is the intended change and judges whether the release *around* it
  (changelog, version, docs, tree state) is honest and complete.
- Not `dogfood` or `docs-drift` — those check the docs; this checks the release.
  A release-audit that keeps finding doc drift is telling you to run `docs-drift`
  as its own pass.
