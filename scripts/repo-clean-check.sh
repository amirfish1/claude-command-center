#!/usr/bin/env bash
# repo-clean-check.sh — strict "is this repo fully landed?" check.
# Prints one BLOCK/WARN line per problem; exits 1 only if there is a BLOCK. Read-only apart from `git fetch`.
#
# BLOCK (exit 1): not on main, uncommitted/untracked files, unpushed commits on main.
# WARN (exit 0): extra worktrees, local branches with commits on no remote.
#
# Usage:   scripts/repo-clean-check.sh <repo-path>
# Remote:  ssh host "sudo -u user -H bash -s -- <repo-path>" < scripts/repo-clean-check.sh
set -uo pipefail
r="${1:?usage: repo-clean-check.sh <repo-path>}"
g() { git -c safe.directory="$r" -C "$r" "$@"; }
bad=0
# BLOCK = would lose or leave work out of a release (fails the check).
# WARN  = housekeeping (stale worktrees, side branches); reported, never blocks.
p() { echo "BLOCK $r: $*"; bad=1; }
w() { echo "WARN $r: $*"; }
g rev-parse --git-dir >/dev/null 2>&1 || { p "not a git repo"; exit 1; }
g fetch --quiet origin 2>/dev/null || p "cannot fetch origin"
[ "$(g branch --show-current)" = "main" ] || p "on '$(g branch --show-current)', not main"
dirty="$(g status --porcelain)"
[ -z "$dirty" ] || p "$(echo "$dirty" | wc -l | tr -d ' ') uncommitted/untracked file(s): $(echo "$dirty" | head -5 | tr '\n' ';')"
ahead="$(g rev-list --count @{u}..HEAD 2>/dev/null || echo '?')"
[ "$ahead" = 0 ] || p "$ahead unpushed commit(s) on main"
extra="$(g worktree list --porcelain | grep '^worktree ' | tail -n +2)"
[ -z "$extra" ] || w "open worktree(s): $(echo "$extra" | sed 's/^worktree //' | tr '\n' ' ')"
# Unpushed = reachable from the branch but from no remote ref. Branches already
# pushed (even if squash-merged, so "not merged" by ancestry) are fine.
unpushed="$(g for-each-ref --format='%(refname:short)' refs/heads | while read -r b; do
  [ "$(g rev-list --count "$b" --not --remotes 2>/dev/null)" != 0 ] && echo "$b"; done)"
[ -z "$unpushed" ] || w "local branch(es) with commits on no remote: $(echo "$unpushed" | tr '\n' ' ')"
exit $bad
