#!/usr/bin/env bash
# release-trigger.sh — ssh forced-command entry for the daily release gate.
# macOS keychain (gh token, codesign, notarytool) is unreachable from an ssh
# session, so this only kicks the LaunchAgent ai.ccc.release-daily, which runs
# in the GUI session, then waits for its verdict and prints it.
# Remote usage:  ssh mac            (real run)   |   ssh mac -- --dry-run
set -uo pipefail
LABEL="ai.ccc.release-daily"
MODE_FILE="$HOME/.claude/command-center/release-daily.mode"
LOG="$HOME/Library/Logs/ccc-release/$(date +%Y-%m-%d).log"
mkdir -p "$(dirname "$LOG")"; touch "$LOG"
rm -f "$MODE_FILE"
[ "${SSH_ORIGINAL_COMMAND:-}" = "--dry-run" ] && echo "--dry-run" > "$MODE_FILE"
start="$(wc -l < "$LOG")"
launchctl kickstart -k "gui/$(id -u)/$LABEL" || { echo "FAILED: cannot start $LABEL (is the LaunchAgent installed and the Mac logged in?)"; exit 1; }
# Wait up to 45 min for a verdict line written after we started.
for _ in $(seq 1 540); do
  sleep 5
  verdict="$(tail -n +"$((start+1))" "$LOG" | grep -E ' (WAIVED|RELEASED|DRY-RUN-OK|FAILED)' | tail -1)"
  if [ -n "$verdict" ]; then echo "$verdict"; case "$verdict" in *FAILED*) exit 1 ;; *) exit 0 ;; esac; fi
done
echo "FAILED: no verdict within 45 min (see $LOG)"; exit 1
