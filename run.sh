#!/usr/bin/env bash
# Claude Command Center launcher.
#
# Watched repo resolution (highest priority first):
#   1. CCC_WATCH_REPO env var if set explicitly
#   2. $PWD if you cd'd into a project before launching (the common case)
#   3. ~/.claude/command-center/last-repo.txt — the last picker selection
#
# Rule (2) is skipped when launched from the CCC install dir itself, because
# being in the source tree is "system" cwd, not a target — that case defers
# to the persisted selection so restarting CCC keeps the repo you were on.
#
# Usage:
#   ./run.sh                       # watch $PWD (or persisted), port 8090
#   CCC_WATCH_REPO=~/dev/foo ./run.sh
#   PORT=9000 ./run.sh
#   CCC_BIND_HOST=0.0.0.0 ./run.sh # advanced: expose on LAN (no auth — see SECURITY.md)
#   CCC_BIND_HOST=0.0.0.0 \
#     CCC_ALLOWED_ORIGIN=http://my-mac.tailnet.ts.net:8090 ./run.sh
#                                  # advanced: reach the UI from a phone over Tailscale.
#                                  # Comma-separated; exact match against the browser Origin.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LAST_REPO_FILE="$HOME/.claude/command-center/last-repo.txt"

if [ -z "${CCC_WATCH_REPO:-}" ]; then
  if [ "$PWD" = "$HERE" ] && [ -f "$LAST_REPO_FILE" ]; then
    # Launched from the CCC source tree with a persisted picker selection —
    # let server.py read last-repo.txt instead of forcing $PWD (which would
    # repoint at CCC itself and stomp the user's last-active repo).
    DISPLAY_REPO="$(cat "$LAST_REPO_FILE")"
  else
    export CCC_WATCH_REPO="$PWD"
    DISPLAY_REPO="$PWD"
  fi
else
  DISPLAY_REPO="$CCC_WATCH_REPO"
fi

export PORT="${PORT:-8090}"
export CCC_BIND_HOST="${CCC_BIND_HOST:-127.0.0.1}"

mkdir -p "$DISPLAY_REPO/.claude/logs"

echo "→ Command Center"
echo "  watching : $DISPLAY_REPO"
echo "  port     : $PORT"
echo "  bind     : $CCC_BIND_HOST"
echo "  url      : http://localhost:$PORT"

exec python3 "$HERE/server.py"
