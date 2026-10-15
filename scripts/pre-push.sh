#!/usr/bin/env bash
# Performance-regression gate. Runs the perf-budget tests before any push so a
# sibling session can't ship an ungated O(all-conversations) hot path (the
# recurring "CCC is slow" bug class). Fast (~6s); committed so every clone gets
# the same gate. A thin .git/hooks/pre-push shim calls this; hooks are not
# versioned, so install it per clone with scripts/install-git-hooks.sh.
#
# Install pytest before pushing; an unavailable gate must stop the push.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# git exports GIT_DIR (and friends) into a hook's environment so the hook
# itself operates on the right repo. That's correct for git's own purposes,
# but it leaks into every subprocess this script spawns — including pytest's
# `git init`/`git commit` calls in throwaway tmp_path fixtures (e.g.
# test_ship_graph_second_call_does_no_reparse_or_subprocesses). An inherited
# GIT_DIR overrides directory-based repo discovery, so those fixture git
# commands silently operate on THIS repo's real .git instead of the fresh tmp
# one, producing spurious commit failures only reproducible via `git push`
# (never via a direct `pytest` invocation, which has no such env leak).
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR 2>/dev/null || true

# Hunch was removed on purpose (twice). Sessions kept re-adding it after seeing
# leftovers, so block any push that brings it back.
if git ls-files --error-unmatch .hunch >/dev/null 2>&1 || \
   grep -qs 'HUNCH:START' CLAUDE.md AGENTS.md || \
   grep -qs '"hunch"' .mcp.json; then
  echo "pre-push: Hunch is permanently removed from this repo; do not re-add .hunch/, its CLAUDE.md/AGENTS.md block, or a .mcp.json entry."
  exit 1
fi

# Total Recall / Token Optimizer wiring was removed on purpose. This checks
# for reintroduced plumbing (state-dir paths, the old subprocess/API names),
# not mere prose mentions of the tools -- docs/skills-ecosystem* and the
# /api/skills catalog in server.py are allowed to still name them as
# third-party packs in the ecosystem.
tr_to_hits="$(git grep -n -I -E 'total-recall/|brain\.dist|/api/brain/|alexgreensh-token-optimizer|token-optimizer@' \
  -- . \
  ':(exclude)CHANGELOG.md' \
  ':(exclude)changelog.d/**' \
  ':(exclude)docs/release-notes/**' \
  ':(exclude)docs/product-story/**' \
  ':(exclude)scripts/pre-push.sh' \
  2>/dev/null | grep -v -E 'assertNotIn|assertFalse' || true)"
if [ -n "$tr_to_hits" ]; then
  echo "pre-push: reintroduced Total Recall / Token Optimizer wiring:"
  echo "$tr_to_hits"
  echo "That integration was removed; use CCC's own ccc recall instead."
  exit 1
fi

# Test fixtures set user.name "Test User" in tmp repos; a leaked GIT_DIR once
# wrote that into this clone's .git/config, and ~180 commits shipped under it.
# Refuse to push any commit authored or committed as the fixture identity.
zero=0000000000000000000000000000000000000000
while read -r _lref lsha _rref rsha; do
  [ "$lsha" = "$zero" ] && continue
  if [ "$rsha" = "$zero" ]; then range="$lsha --not --remotes"; else range="$rsha..$lsha"; fi
  # shellcheck disable=SC2086
  bad="$(git log --format='%h %ae %ce %s' $range 2>/dev/null | grep -E ' test@example\.com ' || true)"
  if [ -n "$bad" ]; then
    echo "pre-push: commits use the test fixture identity (test@example.com):"
    echo "$bad"
    echo "Check 'git config --show-origin user.email', fix it, then amend these commits."
    exit 1
  fi
done

if [ ! -f tests/test_perf_budget.py ]; then
  exit 0  # nothing to gate on this checkout
fi

PY=""
for candidate in "$REPO_ROOT/.venv/bin/python3" $(type -aP python3 2>/dev/null); do
  [ -x "$candidate" ] || continue
  if "$candidate" -c "import pytest" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "pre-push: no python3 with pytest found (checked .venv and PATH); push blocked." >&2
  echo "Install pytest in .venv or a python3 on PATH, then retry the push." >&2
  exit 1
fi

echo "pre-push: running perf-budget gate…"
if ! "$PY" -m pytest tests/test_perf_budget.py -q --tb=short; then
  echo ""
  echo "❌ perf-budget gate FAILED — a hot path lost its gating/caching."
  echo "   Restore the gate; don't relax the bound."
  exit 1
fi
echo "pre-push: perf gate passed ✓"
