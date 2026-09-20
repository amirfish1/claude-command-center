# Working in this repo

This file tells AI coding agents (and external contributors running them) the house rules. Not user-facing docs — see `README.md` and `CONTRIBUTING.md` for that.

## This is public OSS

Repo lives at `github.com/amirfish1/claude-command-center`. Every commit, comment, file name, and test fixture ships to the world. Assume strangers read it.

- No internal paths, client names, private URLs, or PII in code, comments, or tests.
- No secrets — not even placeholder tokens that "look like" real ones. Use obvious fakes (`sk-ant-test-XXXX`).
- No references to private internal systems. If a feature exists for one user, either generalize it or gitignore it (see the Morning view for the pattern).

## Private documentation boundary

This checkout is public. Keep non-public plans, specs, product-story source,
backlog notes, and agent working documents in the separate private
`CCC-private-docs` repository. Do not recreate `docs/superpowers/`, commit
private-document copies here, or add a private-repository submodule or
symlink. Publish only explicitly reviewed, public-safe exports.

## Commits

**Conventional Commits.** Scan `git log` for existing scopes — match them. Common types in this repo:

- `fix(layout)`, `fix(ci)`, `fix(titles)` — bug fixes
- `feat(ui)`, `feat(repo-picker)`, `feat(titles)` — user-visible features
- `docs`, `chore`, `perf` — as standard

Subject line under ~70 chars. Body (wrapped at ~80) explains the why, not the what — the diff shows what.

Co-author tag from the trailer is fine but not mandatory.

## Git workflow

Outside contributors: fork, branch, and open a PR against `main` — see
`CONTRIBUTING.md`. Keep each PR to one focused change.

Rules that hold for everyone, agents included:

- **Stage by explicit path.** Never `git add -A`, `git add .`, or
  `git commit -a` — they sweep unrelated files into the commit.
  `git commit --only <paths> -m "type(scope): subject"` is the safe form.
- **Never** run `git checkout -- .`, `git restore .`, `git clean -f`, or
  `git reset --hard` without asking first.
- Never force-push `main`, and never bypass the pre-push gate
  (`scripts/pre-push.sh`) with `--no-verify` — fix what it reports.
- **Commit means push.** On the maintainer's shared clone every commit is
  pushed immediately (`git push origin main`) — deployment pulls from
  origin, so an unpushed commit is undeployed. If the gate fails, fix it or
  don't commit. (Fork/PR contributors push their branch, not `main`.)
- `/lean-commit` (`.claude/commands/lean-commit.md`) commits only the paths you
  changed; `scripts/lean-commit.sh` lists candidates with noise filtered.

**Maintainer-local workflow.** How a given maintainer runs their own machines
(when to commit, when to push, parallel-session etiquette) is not project
convention and is not recorded here. If a gitignored `CLAUDE.local.md` exists
at the repo root, read it and any files it imports, and follow it — it takes
precedence over this section.

## CHANGELOG

Follows [Keep a Changelog](https://keepachangelog.com). Every user-visible change drops a small markdown file in `changelog.d/` instead of editing `CHANGELOG.md` directly — that way two parallel sessions don't collide on the `[Unreleased]` section.

- Filename: `<category>-<short-slug>-<discriminator>.md` (e.g. `added-context-pill-2026-04-26.md`).
- File contents: just the bullet text. A leading `- ` is optional.
- Categories: `added`, `changed`, `fixed`, `removed`, `security`, `deprecated`.

See `changelog.d/README.md` for the full convention.

At release time, run `python3 scripts/release.py X.Y.Z` to roll snippets into a fresh `## [X.Y.Z] - YYYY-MM-DD` block in `CHANGELOG.md` and `git rm` the snippet files. The legacy `[Unreleased]` section above it stays as-is until cleared by hand at the next release boundary.

## SemVer

Two places to bump in lockstep:
- `pyproject.toml` — `version = "X.Y.Z"`
- `server.py` — `__version__ = "X.Y.Z"`

Patch for bug fixes. Minor for new features. Major for breaking `/api/*` contracts or breaking CLI flags (`run.sh` / env vars).

Tag as `vX.Y.Z`. `gh release create` with release notes copied from the CHANGELOG section.

## API contracts

`/api/*` endpoints are the stable surface external tooling (agent hooks, the browser UI, pkood integration) binds to. Treat them like public API:

- Adding a field to a response is fine.
- Adding a new endpoint is fine.
- Renaming a field, removing a field, or changing a response shape is a **breaking change** — major version bump, and update SECURITY.md / README.md.
- `/api/repo/switch` is a deprecated compatibility endpoint that returns 410.
  Repo-scoped APIs must receive an explicit `repo_path`.

## Security posture

Read `SECURITY.md` before changing anything about network binding, origin checks, or path validation. Summary:
- Default bind is `127.0.0.1`. `CCC_BIND_HOST=0.0.0.0` requires opt-in + prints a warning.
- Same-origin check on every POST (`_check_same_origin`).
- `/api/open` clamps paths to the explicit repo/session context and command-center log directories.

## Conventions

- `server.py` is stdlib-only on purpose — no pip dependencies at runtime. Don't import `requests`, `pydantic`, `fastapi`, etc. `urllib` + `http.server` + `json` cover it.
- `ccc` (repo root) is the stdlib-only CLI for a running server — `ccc sessions` reads the live census from `GET /api/sessions/census`, `ccc spawn` posts to `/api/sessions/spawn` (repo-scoped to the caller's cwd by default), and `ccc models` renders the `GET /api/engines/models` catalog, all finding the server via `--server` / `$CCC_SERVER` / `~/.claude/command-center/port.txt`. With no known subcommand it passes through to `run.sh` (same name/behaviour as the Homebrew launcher). `scripts/install.sh` symlinks it to `~/.local/bin/ccc`. Same stdlib-only rule as `server.py`.
- `static/index.html` is a single-file app by design (no bundler, no npm). Inline CSS/JS is expected. Don't split it into modules without a strong reason.
- In zsh, lowercase `path` is a special array tied directly to `PATH`. Never use
  `path` as a scratch, local, or loop variable in shell diagnostics; use a
  descriptive name such as `target_path` or `candidate_path`. If commands seem
  to disappear after a probe, check `typeset -p path PATH` before changing the
  machine's global environment.
- Flow workspace work (`#flowBoard`, `static/app.js`, `static/app.css`) has
  maintainer notes in `.claude/rules/flow-workspace.md`.
- `hooks/` scripts run inside agent hook pipelines — they must exit fast and never prompt.
- Multiple CCC instances are allowed only across DIFFERENT repos (multi-repo
  peers, discovered via `registry.json`). At startup `main()` refuses to
  launch a second instance of the SAME repo (matched by git common-dir, which
  is identical across worktrees). Intentional dev/verification duplicates
  bypass with `CCC_EPHEMERAL=1` (also skips the shared `port.txt` claim) or
  `CCC_ALLOW_DUPLICATE_REPO=1`.
- The Morning view (`morning.py`, `morning_store.py`, `static/morning/`) is a **gitignored opt-in plugin** for one user's workflow. Don't reference it in the README or treat it as part of the core.

## Testing

`tests/test_smoke.py` imports `server.py` and checks nothing explodes. CI is minimal by design. If you add a feature, a smoke-level assertion is nice-to-have but not required — the bar is "doesn't break the import."

### Browser / UI verification

To verify UI changes visually, use this repo's **puppeteer** harness: `node snapshot.js` launches headless Chrome, loads `http://127.0.0.1:8090`, and writes `snapshot.png`. Puppeteer's browser lives in `~/.cache/puppeteer`.

CCC uses Puppeteer 25, which no longer exposes `page.waitForTimeout()`. For a
short delay in an ad-hoc verification script, use
`await new Promise((resolve) => setTimeout(resolve, ms))`; prefer
`page.waitForSelector()`, `page.waitForFunction()`, or `page.waitForNetworkIdle()`
when a specific condition is available.

Puppeteer's locator implementation does not provide `Locator.first()`. When a
verification needs one matching element, use `page.evaluate()` with DOM selectors
such as `document.querySelector()` (or evaluate an explicit `querySelectorAll()`
choice) rather than Playwright-style locator chaining.

**Do not use the Codex in-app browser (`iab`) backend or Playwright for this.** `iab` is unavailable outside a desktop app context, and Playwright is not a CCC dependency — "iab browser not available" / "cannot import playwright" means wrong tool, not a breakage. Use `node snapshot.js` (Chromium is sufficient; no WebKit/Firefox needed).

**Vision for screenshots:** inspect the image directly when your current model and available tools support image inspection. Only when the current model cannot directly inspect the image, use the `claude` CLI in headless mode as a low-cost vision fallback:
```bash
echo "Describe what you see in /path/to/screenshot.png, focusing on [specific question]" | claude -p --model claude-haiku-4-5-20251001 --allowedTools "Read"
```
For this fallback, always pass `--model claude-haiku-4-5-20251001` (vision works on Haiku; without `--model` the probe inherits the user's default, currently Fable, and appears in CCC as a Fable session). `--allowedTools ""` leaves claude with no Read tool so it cannot open the PNG — use `"Read"` (OPS-909).
Do not spawn a vision helper when you can inspect the image yourself. If the image itself is missing or inaccessible, resolve access first; a missing file is not a model capability gap. The explicit Haiku model prevents a model without image inspection from accidentally launching an expensive default model. OCR and pixel analysis are not substitutes for understanding visual layout. Apply the read-only tool restrictions below to the fallback command.

## Restart requirements

For every code change, the agent must explicitly state which of these three
servers need a restart before the change takes effect:

1. **CCC dashboard server** (`server.py`, `ccc_server/*.py`, `static/`, `hooks/`)
2. **CCC worker / control-plane worker** (`infra/support-worker/`, control-plane subprocess)
3. **WatchTower** (`wt` CLI / queue tracker — external, not part of CCC)

Default assumption: only the CCC dashboard server needs a restart. Worker and
WatchTower need a restart only when the touched files are part of those
processes. State it clearly in the final summary, e.g.:

- CCC dashboard server: **needs restart**
- Worker: **no restart needed**
- WatchTower: **no restart needed**

## How users get changes

Most changes ship the moment you `git push origin main`. Only `.app`-shell changes need a real release.

| You touched… | How users get it | What you owe |
|---|---|---|
| `server.py`, `static/`, `hooks/`, `install.sh`, `run.sh` (server + dashboard + install) | curl users: next `./run.sh` (install does `git pull --ff-only`). brew users: next `brew upgrade ccc`. DMG users: same path — the .app spawns `~/.ccc/.../run.sh` which is git-tracked. | Just `git push origin main`. No DMG rebuild, no release. |
| `docs/` (landing page, public docs) | GitHub Pages picks it up in ~1 min after push | `git push origin main`. |
| `docs/appcast.xml` | Same as `docs/` — but this is what Sparkle reads. | Push, then verify `curl -s https://ccc.amirfish.ai/appcast.xml` returns the new entry. |
| `scripts/macapp/main.swift`, `scripts/build-dmg.sh`, `scripts/release-dmg.sh`, `scripts/macapp/vendor/Sparkle.framework` (the .app shell or DMG build flow) | **DMG users get it ONLY via Sparkle auto-update**, which only fires when you ship a new versioned DMG with an EdDSA signature in the appcast. | Bump version → `./scripts/release-dmg.sh X.Y.Z` → `gh release create vX.Y.Z` with the DMG attached → commit + push `docs/appcast.xml`. See `docs/RELEASING.md` for the full sequence. |
| `infra/telemetry-worker/` (Cloudflare Worker) | The Worker is independent of `main`. Pushing the repo does NOT deploy it. | `cd infra/telemetry-worker && npx wrangler deploy`. |
| Homebrew formula | Formula lives at `github.com/amirfish1/homebrew-ccc`, NOT this repo. | Push there (separate repo). brew users get it on `brew upgrade ccc`. |
| `changelog.d/*`, `tests/`, `README.md`, `CLAUDE.md`, `AGENTS.md`, `pyproject.toml`/`server.py` version bumps | On push to main | Just `git push origin main`. Bumping versions touches a release cycle — see `docs/RELEASING.md`. |

**Quick rule of thumb:**
- Touched anything in `scripts/macapp/` or `scripts/build-dmg.sh`? → **You owe a Sparkle release** (`docs/RELEASING.md`).
- Touched `infra/telemetry-worker/`? → **Run `wrangler deploy`** separately.
- Everything else? → **`git push origin main`** and you're done.

If you're unsure, default to pushing then checking the table — `git push` is reversible (`git revert`); a half-shipped release is harder to clean up.

Don't mock external systems (`gh`, agent CLIs, `pkood`) in the smoke test. The smoke test is about import-time correctness, not behavior.

**Note:** the `hunch_*` names below are MCP tools, not shell commands. In Codex sessions they appear as `mcp__hunch__*`; there is no `hunch_context` CLI — never invoke them via the shell.

<!-- HUNCH:START — auto-generated, do not edit by hand -->
## 🧠 Hunch (Engineering Memory)

This repo has **Hunch** — a curated graph of *why* the code is the way it is (decisions, bug history, invariants). It currently holds **37 decisions, 0 bugs, 8 constraints, 12 components, 0 policies, 9 open findings**.

**Consult Hunch via the `hunch_*` MCP tools — pick by MOMENT, not from memory:**

**Orient (session/task start):**
- For a new user task, call `hunch_task(action: "start", title: <short task title>)` once and retain its `task_id`. If a native prompt hook already supplied a task ID, reuse its exact start arguments instead of creating another task; each new native prompt has its own ID. Otherwise reuse the ID for follow-up work on the same task; never borrow another task's ID. This is task bookkeeping; `hunch_context` remains the first memory lookup. If reporting fails, continue the work and disclose the gap.
- When the user asks to **update Hunch**, run `hunch update` from this repository root. It updates to the latest release and repairs all configured harness pins. Use `hunch update --global` to also update a global CLI alongside a repository dependency; reconnect active MCP sessions afterward.
- `hunch_context(target, task_id)` — the minimal relevant slice for what you're about to do; a task phrase falls back to the closest graph matches. **Call FIRST** for memory. Include the current task ID on each context call so its contribution is inspectable.
- `hunch_structure(target?)` — the indexed shape of the repo/dir/file/symbol — orient from the graph, not grep rounds.
- `hunch_runbook(task)` — the proven steps for a recurring task, before re-deriving them.
- `hunch_escalations()` — the decisions only the HUMAN can make (including one exact imported ADR at a time, topic conflicts, and policy calls). Normally empty; when it isn't, ASK the user inline — an entry is a question, silence is never approval. Apply an ADR answer only through `hunch_review_imported_adr` with its printed source and review hashes.
- `hunch now` (CLI) — recent decisions + the live roadmap; `hunch log` — the memory-move timeline (every capture/adopt/supersede/prune/repair, each revertable).

**Before designing / choosing an approach:**
- `hunch_why(target)` — why a file/symbol is shaped this way (decisions, bugs, constraints) — including what was already REJECTED.
- `hunch_current_decision(topic)` — the one live answer for a topic (history + rejected included).
- `hunch_bug_lineage(symptom_or_symbol)` — has this failed before? what was the root cause?
- `hunch_compare(candidates)` — rank candidate branches/commits by fewest invariant hits.
- `hunch_query(query)` — free-text search when nothing above fits.

**Before editing:**
- `hunch_check_constraints(scope)` and `hunch_get_dependents(symbol)` / `hunch_blast_radius(target)` — invariants in scope + who you'd break. (The pre-edit hook injects this per file automatically; call these for PLANNING breadth.)
- `hunch_findings(scope?)` — known-but-unfixed gaps in the area (past audits, measurements, incidents) so you inherit them instead of re-discovering them.

**Before committing / merging:**
- `hunch_conformance()` — does the code still SATISFY recorded intent? Run before and after a refactor.
- `hunch_policy_evaluate(policy_id?, active_only?)` / `hunch_policy_plan(policy_id)` / `hunch_policy_card(policy_id)` / `hunch_policy_proof(policy_id)` — evaluate canonical policy, inspect the planned corpus, review the evidence/uncertainty card, and inspect raw replay receipts; only an explicit human activation grants authority.
- `hunch_pr_impact(base?)` / `hunch_merge_verdict(...)` — a change's memory surface; would it re-open a closed bug?

**Before the final response — make Hunch's contribution visible:**
- When running a relevant check, use the exact verification_argv launcher returned by hunch_task start, followed by the check command and its arguments, from this worktree. It runs `hunch task verify <task_id> -- <command> [arguments]` using the same installation as MCP, avoiding stale global binaries. This retains the actual exit result and source snapshot; raw output is not stored. Do not rerun an expensive check solely for reporting; missing evidence stays unverified.
- Include the current task_id when calling hunch_record_decision, hunch_record_correction, or hunch_record_finding. The save path records its actual memory home and verifies exact Git revisions when committing or pushing; never infer publication from a successful capture alone.
- Before claiming an application, call `hunch_report(task_id)` and copy the exact occurrence_id, record_id and content_hash from application_references, adding an action you actually took. Never derive an occurrence ID by replacing a receipt prefix or use the task's scope hash as a record hash. If you did not apply a lesson, omit applications.
- Call `hunch_task(action: "finish", task_id, applications?)` and include the returned contribution_card in your final response without the user asking. Render its Markdown evidence link outside any code block so it remains clickable. Copy the card with its evidence link and agent-reported label intact; the structured result contains the card even when the host hides text blocks. Do not replace it with a generic claim that Hunch helped. If presentation_enabled is false, omit the card. A delivered lesson or passing command alone does not prove causal impact.
- If interrupted, finish with `outcome: "interrupted"` when possible. `hunch_report(task_id, html: true)` opens the evidence trail by generating a local file; it may contain private memory and is not a public export. If report tools are unavailable after an update, say so and reconnect the host rather than inventing a report.

**Build the Constitution review queue:**
- `hunch constitution bootstrap --since 90d --max-candidates 3` (CLI) — normalize recent structured human evidence into at most three non-active policy candidates; add `--history` for exact, human-identifier-grounded fix/revert deltas or explicit dependency retirements. Coincidence/ambiguity stays uncompilable; neither path grants authority.
- `hunch constitution ingest --since 90d [--instructions] [--from export.json]` (CLI) — normalize corrections/failures plus bounded committed instructions/ADRs and strict local review/conversation/PR exports into Git-native evidence; raw prose is hash-only, unsupported intent remains uncompilable, and no policy is minted.

**After deciding / when corrected:**
- `hunch_capture_decision(topic?)` → `hunch_record_decision(...)` — interview first, then write; status `proposed` = roadmap intent (shows in `hunch now`).
- `hunch_record_correction(...)` — a human correction becomes an ENFORCED rule (Never Twice), not a one-session memory.
- `hunch_record_finding(...)` — an OBSERVATION with no code change (an audit that found a gap, a measured number, an incident) becomes durable memory anchored to a date + evidence; `/audit` runs the ritual.
- `hunch_timeline(target)` — decision history when investigating how something evolved.

### ⛔ Top invariants (do not break)
- **[warning]** Never spawn a subprocess per row and never do O(all sessions/conversations) work uncached on a path that scans ~/.claude/projects or session state; gate by candidacy (recent-mtime window), cache by (mtime, size) persisted to disk, batch subprocess calls into one _(scope: ccc_server/ask.py; con_0496274e58)_
- **[warning]** server.py changes require restarting BOTH the dashboard (com.github.claude-command-center) AND the worker (com.github.claude-command-center.worker), never just the dashboard _(scope: server.py; con_2cc63a5abf)_
- **[warning]** When bounding a headless `claude -p` subprocess to a read-only toolset, `--allowedTools` alone does NOT restrict the toolset — you must also pass `--disallowedTools` to actually block Bash/Write/Edit/etc; `--allowedTools "Read,Grep,Glob"` combined with `--permission-mode dontAsk` still let the model run Bash successfully in a direct empirical test _(scope: **; con_418e0377d4)_
- **[warning]** Never spawn a subprocess per row and never do O(all sessions/conversations) work uncached on a path that scans ~/.claude/projects or session state; gate by candidacy, cache by (mtime, size), batch subprocess calls _(scope: server.py; con_627861dec9)_
- **[warning]** Never git add -A, git add ., or git commit -a in this repo; stage by explicit path and commit with git commit --only, and for partial-file staging (git apply --cached / git add -p) commit immediately after with no other commands in between _(scope: **; con_9ff65026e6)_
- **[warning]** Never `git add -A`, `git add .`, or `git commit -a` in this repo; stage by explicit path and commit with `git commit --only <paths>` _(scope: **; con_db5f0fc0be)_
- **[warning]** Never add a manual refresh button to fix UI staleness in CCC; fix the staleness at its source with auto-refresh instead _(scope: **; con_e3a02ac292)_
- **[advisory]** Fix broken infra/tooling (a script, a launchd job, a missing dependency) the same turn you find it — don't ask the user first and don't just report it _(scope: **; con_cc564ad105)_

_Hunch updates itself from commits and test failures. Records carry provenance + confidence; treat low-confidence items as advisory._
<!-- HUNCH:END -->
