#!/usr/bin/env bash
# Post-commit driver for scripts/hunch-reindex.mjs (Hunch symbol graph from HEAD).
#
#   scripts/hunch-reindex.sh              run now, print to stdout
#   scripts/hunch-reindex.sh --from-hook  debounced + lock-guarded, logs to
#                                         ~/Library/Logs/hunch-reindex/
#
# Shared-clone rules: many sessions commit within seconds of each other, so
# the hook path sleeps briefly to fold a burst into one index pass, holds a
# mkdir lock so runs never overlap, and re-runs once if HEAD moved while it
# was indexing. Never blocks the commit (the shim backgrounds us).
set -u
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" || exit 0
[ -d .hunch ] || exit 0
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
command -v node >/dev/null 2>&1 || exit 0

LOCK="$ROOT/.hunch/.reindex.lock"     # .hunch/ is gitignored
LOG_DIR="$HOME/Library/Logs/hunch-reindex"
FROM_HOOK=0
[ "${1:-}" = "--from-hook" ] && FROM_HOOK=1

if [ "$FROM_HOOK" = 1 ]; then
  mkdir -p "$LOG_DIR"
  exec >>"$LOG_DIR/$(basename "$ROOT").log" 2>&1
  sleep 15   # fold a commit burst into one pass
fi

# Stale lock (a killed run) older than 10 minutes is reclaimed.
if [ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then
  rmdir "$LOCK" 2>/dev/null
fi
mkdir "$LOCK" 2>/dev/null || { [ "$FROM_HOOK" = 1 ] || echo "hunch-reindex: another run holds $LOCK"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

for _pass in 1 2; do
  START="$(git rev-parse HEAD 2>/dev/null)"
  printf '%s ' "$(date '+%Y-%m-%d %H:%M:%S')"
  node "$ROOT/scripts/hunch-reindex.mjs" || { echo "hunch-reindex: failed (rc=$?)"; exit 0; }
  [ "$(git rev-parse HEAD 2>/dev/null)" = "$START" ] && break
done
exit 0
