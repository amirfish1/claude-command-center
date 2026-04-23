# Changelog

All notable changes to this project will be documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Each message card in the conversation view now shows a relative timestamp
  next to its line number. Tiers: `just now` (<1 min) → `N minutes ago` (<1 h)
  → `N hours ago` (<5 h) → `HH:MM` (same day, older) → `Yesterday · HH:MM`
  → `MMM D · HH:MM`. Hover reveals the full localized date-time.

### Fixed
- Per-card ✨ "regenerate title" button now shows on every session card that
  has a first user message, not only un-summarized ones. Previously, once a
  card was user-renamed (`name_overridden`), the button was hidden and there
  was no in-UI way back to an AI-generated title. On renamed cards the
  button is dimmed and its tooltip flags the destructive intent
  ("Regenerate title — replaces your manual rename").
- Session → GitHub-issue auto-link no longer uses the jsonl tail
  (`tail_issue_number`) as a last-resort signal. The tail scan matches any
  `gh issue …` command, `Closes #N` commit, or `github.com/.../issues/N`
  URL Claude happens to run mid-conversation, which produced false links
  when an assistant turn merely *discussed* an unrelated issue. Auto-link
  now relies solely on spawn-time identity — `display_name`, the first
  user message, and the branch — where genuine "I'm working on #NNN"
  intent lives. Explicit side-car mappings remain authoritative.
- Haiku title-summarizer subsessions no longer leak into the kanban. The
  `/api/sessions` scan now skips conversations whose first user message
  starts with our internal `Produce a concise 4-8 word title…` prompt,
  so clicking the ✨ Titles button on the CCC repo (or any repo watched
  from the CCC working directory) stops filling the board with identical
  throwaway cards.
- Archived/verified cards no longer flash back into their old column
  briefly after the click. Previously the 10s `/api/sessions` poller
  could overwrite the optimistic `c.archived = true` mutation if a
  request was already in flight when the user clicked. A short-lived
  client-side override map (30s TTL, auto-cleared once the server
  agrees) shields the optimistic value across stale poll responses.
  Fixes both the explicit Archive/Verify buttons and the drag-drop paths.
- `run.sh` no longer clobbers the persisted watched repo when launched
  from the CCC source tree. It used to force `CCC_WATCH_REPO=$PWD`
  unconditionally, which overrode `~/.claude/command-center/last-repo.txt`
  whenever the script ran from its own install dir. Now: explicit env
  var still wins, otherwise `$PWD` wins unless `$PWD` is the install
  dir AND a persisted selection exists — in which case we defer to it.

## [0.1.1] - 2026-04-23

### Fixed
- Chat input at the bottom of the conversation pane was clipped by the fixed
  topbar's 33px body padding — only a 1px border-top sliver showed. The split
  kanban view now sizes to `calc(100vh - 33px)` so the input row is visible.

### Added
- Repo picker now has a "…" button for picking folders the `$HOME` scan
  can't reach (paths outside `~/`, or nested below a top-level dir).
  The picked path is persisted to `~/.claude/command-center/custom-repos.txt`
  via a new `POST /api/repo/add` endpoint and auto-switches on success.

## [0.1.0] - 2026-04-22

Initial public release.

### Added
- Kanban board over all live + dormant Claude Code sessions, classified by
  signals (commit / push / sidecar status / GitHub label).
- GitHub issue → session → verify → close pipeline with attention queue.
- Headless `claude -p` spawn with stdin-pipe follow-up, plus resume-on-demand.
- Optional Vercel deploy polling and auto-fix-deploy.
- Optional [`pkood`](https://github.com/anthropics/pkood) integration for
  background agent runners.
- Repo picker — live-switch the watched repo from the toolbar without restarting.
- AI title regeneration via `claude -p --model haiku`.
### Security
- `127.0.0.1` bind by default. `CCC_BIND_HOST=0.0.0.0` requires opt-in and
  prints a startup warning.
- Same-origin POST check (Origin header) on every state-changing request.
- `/api/open` clamped to paths under `REPO_ROOT` / `LOG_DIR`. Default action
  is `open -R` (Reveal in Finder), not launch.
- `/api/repo/switch` validates targets against the picker allow-list.
- See [`SECURITY.md`](SECURITY.md) for the full threat model.

[Unreleased]: https://github.com/amirfish1/claude-command-center/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.1
[0.1.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.0
