# changelog.d/ — one snippet per change

Drop a small markdown file in this directory for every user-visible change
instead of editing `CHANGELOG.md` directly. Two parallel sessions can land
changes without colliding on the `[Unreleased]` section.

## Filename convention

`<category>-<short-slug>-<discriminator>.md`

- **category** — `added` / `changed` / `fixed` / `removed` / `security` / `deprecated`
- **short-slug** — 2-4 words describing the change (`context-pill`, `slug-encoder-fix`)
- **discriminator** — a date (`2026-04-26`) or short hash; just enough to
  guarantee two sessions writing about different things on the same day
  pick different filenames

Examples:

```
changelog.d/added-context-pill-2026-04-26.md
changelog.d/fixed-slug-encoder-2026-04-25.md
changelog.d/changed-icebox-rename-2026-04-22.md
```

The category prefix decides which `### Added` / `### Fixed` / etc. group
the bullet lands in at release time. The rest of the filename is human
breadcrumb only — never shown.

## File contents

Just the bullet text. No headers, no frontmatter. A leading `- ` is
optional; the release script adds one if missing.

```markdown
**Context-usage pill** above the input bar (`ctx 74k / 200k (37%)`).
New `GET /api/session/<id>/usage` endpoint walks the JSONL summing
`input_tokens + cache_creation + cache_read` per assistant turn.
Click the pill to toggle 200k ↔ 1M context-limit assumption.
```

Multi-line is fine — Markdown wrapping rules apply.

## Release flow

```bash
python3 scripts/release.py 0.2.0
```

That:
1. Reads every `*.md` under `changelog.d/` (skipping `README.md`).
2. Groups bullets by category prefix.
3. Inserts a fresh `## [0.2.0] - YYYY-MM-DD` block above the most recent
   release entry in `CHANGELOG.md`.
4. `git rm`s the snippet files.
5. Leaves `[Unreleased]` and the version bumps in `pyproject.toml` /
   `server.py:__version__` to you (they're separate concerns — the
   script doesn't touch them).

After it runs, review the diff, commit, then `git tag vX.Y.Z` and
`gh release create vX.Y.Z` per the project's existing release ceremony.
