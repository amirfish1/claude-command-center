#!/usr/bin/env bash
# release-trigger.sh — ssh forced-command entry for the daily release gate.
# macOS keychain (gh token, codesign, notarytool) is unreachable from an ssh
# session, so this only kicks the LaunchAgent ai.ccc.release-daily, which runs
# in the GUI session, then waits for its verdict and prints it.
# Remote usage:  ssh mac            (real run)   |   ssh mac -- --dry-run
set -uo pipefail
if [ "${CCC_RELEASE_TRIGGER_LOCKED:-}" != 1 ]; then
  exec python3 "$(dirname "$0")/release_backfill.py" trigger
fi
LABEL="ai.ccc.release-daily"
MODE_FILE="$HOME/.claude/command-center/release-daily.mode"
LOG="$HOME/Library/Logs/ccc-release/$(date +%Y-%m-%d).log"
mkdir -p "$(dirname "$LOG")"; touch "$LOG"
# The Python entry holds an OS lock instead of replacing another request's mode file.
request="${SSH_ORIGINAL_COMMAND:-}"
backfill_tag=""; backfill_token=""
if [[ "$request" = backfill* ]]; then
  if [[ "$request" =~ ^backfill\ (v[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    request="$request $(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
  fi
  if [[ "$request" =~ ^backfill\ (v[0-9]+\.[0-9]+\.[0-9]+)\ ([a-f0-9]{32})$ ]]; then
    backfill_tag="${BASH_REMATCH[1]}"; backfill_token="${BASH_REMATCH[2]}"
  else
    echo "FAILED: expected backfill vX.Y.Z <32-character request token>"; exit 2
  fi
elif [ -n "$request" ] && [ "$request" != "--dry-run" ]; then
  echo "FAILED: unsupported release command"; exit 2
fi
mkdir -p "$(dirname "$MODE_FILE")"
if [ -n "$request" ]; then
  printf '%s\n' "$request" > "${MODE_FILE}.$$"
  mv "${MODE_FILE}.$$" "$MODE_FILE"
else
  rm -f "$MODE_FILE"
fi
start="$(wc -l < "$LOG")"
# Do not kill an ongoing signing/notarization run.
launchctl kickstart "gui/$(id -u)/$LABEL" || { echo "FAILED: cannot start $LABEL (is the LaunchAgent installed and the Mac logged in?)"; exit 1; }
# Wait up to 45 min for a verdict line written after we started.
for _ in $(seq 1 540); do
  sleep 5
  if [ -n "$backfill_tag" ]; then
    verdict="$(tail -n +"$((start+1))" "$LOG" | grep -E " BACKFILL-(OK|FAILED) $backfill_tag $backfill_token$" | tail -1)"
  else
    verdict="$(tail -n +"$((start+1))" "$LOG" | grep -E ' (WAIVED|RELEASED|DRY-RUN-OK|FAILED)' | tail -1)"
  fi
  if [ -n "$verdict" ]; then
    case "$verdict" in *FAILED*) echo "$verdict"; exit 1 ;; esac
    if [ -n "$backfill_tag" ]; then echo "BACKFILL-OK $backfill_tag $backfill_token"; else echo "$verdict"; fi
    exit 0
  fi
done
echo "FAILED: no verdict within 45 min (see $LOG)"; exit 1
