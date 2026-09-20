---
globs: "**/*"
---
# Git and commits (always on)

Parallel agent sessions share `main` on this clone. See `CLAUDE.md` § Git commits
for the full tier table. Short form:

- **Mid-work: Tier A lean commit** — `git commit --only <your-paths> -m "type(scope): subject"` then stop (no `changelog.d/`, version bump, or push in that turn).
- **When:** slice done or before idle — not every turn. Slash command: `/lean-commit`.
- **Tier B:** feature built or bug fixed → add a `changelog.d/` snippet if user-visible (separate commit or next turn); never edit `CHANGELOG.md` by hand. Then push.
- **Always push at the end of a feature or bug fix** (`git push origin main`, targeted tests passing) — don't wait to be asked. No push for half-done WIP; never force-push `main`; never `--no-verify` past the pre-push gate.
- **Release (Tier C):** version bump + `scripts/cut-release.sh` only when cutting a release.
