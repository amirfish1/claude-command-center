# Releasing

Short, copy-pasteable checklist for cutting a new version. Not a policy doc — the policy lives in `CLAUDE.md`.

## TL;DR — one command

```bash
./scripts/cut-release.sh X.Y.Z                      # full release: changelog, tag, GH release, DMG, appcast, brew
./scripts/cut-release.sh X.Y.Z --dry-run            # print every step, change nothing (do this first)
./scripts/cut-release.sh X.Y.Z --notes-file notes.md  # hand-written release notes (else uses the CHANGELOG section)
./scripts/cut-release.sh X.Y.Z --skip-dmg           # source/brew only (no notarized DMG / Sparkle update)
```

`cut-release.sh` orchestrates the whole sequence below, fails loud on the first error, auto-computes the Homebrew sha256, and verifies at the end. **Always `--dry-run` first.** Prereqs are the same as the manual steps (Developer ID cert, `ccc-notary` notarytool profile, Sparkle EdDSA key in login keychain, `gh` logged in, Homebrew tap at `~/Apps/homebrew-ccc` or `$CCC_BREW_TAP`).

The manual steps below are the fallback / reference for what the wrapper does.

---

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

## 6. Build + ship the macOS DMG (Sparkle auto-update)

```bash
# Builds, codesigns with Developer ID, notarizes, then EdDSA-signs the
# DMG and updates docs/appcast.xml in one shot:
./scripts/release-dmg.sh X.Y.Z

# Upload to the GitHub release:
gh release upload vX.Y.Z ccc-vX.Y.Z.dmg

# Publish the appcast so users on older versions see the update:
git commit --only docs/appcast.xml -m "chore(release): publish vX.Y.Z appcast"
git push origin main
```

For local iteration without notarization (~10s build):

```bash
./scripts/build-dmg.sh --fast X.Y.Z
```

The Sparkle EdDSA private key lives in the maintainer's macOS login
keychain (account = `+oU5VeStRaidpogMHUktYpr/JxKuSn9wY1xEgN106lY=`, label
"Private key for signing Sparkle updates"). Lose that keychain entry and
you lose the ability to ship auto-updates to users on the current public
key — they'll have to download a fresh DMG with a new `SUPublicEDKey`.

## 7. Verify

- CI is green: `gh run list --limit 3`
- Release page looks right: `gh release view vX.Y.Z --web`
- `/api/version` reports the new number: `curl -s localhost:$PORT/api/version`
- Appcast is live: `curl -s https://amirfish1.github.io/claude-command-center/appcast.xml | grep sparkle:version`

## 8. If something goes wrong

- **Wrong tag pushed:** delete locally and remotely, then redo.
  ```bash
  git tag -d vX.Y.Z
  git push origin :refs/tags/vX.Y.Z
  gh release delete vX.Y.Z --yes   # if the release was already created
  ```
- **Forgot a CHANGELOG entry:** amend the release commit before pushing the tag. Once the tag is pushed, don't amend — ship a follow-up patch.
