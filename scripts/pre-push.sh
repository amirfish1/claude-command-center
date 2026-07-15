#!/usr/bin/env bash
# Performance-regression gate. Runs the perf-budget tests before any push so a
# sibling session can't ship an ungated O(all-conversations) hot path (the
# recurring "CCC is slow" bug class). Fast (~6s); committed so every clone gets
# the same gate. Installed at .git/hooks/pre-push (a thin shim calls this).
#
# Bypass for a genuine emergency: git push --no-verify
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

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
[ -z "$PY" ] && { echo "pre-push: no python3 with pytest found (checked .venv and PATH), skipping perf gate"; exit 0; }

echo "pre-push: running perf-budget gate…"
if ! "$PY" -m pytest tests/test_perf_budget.py -q --tb=short; then
  echo ""
  echo "❌ perf-budget gate FAILED — a hot path lost its gating/caching."
  echo "   Restore the gate; don't relax the bound. (Emergency bypass: git push --no-verify)"
  exit 1
fi
echo "pre-push: perf gate passed ✓"
