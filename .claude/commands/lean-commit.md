---
description: Tier-A lean commit — only your paths, no changelog.d/push in same turn
allowed-tools: Bash(git diff:*), Bash(git status:*), Bash(git commit:*), Bash(scripts/lean-commit.sh:*)
---

Perform a **lean commit** on the shared `main` clone. Read `CLAUDE.md` § Git commits if anything is unclear.

## What “lean commit” means

- **Do:** `git commit --only <paths> -m "type(scope): subject"` then stop.
- **Do not:** add `changelog.d/` snippets, edit `CHANGELOG.md`, bump `pyproject.toml` / `server.py` version, push, or a full `git status` tour — in this turn.
- **When:** user invoked `/lean-commit` or asked to land work — commit **only files you changed in this session** (or paths they passed in `$ARGUMENTS`).

## Context (minimal)

- Branch: !`git branch --show-current`
- Candidate paths (noise filtered): !`scripts/lean-commit.sh`
- Short stat for candidates only — run yourself: `git diff --stat -- <paths>` (do not paste entire repo status)

## Steps

1. **Paths**
   - If `$ARGUMENTS` lists paths, use those (must be files you actually changed).
   - Else infer from this session’s edits; cross-check with `scripts/lean-commit.sh` only as a hint — do not commit unrelated paths.
   - Never `git add -A`, `git add .`, or `git commit -a`.

2. **Message** — Conventional, under ~72 chars. Match existing scopes: `feat(ui)`, `fix(layout)`, `fix(ci)`, `chore`, `docs`, `perf`, etc.

3. **Commit** — one command:
   ```bash
   git commit --only path/one path/two -m "type(scope): subject"
   ```

4. **Reply** — one short line: `Lean commit <sha> — <subject>` and list paths committed. Do not push in this turn — the push comes when the feature or fix is finished (`CLAUDE.md` § Push).

If there is nothing to commit for your paths, say so in one line — do not commit other sessions’ files.

**User-visible slice done?** Add a `changelog.d/` file in a **separate** turn (Tier B), not bundled into this lean commit.
