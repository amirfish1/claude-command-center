#!/usr/bin/env bash
# Schedule the Instinct daily brief (ccc_server/instinct.py) once a day.
#
#   scripts/instinct-schedule.sh              # print the unit/plist, change nothing
#   scripts/instinct-schedule.sh --install    # write + enable it for this user
#   scripts/instinct-schedule.sh --uninstall  # remove it
#   INSTINCT_AT=07:30 scripts/instinct-schedule.sh --install
#
# Linux: a systemd --user service + timer. macOS: a LaunchAgent. The job only
# reads (git, the local CCC API, `wt ... --json`) and writes the brief under
# ~/.claude/command-center/instinct/. It files no tickets and sends nothing;
# publishing needs `publish_command` in the config plus INSTINCT_PUBLISH=1.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AT="${INSTINCT_AT:-07:00}"
MODE="${1:-print}"
PY="$(command -v python3)"
HOUR="${AT%%:*}"; MIN="${AT##*:}"
if ! [[ "$HOUR" =~ ^[0-9]{1,2}$ && "$MIN" =~ ^[0-9]{2}$ ]] \
   || (( 10#$HOUR > 23 || 10#$MIN > 59 )); then
  echo "instinct-schedule: INSTINCT_AT must be HH:MM (got '$AT')" >&2; exit 2
fi
EXTRA=""
[ "${INSTINCT_PUBLISH:-0}" = "1" ] && EXTRA=" --publish"
LOG_DIR="$HOME/.claude/command-center/logs"

xml_escape() { local s="${1//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"; printf '%s' "$s"; }

if [ "$(uname)" = "Darwin" ]; then
  LABEL="com.github.claude-command-center.instinct"
  TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
  PUB_ARG=""
  [ -n "$EXTRA" ] && PUB_ARG="<string>--publish</string>"
  BODY="<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$(xml_escape "$REPO_ROOT")</string>
  <key>ProgramArguments</key><array>
    <string>$(xml_escape "$PY")</string><string>-m</string><string>ccc_server.instinct</string><string>brief</string>$PUB_ARG
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$(xml_escape "$HOME")/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$((10#$HOUR))</integer><key>Minute</key><integer>$((10#$MIN))</integer>
  </dict>
  <key>StandardOutPath</key><string>$(xml_escape "$LOG_DIR")/instinct.out.log</string>
  <key>StandardErrorPath</key><string>$(xml_escape "$LOG_DIR")/instinct.err.log</string>
</dict></plist>"
  case "$MODE" in
    print|--print) echo "# $TARGET"; echo "$BODY" ;;
    --install)
      mkdir -p "$(dirname "$TARGET")" "$LOG_DIR"
      echo "$BODY" > "$TARGET"
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
      # bootout is asynchronous; bootstrap right after it often fails with
      # "5: Input/output error", so retry briefly before giving up.
      for _ in 1 2 3 4 5; do
        launchctl bootstrap "gui/$(id -u)" "$TARGET" 2>/dev/null && break
        sleep 1
      done
      launchctl print "gui/$(id -u)/$LABEL" >/dev/null
      echo "installed $TARGET (daily at $AT)" ;;
    --uninstall)
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
      rm -f "$TARGET"; echo "removed $TARGET" ;;
    *) echo "usage: $0 [--print|--install|--uninstall]" >&2; exit 2 ;;
  esac
  exit 0
fi

UNIT_DIR="$HOME/.config/systemd/user"
case "$REPO_ROOT$PY$HOME" in
  *[[:space:]]*) echo "instinct-schedule: paths with spaces are not supported in systemd units" >&2
                 echo "  ($REPO_ROOT)" >&2; exit 2 ;;
esac
SERVICE="[Unit]
Description=CCC Instinct daily brief

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$PY -m ccc_server.instinct brief$EXTRA
StandardOutput=append:$LOG_DIR/instinct.out.log
StandardError=append:$LOG_DIR/instinct.err.log"
TIMER="[Unit]
Description=Run the CCC Instinct brief daily at $AT

[Timer]
OnCalendar=*-*-* $AT:00
Persistent=true

[Install]
WantedBy=timers.target"

case "$MODE" in
  print|--print)
    echo "# $UNIT_DIR/ccc-instinct.service"; echo "$SERVICE"; echo
    echo "# $UNIT_DIR/ccc-instinct.timer"; echo "$TIMER" ;;
  --install)
    mkdir -p "$UNIT_DIR" "$LOG_DIR"
    echo "$SERVICE" > "$UNIT_DIR/ccc-instinct.service"
    echo "$TIMER" > "$UNIT_DIR/ccc-instinct.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now ccc-instinct.timer
    echo "installed ccc-instinct.timer (daily at $AT)"
    if [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != "yes" ]; then
      echo "note: user timers stop when you log out; to keep it running:"
      echo "  loginctl enable-linger $(id -un)"
    fi ;;
  --uninstall)
    systemctl --user disable --now ccc-instinct.timer 2>/dev/null || true
    rm -f "$UNIT_DIR/ccc-instinct.service" "$UNIT_DIR/ccc-instinct.timer"
    systemctl --user daemon-reload
    echo "removed ccc-instinct units" ;;
  *) echo "usage: $0 [--print|--install|--uninstall]" >&2; exit 2 ;;
esac
