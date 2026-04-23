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

## CHANGELOG

Follows [Keep a Changelog](https://keepachangelog.com). Every user-visible change appends a bullet under `## [Unreleased]` as part of the same PR/commit. At release time, `[Unreleased]` is renamed to `[X.Y.Z] - YYYY-MM-DD` and a fresh empty `[Unreleased]` goes above it.

Categories: `Added`, `Changed`, `Fixed`, `Removed`, `Security`, `Deprecated`.

## SemVer

Two places to bump in lockstep:
- `pyproject.toml` — `version = "X.Y.Z"`
- `server.py` — `__version__ = "X.Y.Z"`

Patch for bug fixes. Minor for new features. Major for breaking `/api/*` contracts or breaking CLI flags (`run.sh` / env vars like `CCC_WATCH_REPO`).

Tag as `vX.Y.Z`. `gh release create` with release notes copied from the CHANGELOG section.

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
- `/api/open` clamps paths to `REPO_ROOT` / `LOG_DIR`.

## Conventions

- `server.py` is stdlib-only on purpose — no pip dependencies at runtime. Don't import `requests`, `pydantic`, `fastapi`, etc. `urllib` + `http.server` + `json` cover it.
- `static/index.html` is a single-file app by design (no bundler, no npm). Inline CSS/JS is expected. Don't split it into modules without a strong reason.
- `hooks/` scripts run inside Claude Code's hook pipeline — they must exit fast and never prompt.
- The Morning view (`morning.py`, `morning_store.py`, `static/morning/`) is a **gitignored opt-in plugin** for one user's workflow. Don't reference it in the README or treat it as part of the core.

## Testing

`tests/test_smoke.py` imports `server.py` and checks nothing explodes. CI is minimal by design. If you add a feature, a smoke-level assertion is nice-to-have but not required — the bar is "doesn't break the import."

Don't mock external systems (`gh`, `claude`, `pkood`) in the smoke test. The smoke test is about import-time correctness, not behavior.
