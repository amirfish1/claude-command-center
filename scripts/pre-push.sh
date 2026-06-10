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

PY="$(command -v python3 || true)"
[ -z "$PY" ] && { echo "pre-push: python3 not found, skipping perf gate"; exit 0; }

echo "pre-push: running perf-budget gate…"
if ! "$PY" -m pytest tests/test_perf_budget.py -q --tb=short; then
  echo ""
  echo "❌ perf-budget gate FAILED — a hot path lost its gating/caching."
  echo "   Restore the gate; don't relax the bound. (Emergency bypass: git push --no-verify)"
  exit 1
fi
echo "pre-push: perf gate passed ✓"
