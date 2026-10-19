#!/usr/bin/env bash
# repo-clean-check.sh — strict "is this repo fully landed?" check.
# Prints one problem per line and exits 1 if any; prints nothing and exits 0
# when the repo is clean. Read-only apart from `git fetch`.
#
# Problems: not on main, uncommitted or untracked files, unpushed commits on
# any local branch, any extra worktree, any local branch with commits that exist on no remote.
#
# Usage:   scripts/repo-clean-check.sh <repo-path>
# Remote:  ssh host "sudo -u user -H bash -s -- <repo-path>" < scripts/repo-clean-check.sh
set -uo pipefail
r="${1:?usage: repo-clean-check.sh <repo-path>}"
g() { git -c safe.directory="$r" -C "$r" "$@"; }
bad=0
p() { echo "$r: $*"; bad=1; }
g rev-parse --git-dir >/dev/null 2>&1 || { p "not a git repo"; exit 1; }
g fetch --quiet origin 2>/dev/null || p "cannot fetch origin"
[ "$(g branch --show-current)" = "main" ] || p "on '$(g branch --show-current)', not main"
dirty="$(g status --porcelain)"
[ -z "$dirty" ] || p "$(echo "$dirty" | wc -l | tr -d ' ') uncommitted/untracked file(s): $(echo "$dirty" | head -5 | tr '\n' ';')"
ahead="$(g rev-list --count @{u}..HEAD 2>/dev/null || echo '?')"
[ "$ahead" = 0 ] || p "$ahead unpushed commit(s) on main"
extra="$(g worktree list --porcelain | grep '^worktree ' | tail -n +2)"
[ -z "$extra" ] || p "open worktree(s): $(echo "$extra" | sed 's/^worktree //' | tr '\n' ' ')"
# Unpushed = reachable from the branch but from no remote ref. Branches already
# pushed (even if squash-merged, so "not merged" by ancestry) are fine.
unpushed="$(g for-each-ref --format='%(refname:short)' refs/heads | while read -r b; do
  [ "$(g rev-list --count "$b" --not --remotes 2>/dev/null)" != 0 ] && echo "$b"; done)"
[ -z "$unpushed" ] || p "local branch(es) with commits on no remote: $(echo "$unpushed" | tr '\n' ' ')"
exit $bad
