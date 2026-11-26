#!/usr/bin/env bash
# repo-clean-check.sh — strict "is this repo fully landed?" check.
# Prints one BLOCK/WARN line per problem; exits 1 only if there is a BLOCK. Read-only apart from `git fetch`.
#
# BLOCK (exit 1): not on main, uncommitted/untracked files, unpushed commits on main.
# WARN (exit 0): extra worktrees, local branches with commits on no remote.
#
# Usage:   scripts/repo-clean-check.sh <repo-path> [--block-key]
# Remote:  ssh host "sudo -u user -H bash -s -- <repo-path>" < scripts/repo-clean-check.sh
set -uo pipefail
r="${1:?usage: repo-clean-check.sh <repo-path>}"
g() { git -c safe.directory="$r" -C "$r" "$@"; }
bad=0
block_evidence=""
# BLOCK = would lose or leave work out of a release (fails the check).
# WARN  = housekeeping (stale worktrees, side branches); reported, never blocks.
p() {
  echo "BLOCK $r: $1"
  bad=1
  block_evidence="$block_evidence"$'\n'"${2:-$1}"
}
w() { echo "WARN $r: $*"; }
finish() {
  if [ "$bad" = 1 ] && [ "${1:-}" = "--block-key" ]; then
    printf '%s\0%s' "$r" "$block_evidence" | python3 -c '
import hashlib
import sys

print("BLOCK-KEY", hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
  fi
  exit "$bad"
}
g rev-parse --git-dir >/dev/null 2>&1 || { p "not a git repo"; finish "${2:-}"; }
g fetch --quiet origin 2>/dev/null || p "cannot fetch origin"
branch="$(g branch --show-current)"
[ "$branch" = "main" ] || p "on '$branch', not main"
dirty="$(g status --porcelain)"
if [ -n "$dirty" ]; then
  full_dirty="$dirty"
  if [ "${2:-}" = "--block-key" ]; then
    full_dirty="$(g status --porcelain --untracked-files=all)"
  fi
  p "$(echo "$dirty" | wc -l | tr -d ' ') uncommitted/untracked file(s): $(echo "$dirty" | head -5 | tr '\n' ';')" \
    "uncommitted/untracked: $full_dirty"
fi
if commits="$(g rev-list @{u}..HEAD 2>/dev/null)"; then
  if [ -n "$commits" ]; then
    ahead="$(printf '%s\n' "$commits" | wc -l | tr -d ' ')"
    p "$ahead unpushed commit(s) on main" "unpushed commits: $commits"
  fi
else
  p "? unpushed commit(s) on main"
fi
extra="$(g worktree list --porcelain | grep '^worktree ' | tail -n +2)"
[ -z "$extra" ] || w "open worktree(s): $(echo "$extra" | sed 's/^worktree //' | tr '\n' ' ')"
# Unpushed = reachable from the branch but from no remote ref. Branches already
# pushed (even if squash-merged, so "not merged" by ancestry) are fine.
unpushed="$(g for-each-ref --format='%(refname:short)' refs/heads | while read -r b; do
  [ "$(g rev-list --count "$b" --not --remotes 2>/dev/null)" != 0 ] && echo "$b"; done)"
[ -z "$unpushed" ] || w "local branch(es) with commits on no remote: $(echo "$unpushed" | tr '\n' ' ')"
finish "${2:-}"
