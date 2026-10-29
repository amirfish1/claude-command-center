#!/usr/bin/env bash
set -euo pipefail

if [ "${CCC_E2E_FREE:-}" != "1" ]; then
  printf '%s\n' 'This test calls a real free provider. Set CCC_E2E_FREE=1 to run it.' >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${CCC_PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in python3.14 python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
      PY="$(command -v "$candidate")"
      break
    fi
  done
fi
[ -n "$PY" ] || { printf '%s\n' 'Use Python 3.12 or newer. Set CCC_PYTHON to its path.' >&2; exit 2; }
exec "$PY" "$REPO_ROOT/scripts/e2e_failover.py"
