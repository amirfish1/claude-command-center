# Working in this repo

House rules for Claude Code. Long-form rationale, tables, and incident notes:
`docs/agent-rules.md`. User-facing docs: `README.md`, `CONTRIBUTING.md`.

## Public OSS
Everything ships to github.com/amirfish1/claude-command-center.
- No internal paths, client names, private URLs, PII, or real-looking secrets (use `sk-ant-test-XXXX`).
- Private plans/specs/backlog/agent working docs live in the private `CCC-private-docs` repo.
  Never recreate `docs/superpowers/`, copy private docs here, or link that repo in.
- The Morning view (`morning.py`, `morning_store.py`, `static/morning/`) is a gitignored personal plugin; keep it out of README/core.

## Commits
- Conventional Commits; match scopes in `git log` (`fix(layout)`, `feat(ui)`, `docs`, `chore`, `perf`). Subject ≤70 chars, body says why.
- Stage by path; commit with `git commit --only <paths> -m "..."`. Never `git add -A`/`.`/`commit -a`.
- Ask before `git checkout -- .`, `git restore .`, `git clean -f`, `git reset --hard`. Never force-push `main`; never `--no-verify` past `scripts/pre-push.sh`.
- Commit means push: `git push origin main`, then confirm `git rev-parse HEAD` == `git ls-remote origin refs/heads/main`.
- If a gitignored `CLAUDE.local.md` exists, read and follow it (it overrides this section).
- User-visible change → add `changelog.d/<category>-<slug>-<date>.md` (bullet text only; categories added/changed/fixed/removed/security/deprecated). Never edit `CHANGELOG.md` directly.
- Releases: `./scripts/cut-release.sh X.Y.Z` (`--dry-run` first); see `docs/RELEASING.md`. Version lives in `pyproject.toml` and `server.py` `__version__`.
- What needs a deploy beyond push (`scripts/macapp/`, DMG, Sparkle, `infra/telemetry-worker/`, Homebrew): `docs/agent-rules.md`.

## API and security
- `/api/*` is public API: adding fields/endpoints is fine; renaming/removing/reshaping is a major bump.
- Read `SECURITY.md` before touching binding, origin checks, or path validation. `/api/repo/switch` is a deprecated compatibility shim that returns 410 — repo-scoped APIs must receive an explicit `repo_path` instead.

## Conventions
- `server.py` is stdlib-only (no pip deps at runtime). `static/index.html` is a single-file app (no bundler/npm).
- `hooks/` scripts run in Claude Code's hook pipeline: exit fast, never prompt.
- Bounding headless `claude -p` to read-only tools needs `--disallowedTools` too; `--allowedTools` alone doesn't restrict.
- Fix UI staleness at its source (auto-refresh); never add a manual refresh button.
- Never hold a foreground Bash polling loop for more than minutes; use `run_in_background`, Monitor, or end the turn (a live turn blocks queued input).

## Performance gates
Every "CCC is slow" incident was O(all sessions) work per item, uncached. On any path scanning `~/.claude/projects` or session state:
gate live work by candidacy, cache by `(mtime, size)` persisted to disk, never spawn a subprocess per row, skip flags the view doesn't render.
`tests/test_perf_budget.py` (run by the pre-push gate) enforces call counts; add a test for any new all-sessions path; never relax a bound.

## Credential requests

When a task needs a credential, use the [credential broker](docs/credential-broker.md)
to show a masked dashboard prompt instead of asking for the value in chat or
asking the user to SSH in. Default: `ccc secret request NAME --reason "..."`
saves the value to the CCC Vault and prints only the entry name; then use it
with `ccc vault exec --env VAR=NAME -- cmd`. Only when a program must receive
the value directly, pipe `ccc secret request NAME --reason "..." --return-key`
into a program that accepts credentials on stdin; never let a tool capture the
value into a transcript. Keep names/reasons free of secrets. It requires
explicit consumer integration; it does not intercept login prompts automatically.

## Testing
- Run targeted tests locally: `python3 -m pytest tests/test_<feature>.py`. Never run `tests/test_smoke.py` locally (>4 GB RAM, freezes the server); CI runs it.
- Full unittest run: `PYTHONWARNINGS="ignore::ResourceWarning" python3 -m unittest discover`.
- Don't mock `gh`/`claude`/`pkood` in unit tests.
- UI checks: `node snapshot.js` (puppeteer 25, no `page.waitForTimeout`) or the chrome-devtools MCP. Never Playwright.
