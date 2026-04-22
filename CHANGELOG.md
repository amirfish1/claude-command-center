# Changelog

All notable changes to this project will be documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-04-21

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
- Morning view (opt-in) — goals / strategic / tactical surfaces with
  Apple Notes ingestion.

### Security
- `127.0.0.1` bind by default. `CCC_BIND_HOST=0.0.0.0` requires opt-in and
  prints a startup warning.
- Same-origin POST check (Origin header) on every state-changing request.
- `/api/open` clamped to paths under `REPO_ROOT` / `LOG_DIR`. Default action
  is `open -R` (Reveal in Finder), not launch.
- `/api/repo/switch` validates targets against the picker allow-list.
- See [`SECURITY.md`](SECURITY.md) for the full threat model.

[Unreleased]: https://github.com/amirfish1/claude-command-center/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.0
