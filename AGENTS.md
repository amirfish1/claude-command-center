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

## Git commits (shared `main`, parallel sessions)

Multiple sessions share one checkout on `main`. **Commit small and often** so
pushing (or **Push all** in the CCC UI) does not require hunting other sessions.

| Tier | When | Do | Do not |
|------|------|-----|--------|
| **A — lean** | Slice done or before idle | `git commit --only <paths> -m "type(scope): subject"` | `changelog.d/`, version bump, push in same turn |
| **B — done** | User-visible slice complete | Tier A + `changelog.d/` snippet | Edit `CHANGELOG.md` by hand |
| **C — release** | Shipping `vX.Y.Z` | `./scripts/cut-release.sh` | Random version bumps |

**`/lean-commit`** — slash command; see `.claude/commands/lean-commit.md`. Helper:
`scripts/lean-commit.sh` (lists candidate paths, noise filtered).

Never `git add -A` / `git commit -a`. Never push unless the user said
push/ship/Push all. Full rules: `CLAUDE.md` § Git commits and
`.claude/rules/git-and-commits.md`.

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
- `static/index.html` is a single-file app by design (no bundler, no npm). Inline CSS/JS is expected. Don't split it into modules without a strong reason.
- In zsh, lowercase `path` is a special array tied directly to `PATH`. Never use
  `path` as a scratch, local, or loop variable in shell diagnostics; use a
  descriptive name such as `target_path` or `candidate_path`. If commands seem
  to disappear after a probe, check `typeset -p path PATH` before changing the
  machine's global environment.
- Flow workspace work (`#flowBoard`, `static/app.js`, `static/app.css`) has
  maintainer notes in `.claude/rules/flow-workspace.md`.
- `hooks/` scripts run inside agent hook pipelines — they must exit fast and never prompt.
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

**Do not use the Codex in-app browser (`iab`) backend or Playwright for this.** `iab` is unavailable outside a desktop app context, and Playwright is not a CCC dependency — "iab browser not available" / "cannot import playwright" means wrong tool, not a breakage. Use `node snapshot.js` (Chromium is sufficient; no WebKit/Firefox needed).

Depends entirely on what you touched. Most changes ship the moment you `git push origin main`. Only `.app`-shell changes need a real release.

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
