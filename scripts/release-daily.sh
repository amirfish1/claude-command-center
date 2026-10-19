#!/usr/bin/env bash
# release-daily.sh — the once-a-day release gate. Run it on the machine that can
# sign and notarize (it calls scripts/cut-release.sh); trigger it from a timer
# or over ssh from another host.
#
# It WAIVES (exit 0, nothing released) unless all of these hold:
#   1. this repo and every repo in $CCC_RELEASE_REPOS are on main, with no
#      uncommitted changes and nothing unpushed
#   2. changelog.d/ holds at least $CCC_RELEASE_MIN_ENTRIES entries (default 3),
#      at least one of them "added-*" or "fixed-*"
#   3. the last release tag is at least 24h old
# Otherwise it cuts the next version: minor if any "added-*" entry, else patch.
#
# Usage: ./scripts/release-daily.sh [--dry-run]
# Env:   CCC_RELEASE_REPOS  space-separated sibling repos to require clean+pushed
#                           (default: $HOME/Apps/watchtower)
#        CCC_RELEASE_MIN_ENTRIES  default 3
# Exit:  0 released or waived, 1 release attempt failed.
# Log:   ~/Library/Logs/ccc-release/ (macOS) or ~/.local/state/ccc-release/
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
# Also accepts the flag via SSH_ORIGINAL_COMMAND, for a forced-command ssh key.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
DRY=0; { [ "${1:-}" = "--dry-run" ] || [ "${SSH_ORIGINAL_COMMAND:-}" = "--dry-run" ]; } && DRY=1
MIN="${CCC_RELEASE_MIN_ENTRIES:-3}"
EXTRA_REPOS="${CCC_RELEASE_REPOS:-$HOME/Apps/watchtower}"
if [ "$(uname -s)" = "Darwin" ]; then LOG_DIR="$HOME/Library/Logs/ccc-release"; else LOG_DIR="$HOME/.local/state/ccc-release"; fi
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"
say() { echo "$(date '+%H:%M:%S') $*" | tee -a "$LOG"; }
waive() { say "WAIVED: $*"; exit 0; }

LOCK="${TMPDIR:-/tmp}/ccc-release-daily.lock"
mkdir "$LOCK" 2>/dev/null || waive "another release run holds $LOCK"
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

check_repo() {
  local r="$1" branch dirty ahead
  [ -d "$r/.git" ] || [ -f "$r/.git" ] || waive "$r is not a git repo"
  git -C "$r" fetch --quiet origin 2>/dev/null || waive "cannot fetch $r"
  branch="$(git -C "$r" branch --show-current)"
  [ "$branch" = "main" ] || waive "$r is on '$branch', not main"
  dirty="$(git -C "$r" status --porcelain)"
  [ -z "$dirty" ] || waive "$r has uncommitted changes ($(echo "$dirty" | wc -l | tr -d ' ') files)"
  ahead="$(git -C "$r" rev-list --count @{u}..HEAD 2>/dev/null || echo 1)"
  [ "$ahead" = 0 ] || waive "$r has $ahead unpushed commit(s)"
}
check_repo "$REPO_ROOT"
for r in $EXTRA_REPOS; do check_repo "$r"; done

git pull --ff-only --quiet origin main 2>/dev/null || waive "cannot fast-forward $REPO_ROOT"

entries="$(ls changelog.d 2>/dev/null | grep -Ev '^README' | grep -E '\.md$' || true)"
n="$(printf '%s' "$entries" | grep -c . || true)"
[ "$n" -ge "$MIN" ] || waive "only $n changelog entries (need $MIN)"
printf '%s\n' "$entries" | grep -Eq '^(added|fixed)-' || waive "no added-/fixed- entries among $n"

tag="$(git describe --tags --abbrev=0 2>/dev/null || true)"
if [ -n "$tag" ]; then
  age=$(( $(date +%s) - $(git log -1 --format=%ct "$tag") ))
  [ "$age" -ge 86400 ] || waive "last release $tag is only $((age/3600))h old"
fi
prev="${tag#v}"; prev="${prev:-0.0.0}"
IFS=. read -r MA MI PA <<< "$prev"
if printf '%s\n' "$entries" | grep -q '^added-'; then NEXT="$MA.$((MI+1)).0"; else NEXT="$MA.$MI.$((PA+1))"; fi

say "ENOUGH: $n entries since $tag; cutting v$NEXT (dry-run=$DRY)"
if [ "$DRY" = 1 ]; then
  ./scripts/cut-release.sh "$NEXT" --dry-run >>"$LOG" 2>&1; rc=$?
else
  ./scripts/cut-release.sh "$NEXT" >>"$LOG" 2>&1; rc=$?
fi
if [ "$rc" = 0 ]; then say "$([ "$DRY" = 1 ] && echo DRY-RUN-OK || echo RELEASED): v$NEXT"; else say "FAILED: cut-release exit $rc (see $LOG)"; fi
exit "$([ "$rc" = 0 ] && echo 0 || echo 1)"
