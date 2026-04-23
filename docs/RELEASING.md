# Releasing

Short, copy-pasteable checklist for cutting a new version. Not a policy doc — the policy lives in `CLAUDE.md`.

## Pick a version

SemVer. Look at what's under `## [Unreleased]` in `CHANGELOG.md`:

- Any `### Removed` or breaking API change → **major** bump (`0.1.0` → `1.0.0`)
- Any `### Added` for a user-visible feature → **minor** (`0.1.0` → `0.2.0`)
- Only `### Fixed` / `### Security` → **patch** (`0.1.0` → `0.1.1`)

Pre-1.0 is a grey area — breaking changes at 0.x can bump minor instead of major. Use judgment.

## 1. Update the CHANGELOG

Rename the `[Unreleased]` header and add a fresh empty one above it. At the bottom of the file, add the new compare link and update the `[Unreleased]` link.

```diff
 ## [Unreleased]

+## [X.Y.Z] - YYYY-MM-DD
+
+### Fixed
+- ...
+
+### Added
+- ...

 ## [prev] - ...
```

```diff
-[Unreleased]: https://github.com/amirfish1/claude-command-center/compare/vPREV...HEAD
+[Unreleased]: https://github.com/amirfish1/claude-command-center/compare/vX.Y.Z...HEAD
+[X.Y.Z]: https://github.com/amirfish1/claude-command-center/releases/tag/vX.Y.Z
 [prev]: https://github.com/amirfish1/claude-command-center/releases/tag/vPREV
```

## 2. Bump the version in two places

Keep these in lockstep — the smoke test doesn't catch divergence yet.

```bash
# pyproject.toml
sed -i '' 's/^version = ".*"/version = "X.Y.Z"/' pyproject.toml
# server.py
sed -i '' 's/^__version__ = ".*"/__version__ = "X.Y.Z"/' server.py

# Verify
grep -n '__version__\|^version = ' server.py pyproject.toml
```

## 3. Commit

```bash
git add CHANGELOG.md pyproject.toml server.py
git commit -m "chore(release): vX.Y.Z"
```

## 4. Tag + push

```bash
git tag -a vX.Y.Z -m "vX.Y.Z — one-line summary"
git push origin main
git push origin vX.Y.Z
```

## 5. Create the GitHub release

Paste the CHANGELOG section as the release notes.

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes "$(cat <<'EOF'
## Fixed
- ...

## Added
- ...

**Full changelog:** https://github.com/amirfish1/claude-command-center/compare/vPREV...vX.Y.Z
EOF
)"
```

## 6. Verify

- CI is green: `gh run list --limit 3`
- Release page looks right: `gh release view vX.Y.Z --web`
- `/api/version` reports the new number: `curl -s localhost:$PORT/api/version`

## If something goes wrong

- **Wrong tag pushed:** delete locally and remotely, then redo.
  ```bash
  git tag -d vX.Y.Z
  git push origin :refs/tags/vX.Y.Z
  gh release delete vX.Y.Z --yes   # if the release was already created
  ```
- **Forgot a CHANGELOG entry:** amend the release commit before pushing the tag. Once the tag is pushed, don't amend — ship a follow-up patch.
