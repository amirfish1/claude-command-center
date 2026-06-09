#!/usr/bin/env bash
# cut-release.sh — one command to ship a full CCC release.
#
# Orchestrates the whole sequence that used to be ~8 manual steps across two
# repos. Each step is the same command docs/RELEASING.md documents by hand;
# this just chains them, fails loud on the first error, and verifies at the end.
#
# Usage:
#   ./scripts/cut-release.sh X.Y.Z              # full release (DMG + brew)
#   ./scripts/cut-release.sh X.Y.Z --notes-file path/to/notes.md
#   ./scripts/cut-release.sh X.Y.Z --dry-run    # print steps, change nothing
#   ./scripts/cut-release.sh X.Y.Z --skip-dmg   # source/brew only, no DMG
#
# Prereqs (see docs/RELEASING.md): Developer ID cert, notarytool profile
# 'ccc-notary', Sparkle EdDSA key in login keychain, gh logged in, and the
# Homebrew tap checked out at $BREW_TAP (default ~/Apps/homebrew-ccc).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VERSION=""
NOTES_FILE=""
DRY_RUN=0
SKIP_DMG=0
SKIP_BREW=0
BREW_TAP="${CCC_BREW_TAP:-$HOME/Apps/homebrew-ccc}"

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --skip-dmg) SKIP_DMG=1 ;;
    --skip-brew) SKIP_BREW=1 ;;
    --notes-file=*) NOTES_FILE="${arg#*=}" ;;
    --notes-file) shift; NOTES_FILE="${1:-}" ;;
    -*) echo "cut-release: unknown flag $arg" >&2; exit 2 ;;
    *) [ -z "$VERSION" ] && VERSION="$arg" ;;
  esac
done

if ! printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "cut-release: version must be X.Y.Z, got '${VERSION:-<none>}'" >&2
  echo "usage: ./scripts/cut-release.sh X.Y.Z [--skip-dmg] [--skip-brew] [--dry-run] [--notes-file F]" >&2
  exit 2
fi

GREEN=$'\033[32m'; YEL=$'\033[33m'; RED=$'\033[31m'; NC=$'\033[0m'
step() { echo "${GREEN}==> $*${NC}"; }
warn() { echo "${YEL}!  $*${NC}"; }
run()  { if [ "$DRY_RUN" = 1 ]; then echo "   ${YEL}[dry-run]${NC} $*"; else eval "$@"; fi; }

PREV_TAG="$(git describe --tags --abbrev=0 2>/dev/null || echo '')"
step "Cutting v${VERSION}  (prev tag: ${PREV_TAG:-none})  dry-run=${DRY_RUN}"

# ── Preflight ───────────────────────────────────────────────────────────────
step "Preflight checks"
[ -z "$(git status --porcelain --untracked-files=no | grep -vE 'README.md|docs/images')" ] \
  || warn "working tree has tracked changes beyond README/demo — they may ride along"
git rev-parse "v${VERSION}" >/dev/null 2>&1 && { echo "${RED}tag v${VERSION} already exists${NC}" >&2; exit 1; } || true
command -v gh >/dev/null || { echo "${RED}gh not found${NC}" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "${RED}gh not authenticated${NC}" >&2; exit 1; }

# ── 1. Changelog rollup ─────────────────────────────────────────────────────
step "1/9  Roll up changelog.d → CHANGELOG.md [${VERSION}]"
# Warn about snippets release.py will silently skip (bad category prefix).
BAD=$(ls changelog.d/ 2>/dev/null | grep -vE '^(added|changed|deprecated|removed|fixed|security|README)' || true)
[ -z "$BAD" ] || warn "these snippets have non-standard prefixes and will be SKIPPED:"$'\n'"$BAD"
run "python3 scripts/release.py ${VERSION}"

# ── 2. Changelog compare-links ──────────────────────────────────────────────
step "2/9  Update CHANGELOG compare-links"
if [ "$DRY_RUN" = 0 ]; then
  python3 - "$VERSION" "$PREV_TAG" <<'PY'
import re, sys
ver, prev = sys.argv[1], sys.argv[2].lstrip('v') or '0.0.0'
p = "CHANGELOG.md"; t = open(p).read()
base = "https://github.com/amirfish1/claude-command-center"
t = re.sub(r'^\[Unreleased\]:.*$',
           f'[Unreleased]: {base}/compare/v{ver}...HEAD\n[{ver}]: {base}/releases/tag/v{ver}',
           t, count=1, flags=re.M)
open(p, "w").write(t)
print(f"   linked [Unreleased]->v{ver} and [{ver}] tag")
PY
fi

# ── 3. Version bump (lockstep) ──────────────────────────────────────────────
step "3/9  Bump version in pyproject.toml + server.py"
run "sed -i '' 's/^version = \".*\"/version = \"${VERSION}\"/' pyproject.toml"
run "sed -i '' 's/^__version__ = \".*\"/__version__ = \"${VERSION}\"/' server.py"
if [ "$DRY_RUN" = 0 ]; then
  grep -q "version = \"${VERSION}\"" pyproject.toml && grep -q "__version__ = \"${VERSION}\"" server.py \
    || { echo "${RED}version bump verification failed${NC}" >&2; exit 1; }
fi

# Landing page (ccc.amirfish.ai) needs no per-release edit: it links to the
# version-stable GitHub redirect releases/latest/download/ccc.dmg. We just
# publish a stable-named ccc.dmg copy in step 7 so that link always resolves.

# ── 4. Commit + tag + push ──────────────────────────────────────────────────
step "4/9  Commit, tag, push main + tag"
run "git add CHANGELOG.md pyproject.toml server.py changelog.d"
run "git commit -m 'chore(release): v${VERSION}' -m 'Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>'"
run "git tag -a v${VERSION} -m 'v${VERSION}'"
run "git push origin main"
run "git push origin v${VERSION}"

# ── 5. GitHub release ───────────────────────────────────────────────────────
step "5/9  Create GitHub release"
if [ -n "$NOTES_FILE" ] && [ -f "$NOTES_FILE" ]; then
  run "gh release create v${VERSION} --title 'v${VERSION}' --notes-file '${NOTES_FILE}'"
else
  warn "no --notes-file given; using the [${VERSION}] CHANGELOG section as notes"
  if [ "$DRY_RUN" = 0 ]; then
    awk "/^## \[${VERSION}\]/{f=1;next} /^## \[/{f=0} f" CHANGELOG.md > /tmp/ccc-relnotes.md
    gh release create "v${VERSION}" --title "v${VERSION}" --notes-file /tmp/ccc-relnotes.md
  fi
fi

# ── 6. DMG: build, sign, notarize, appcast ──────────────────────────────────
if [ "$SKIP_DMG" = 0 ]; then
  step "6/9  Build + notarize + EdDSA-sign DMG, update appcast"
  run "./scripts/release-dmg.sh ${VERSION}"
  step "7/9  Upload DMG + publish appcast"
  run "gh release upload v${VERSION} ccc-v${VERSION}.dmg"
  # Stable-named copy so the website's permanent link
  # (releases/latest/download/ccc.dmg) always resolves — Sparkle/appcast keep
  # the versioned name. This is what lets the landing page never need a bump.
  run "cp ccc-v${VERSION}.dmg ccc.dmg && gh release upload v${VERSION} ccc.dmg --clobber && rm -f ccc.dmg"
  run "git commit --only docs/appcast.xml -m 'chore(release): publish v${VERSION} appcast'"
  run "git push origin main"
else
  warn "6-7/9  --skip-dmg: no DMG, no appcast (DMG users will NOT get this update)"
fi

# ── 8. Homebrew formula ─────────────────────────────────────────────────────
if [ "$SKIP_BREW" = 0 ]; then
  step "8/9  Update Homebrew formula at ${BREW_TAP}"
  if [ ! -f "${BREW_TAP}/Formula/ccc.rb" ]; then
    warn "no formula at ${BREW_TAP}/Formula/ccc.rb — skipping brew (set CCC_BREW_TAP)"
  else
    TARBALL="https://github.com/amirfish1/claude-command-center/archive/refs/tags/v${VERSION}.tar.gz"
    if [ "$DRY_RUN" = 0 ]; then
      step "     computing sha256 of release tarball (waiting for GitHub to publish it)"
      SHA=""
      for i in 1 2 3 4 5 6; do
        SHA="$(curl -fsSL "$TARBALL" | shasum -a 256 | cut -d' ' -f1 || true)"
        [ -n "$SHA" ] && [ ${#SHA} -eq 64 ] && break
        warn "tarball not ready yet (attempt $i); retrying in 5s"; sleep 5
      done
      [ ${#SHA} -eq 64 ] || { echo "${RED}could not fetch tarball sha256${NC}" >&2; exit 1; }
      sed -i '' -E "s#archive/refs/tags/v[0-9.]+\.tar\.gz#archive/refs/tags/v${VERSION}.tar.gz#" "${BREW_TAP}/Formula/ccc.rb"
      sed -i '' -E "s/^([[:space:]]*sha256 \")[a-f0-9]{64}(\")/\1${SHA}\2/" "${BREW_TAP}/Formula/ccc.rb"
      grep -q "$SHA" "${BREW_TAP}/Formula/ccc.rb" || { echo "${RED}brew sha256 update failed${NC}" >&2; exit 1; }
      ( cd "$BREW_TAP" && git add Formula/ccc.rb && git commit -q -m "ccc ${VERSION}" && git push origin HEAD )
      echo "   brew formula → ${VERSION} (sha ${SHA:0:12}…) pushed"
    else
      echo "   ${YEL}[dry-run]${NC} would bump ${BREW_TAP}/Formula/ccc.rb to v${VERSION} + push"
    fi
  fi
else
  warn "8/9  --skip-brew: formula not updated"
fi

# ── 9. Verify ───────────────────────────────────────────────────────────────
step "9/9  Verify"
if [ "$DRY_RUN" = 0 ]; then
  echo "   release:  $(gh release view v${VERSION} --json url --jq .url 2>/dev/null || echo '?')"
  echo "   assets:   $(gh release view v${VERSION} --json assets --jq '[.assets[].name]|join(\", \")' 2>/dev/null || echo '?')"
  echo "   appcast:  $(grep -o 'sparkle:version>[0-9.]*' docs/appcast.xml | head -1)"
  echo "   /api/version: $(curl -fsS http://127.0.0.1:8090/api/version 2>/dev/null || echo '(server not running)')"
  echo "   CI:       run 'gh run list --limit 2' to confirm green"
fi
step "Done — v${VERSION} shipped. 🚀"
