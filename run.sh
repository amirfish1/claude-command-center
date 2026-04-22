#!/usr/bin/env bash
# Claude Command Center launcher.
#
# Usage:
#   ./run.sh                       # watch $PWD, port 8090, bind 127.0.0.1
#   CCC_WATCH_REPO=~/dev/foo ./run.sh
#   PORT=9000 ./run.sh
#   CCC_BIND_HOST=0.0.0.0 ./run.sh # advanced: expose on LAN (no auth — see SECURITY.md)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export CCC_WATCH_REPO="${CCC_WATCH_REPO:-$PWD}"
export PORT="${PORT:-8090}"
export CCC_BIND_HOST="${CCC_BIND_HOST:-127.0.0.1}"

mkdir -p "$CCC_WATCH_REPO/.claude/logs"

echo "→ Command Center"
echo "  watching : $CCC_WATCH_REPO"
echo "  port     : $PORT"
echo "  bind     : $CCC_BIND_HOST"
echo "  url      : http://localhost:$PORT"

exec python3 "$HERE/server.py"
