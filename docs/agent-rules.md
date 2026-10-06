# Agent rules: full reference

The always-loaded summary is `CLAUDE.md`. This file keeps the long-form
rationale, tables, and incident notes it points to.

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
  origin, so an unpushed commit is undeployed. After pushing, verify the remote
  ref matches local `HEAD` (`git rev-parse HEAD` and
  `git ls-remote origin refs/heads/main`) before reporting completion. If the
  gate or verification fails, fix it or don't claim the change shipped.
  (Fork/PR contributors push and verify their branch, not `main`.)
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

**Cutting a release: run `./scripts/cut-release.sh X.Y.Z`.** One command does the whole sequence — changelog rollup, version bump (both files), tag + push, GitHub release, notarized DMG + Sparkle appcast, and the Homebrew formula bump (auto-computes the sha256). Always `--dry-run` first. Full reference and prereqs in `docs/RELEASING.md`. Don't hand-run the 8 steps unless the wrapper can't (the manual path is the fallback).

## API contracts

`/api/*` endpoints are the stable surface external tooling (Claude Code hooks, the browser UI, pkood integration) binds to. Treat them like public API:

- Adding a field to a response is fine.
- Adding a new endpoint is fine.
- Renaming a field, removing a field, or changing a response shape is a **breaking change** — major version bump, and update SECURITY.md / README.md.
- `/api/repo/switch` has an allow-list for CSRF defence. Don't loosen without re-reading the comment at the call site.

## Security posture

Read `SECURITY.md` before changing anything about network binding, origin checks, or path validation. Summary:
- Default bind is `127.0.0.1`. `CCC_BIND_HOST=0.0.0.0` requires opt-in + prints a warning.
- Same-origin check on every POST (`_check_same_origin`).
- `/api/open` clamps paths to explicit repo/session context and command-center log directories.

## Conventions

- `server.py` is stdlib-only on purpose — no pip dependencies at runtime. Don't import `requests`, `pydantic`, `fastapi`, etc. `urllib` + `http.server` + `json` cover it.
- `static/index.html` is a single-file app by design (no bundler, no npm). Inline CSS/JS is expected. Don't split it into modules without a strong reason.
- `hooks/` scripts run inside Claude Code's hook pipeline — they must exit fast and never prompt.
- The Morning view (`morning.py`, `morning_store.py`, `static/morning/`) is a **gitignored opt-in plugin** for one user's workflow. Don't reference it in the README or treat it as part of the core.

## Never block a turn on a polling loop

Don't wait for something by holding a foreground Bash call open:

```bash
# WRONG — holds the turn open for hours
while true; do wt ls -q QUEUE ...; sleep 120; done
```

A foreground tool child keeps the turn alive, and CCC treats a live turn as
"input will land at the next boundary". A loop that polls for minutes or hours
means that boundary never arrives, so every message queued to that session sits
on "sending…" for as long as the loop runs. Three of these in one session held
its queue for over four hours.

Use `run_in_background: true`, or the `Monitor` tool, or just end the turn and
check on the next one. If a loop genuinely must run in the foreground, bound it
to minutes — never hours.

(`_tool_child_blocks_inject` now force-delivers after 10 minutes, so this
degrades instead of wedging. Don't rely on it: it's a backstop, not a licence.)

## Testing

### Fast Local Unit Tests vs. CI Smoke Suite
- **Local Machine**: Always run **fast, targeted unit tests** for the specific module you are touching (e.g. `python3 -m pytest tests/test_<feature>.py`). Targeted tests finish in < 1s with minimal memory and zero disk lockups.
- **Heavy End-to-End Suites (`tests/test_smoke.py`)**: Do **not** run full `tests/test_smoke.py` in the background during local development. It consumes > 4GB RAM, spawns multiple subprocesses and mock servers, and chokes local disk I/O, freezing the local Command Center server.
- **GitHub Actions CI**: Full multi-OS compile checks (`py-compile`), the complete unit test suite (`unittest`), and the end-to-end server smoke tests (`smoke`) run automatically on GitHub Actions runners in isolated cloud VMs on every push and PR.

Don't mock external systems (`gh`, `claude`, `pkood`) in unit tests. Keep tests focused on fast import-time correctness and specific module invariants.

Running the suite locally with stdlib `python3 -m unittest discover` (Python 3.12+) floods the output with thousands of `ResourceWarning: unclosed database` lines — the test suite reloads `server.py`/`ccc_server` modules many times per run, and each reload drops the previous module's cached sqlite3 connections without closing them. `unittest.main()`'s `TestProgram` defaults to `warnings='default'` whenever `sys.warnoptions` is empty, and that `simplefilter('default')` call wipes any `warnings.filterwarnings()` set inside the test package before the run starts — so filtering from Python code doesn't stick. Set `sys.warnoptions` yourself via the environment instead, which suppresses the flood without hiding real assertion failures:

```bash
PYTHONWARNINGS="ignore::ResourceWarning" python3 -m unittest discover
```

### Browser / UI verification

To verify UI changes visually, this repo uses **puppeteer** (dependency `puppeteer`), via `snapshot.js` — `node snapshot.js` launches headless Chrome, loads `http://127.0.0.1:8090`, and writes `snapshot.png`. Puppeteer's browser lives in `~/.cache/puppeteer` (separate from any Playwright cache). The `chrome-devtools` MCP also works (drives real Chrome) for interactive checks.

CCC uses Puppeteer 25, which no longer exposes `page.waitForTimeout()`. For a
short delay in an ad-hoc verification script, use
`await new Promise((resolve) => setTimeout(resolve, ms))`; prefer
`page.waitForSelector()`, `page.waitForFunction()`, or `page.waitForNetworkIdle()`
when a specific condition is available.

**Do not reach for Playwright.** It is *not* a CCC dependency — "cannot import playwright" / "Playwright browser executable missing" means you picked the wrong tool, not that something is broken. Use `snapshot.js` or chrome-devtools. **Chromium is sufficient**; no WebKit/Firefox needed.

## Performance gates

Every "CCC is slow" incident has been the same bug: a user-facing path doing
`O(all conversations/sessions)` work — a subprocess fork (`ps`/`lsof`/`gh`/`git`),
a full transcript parse, or a whole-list rebuild — **per item, uncached**.
Invisible at test scale (tiny fixtures), seconds in production (1000+ transcripts).

Rules when touching any path that scans `~/.claude/projects` or session state:
- **Gate by candidacy**: only do live/liveness work for sessions that could be
  live (`_discover_live_session_ids()` + a recent-mtime window), not all rows.
- **Cache by `(mtime, size)`** and persist to disk (see `_conv_meta_cache`,
  `_STATS_FILE_CACHE`) so a restart re-parses only changed files.
- **Never spawn a subprocess per row**; batch (one `ps -A`) or memoise.
- **Pass the cheap flags**: don't trigger PR/worktree/effective resolution for a
  view that doesn't render them.

`tests/test_perf_budget.py` enforces this with call-count invariants (not just
latency). The committed `scripts/pre-push.sh` runs it before every push (shared
gate via `.git/hooks/pre-push`; a fresh clone has no hook until you run
`scripts/install-git-hooks.sh`). If it fails, restore the gate — don't relax the
bound. Add a call-count test there for any new all-conversations/all-sessions path.

## Restart matrix — report this on EVERY fix

A committed fix is not a live fix. Python code is loaded once at process
start, so a change sits inert until the process that runs it is restarted.
**End every fix with these three lines**, so nobody has to guess whether what
they just changed is actually running:

```
Dashboard server restart needed:  Y/N
Worker restart needed:            Y/N
WatchTower server restart needed: Y/N
```

How to decide — the three services and what each one loads:

| Service | launchd label | Runs | Restart when you touched |
|---|---|---|---|
| **Dashboard** | `com.github.claude-command-center` | `server.py` + the HTTP API | `server.py` or any module it imports |
| **Worker** | `com.github.claude-command-center.worker` | `ccc_worker.py`, owns engine execution + the shared Codex app-server | `ccc_worker.py`, `worker_engines.py`, `control_plane.py` — **and `server.py`**, see the gotcha below |
| **WatchTower** | `ai.watchtower.watcher` | the `wt` queue daemon on `:8787` | WatchTower's own code (separate repo). CCC changes never need this — default **N** |

**The gotcha that gets missed:** `worker_engines.py` does a lazy `import server`
(`EngineHost._legacy()`), so the worker runs its **own copy** of `server.py`'s
module-level state. A `server.py` fix that runs on an engine path is therefore
**Y for both** the dashboard and the worker. Restarting only the dashboard
leaves the old code live in the worker, which looks exactly like "the fix
didn't work."

**No restart needed (default N everywhere):** `static/*` (served from disk per
request — a browser reload is enough), `docs/`, `changelog.d/`, `tests/`,
markdown. Frontend-only fixes are `N/N/N`.

```bash
launchctl kickstart -k gui/$(id -u)/com.github.claude-command-center.worker
launchctl kickstart -k gui/$(id -u)/com.github.claude-command-center
```

Order does not matter: on launch `run.sh` compares the worker's loaded code
fingerprint (`server.py` + `ccc_server/*.py`) to the repo, kickstarts a stale
worker, and waits for the new worker pid before starting the dashboard. So
`kickstart`-ing only the dashboard also refreshes a stale worker; the second
command above is just the explicit form. Restarting the worker marks running
queue items "needs reconciliation" (one click on Reconcile), so only do it when
the change actually requires it, including via this automatic path.

## Finishing a change — does it need a deploy?

Depends entirely on what you touched. Most changes ship the moment you `git push origin main`. Only `.app`-shell changes need a real release.

| You touched… | How users get it | What you owe |
|---|---|---|
| `server.py`, `static/`, `hooks/`, `install.sh`, `run.sh` (server + dashboard + install) | curl users: next `./run.sh` (install does `git pull --ff-only`). brew users: next `brew upgrade ccc`. DMG users: same path — the .app spawns `~/.ccc/.../run.sh` which is git-tracked. | Just `git push origin main`. No DMG rebuild, no release. |
| `docs/` (landing page, public docs) | GitHub Pages picks it up in ~1 min after push | `git push origin main`. |
| `docs/appcast.xml` | Same as `docs/` — but this is what Sparkle reads. | Push, then verify `curl -s https://ccc.amirfish.ai/appcast.xml` returns the new entry. |
| `scripts/macapp/main.swift`, `scripts/build-dmg.sh`, `scripts/release-dmg.sh`, `scripts/macapp/vendor/Sparkle.framework` (the .app shell or DMG build flow) | **DMG users get it ONLY via Sparkle auto-update**, which only fires when you ship a new versioned DMG with an EdDSA signature in the appcast. | Bump version → `./scripts/release-dmg.sh X.Y.Z` → `gh release create vX.Y.Z` with the DMG attached → commit + push `docs/appcast.xml`. See `docs/RELEASING.md` for the full sequence. |
| `infra/telemetry-worker/` (Cloudflare Worker) | The Worker is independent of `main`. Pushing the repo does NOT deploy it. | `cd infra/telemetry-worker && npx wrangler deploy`. |
| `infra/card-worker/` (Cloudflare Worker) | Independent of `main`; pushing does NOT deploy it. Hosts the share card page (Workers KV `CARDS`). | `cd infra/card-worker && npx wrangler deploy`. |
| Homebrew formula | Formula lives at `github.com/amirfish1/homebrew-ccc`, NOT this repo. | Push there (separate repo). brew users get it on `brew upgrade ccc`. |
| `changelog.d/*`, `tests/`, `README.md`, `CLAUDE.md`, `AGENTS.md`, `pyproject.toml`/`server.py` version bumps | On push to main | Just `git push origin main`. Bumping versions touches a release cycle — see `docs/RELEASING.md`. |

**Quick rule of thumb:**
- Touched anything in `scripts/macapp/` or `scripts/build-dmg.sh`? → **You owe a Sparkle release** (`docs/RELEASING.md`).
- Touched `infra/telemetry-worker/`? → **Run `wrangler deploy`** separately.
- Touched `infra/card-worker/`? → **Run `wrangler deploy`** separately.
- Everything else? → **`git push origin main`** and you're done.

If you're unsure, default to pushing then checking the table — `git push` is reversible (`git revert`); a half-shipped release is harder to clean up.

## Invariants

- Bounding a headless `claude -p` to read-only tools: `--allowedTools` alone does NOT restrict the toolset; also pass `--disallowedTools` (Bash/Write/Edit/etc).
- Never add a manual refresh button to fix UI staleness; fix the staleness at its source with auto-refresh.

## Restart matrix: launchd bundle-ID collision

**If `launchctl kickstart` says "Could not find service" for the dashboard
label**, don't assume the fix is `./run.sh --install-service` — check
`pgrep -f "MacOS/CCC"` first. The .app shares its launchd Label with the app's
bundle identifier (`com.github.claude-command-center`), so while the .app is
open, `launchctl bootstrap` for that same Label always fails with a bare
`Bootstrap failed: 5: Input/output error` (bundle-ID collision in the gui/<uid>
session — confirmed via `launchctl dumpstate | grep application.com.github...`
showing an active per-PID "application" domain for the running app). The
.app self-manages its own `server.py` child whenever nothing else is already
serving the port — quit and relaunch the .app to restore it instead of
fighting the launchd install path. `run.sh` now detects this and prints the
same guidance (OPS-1246).
