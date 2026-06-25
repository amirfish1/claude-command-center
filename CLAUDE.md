# Working in this repo

This file tells Claude (and external contributors running Claude Code) the house rules. Not user-facing docs — see `README.md` and `CONTRIBUTING.md` for that.

## This is public OSS

Repo lives at `github.com/amirfish1/claude-command-center`. Every commit, comment, file name, and test fixture ships to the world. Assume strangers read it.

- No internal paths, client names, private URLs, or PII in code, comments, or tests.
- No secrets — not even placeholder tokens that "look like" real ones. Use obvious fakes (`sk-ant-test-XXXX`).
- No references to private internal systems. If a feature exists for one user, either generalize it or gitignore it (see the Morning view for the pattern).

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
A commit is **only** git in that turn — no extra ceremony bundled in.

### Tiers — pick one per commit

| Tier | When | Do | Do not |
|------|------|-----|--------|
| **A — lean / WIP** | Slice done, still iterating, or before idle | `git commit --only <paths> -m "type(scope): subject"` | `changelog.d/` in same turn; edit `CHANGELOG.md`; version bump; push |
| **B — slice done** | User-visible fix/feature complete | Same as A; add a `changelog.d/` snippet (same or next commit) | Hand-edit `CHANGELOG.md`; release scripts |
| **C — release** | Cutting `vX.Y.Z` | `./scripts/cut-release.sh` (rollup, version bump, tag) | Ad-hoc version bumps on random commits |

Default to **Tier A** unless the user asked for changelog or release work.

### Lean commit (Tier A)

Use the **`/lean-commit`** slash command or:

```bash
git commit --only path/to/changed path/to/other -m "fix(ui): short subject"
```

- **When:** slice done, or pausing / going idle — **not** after every assistant turn.
- **One command, then stop** — no `changelog.d/`, no push unless the user said
  push/ship/Push all.
- Candidate path list (noise filtered): `scripts/lean-commit.sh`

### Push

- **Do not push** unless the user says push/ship/Push all (or you are the
  designated integrator and the tree is clean).
- If the tree is dirty with others' work, commit **your** paths only and stop.

### CHANGELOG (`changelog.d/`)

- **Tier A:** do not add or edit `changelog.d/` in the same turn as the code commit.
- **Tier B:** drop one small file in `changelog.d/` per user-visible change (see
  `changelog.d/README.md`). Never edit `CHANGELOG.md` directly — release rolls
  snippets up.

### Multi-Agent Git Hygiene

Multiple agent sessions can share one working tree on this machine. The shared
clone stays on `main`.

1. **Never branch in the shared clone** unless the user asked. Use
   `git worktree add` for branch-isolated work.
2. **Never** `git add -A`, `git add .`, or `git commit -a`.
3. **Commit with `--only <paths>`** — the index is shared; plain `git commit -m`
   can sweep in sibling sessions' staged work.

- **NEVER** run `git checkout -- .`, `git restore .`, `git clean -f`, or
  `git reset --hard` without asking first.

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

## Testing

`tests/test_smoke.py` imports `server.py` and checks nothing explodes. CI is minimal by design. If you add a feature, a smoke-level assertion is nice-to-have but not required — the bar is "doesn't break the import."

Don't mock external systems (`gh`, `claude`, `pkood`) in the smoke test. The smoke test is about import-time correctness, not behavior.

### Browser / UI verification

To verify UI changes visually, this repo uses **puppeteer** (dependency `puppeteer`), via `snapshot.js` — `node snapshot.js` launches headless Chrome, loads `http://127.0.0.1:8090`, and writes `snapshot.png`. Puppeteer's browser lives in `~/.cache/puppeteer` (separate from any Playwright cache). The `chrome-devtools` MCP also works (drives real Chrome) for interactive checks.

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
gate via `.git/hooks/pre-push`). If it fails, restore the gate — don't relax the
bound. Add a call-count test there for any new all-conversations/all-sessions path.

## Finishing a change — does it need a deploy?

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
