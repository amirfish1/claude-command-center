#!/usr/bin/env bash
# Claude Command Center launcher.
#
# Usage:
#   ./run.sh                     # watch $PWD, port 8090
#   CCC_WATCH_REPO=~/dev/foo ./run.sh
#   PORT=9000 ./run.sh

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export CCC_WATCH_REPO="${CCC_WATCH_REPO:-$PWD}"
export PORT="${PORT:-8090}"

mkdir -p "$CCC_WATCH_REPO/.claude/logs"

echo "→ Command Center"
echo "  watching : $CCC_WATCH_REPO"
echo "  port     : $PORT"
echo "  url      : http://localhost:$PORT"

exec python3 "$HERE/server.py"
