#!/usr/bin/env bash
# Build the real DMG and verify its packaged app can perform a clean first
# install without touching the developer's normal CCC checkout, logs, or port.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
VERSION="0.0.0-native-install-test"
DMG="$REPO_ROOT/ccc-v${VERSION}.dmg"
TEST_ROOT="$(mktemp -d -t ccc-macapp-install)"
TEST_HOME="$TEST_ROOT/home"
INSTALL_DIR="$TEST_HOME/.ccc/claude-command-center"
LOG_DIR="$TEST_HOME/.claude/command-center/logs"
MOUNT_DIR="$TEST_ROOT/mount"
APP_PID=""
SERVER_PID=""
MOUNTED=0

dump_failure_logs() {
  printf '%s\n' '--- native app process log ---' >&2
  sed -n '1,200p' "$TEST_ROOT/app-process.log" >&2 2>/dev/null || true
  printf '%s\n' '--- app/server bootstrap log ---' >&2
  sed -n '1,200p' "$LOG_DIR/app-server.log" >&2 2>/dev/null || true
}

# shellcheck disable=SC2329  # Invoked indirectly by the signal/exit trap below.
cleanup() {
  if [ -n "$APP_PID" ] && kill -0 "$APP_PID" 2>/dev/null; then
    kill -TERM "$APP_PID" 2>/dev/null || true
    wait "$APP_PID" 2>/dev/null || true
  fi
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "$MOUNTED" -eq 1 ]; then
    hdiutil detach "$MOUNT_DIR" >/dev/null 2>&1 || true
  fi
  rm -rf "$TEST_ROOT"
  rm -f "$DMG"
}
trap cleanup EXIT HUP INT TERM

if [ "$(uname -s)" != "Darwin" ]; then
  echo "test-macapp-install: macOS is required" >&2
  exit 2
fi
if [ -e "$DMG" ]; then
  echo "test-macapp-install: refusing to overwrite $DMG" >&2
  exit 1
fi
for command_name in curl git hdiutil lsof python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "test-macapp-install: missing required command: $command_name" >&2
    exit 1
  fi
done

PORT="$(python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"

mkdir -p "$TEST_HOME" "$TEST_ROOT/tmp" "$MOUNT_DIR"
"$HERE/build-dmg.sh" --fast "$VERSION"
hdiutil attach -readonly -nobrowse -mountpoint "$MOUNT_DIR" "$DMG" >/dev/null
MOUNTED=1

APP=""
for candidate in "$MOUNT_DIR"/*.app; do
  if [ -d "$candidate" ]; then
    APP="$candidate"
    break
  fi
done
if [ -z "$APP" ]; then
  echo "test-macapp-install: mounted DMG contains no app bundle" >&2
  exit 1
fi
EXECUTABLE="$APP/Contents/MacOS/CCC"
if [ ! -x "$EXECUTABLE" ]; then
  echo "test-macapp-install: packaged executable is missing" >&2
  exit 1
fi

HOME="$TEST_HOME" \
CFFIXED_USER_HOME="$TEST_HOME" \
TMPDIR="$TEST_ROOT/tmp" \
GIT_TERMINAL_PROMPT=0 \
CCC_INSTALL_DIR="$INSTALL_DIR" \
CCC_REPO_URL="$REPO_ROOT" \
CCC_LOG_DIR="$LOG_DIR" \
CCC_PORT="$PORT" \
  "$EXECUTABLE" >"$TEST_ROOT/app-process.log" 2>&1 &
APP_PID=$!

ready=0
for second in $(seq 1 60); do
  if curl --max-time 1 -fsS \
      "http://127.0.0.1:$PORT/api/version" >"$TEST_ROOT/version.json"; then
    ready=1
    echo "test-macapp-install: ready after ${second}s"
    break
  fi
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "test-macapp-install: app exited before readiness" >&2
    dump_failure_logs
    exit 1
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "test-macapp-install: app never served /api/version" >&2
  dump_failure_logs
  exit 1
fi

curl --max-time 5 -fsS "http://127.0.0.1:$PORT/" >"$TEST_ROOT/dashboard.html"
grep -q '<title>Command Center' "$TEST_ROOT/dashboard.html"
grep -qx dmg "$TEST_HOME/.claude/command-center/install-source"
test -d "$INSTALL_DIR/.git"
if compgen -G "${INSTALL_DIR}.installing.*" >/dev/null; then
  echo "test-macapp-install: atomic-clone staging path remains" >&2
  exit 1
fi

SERVER_PID="$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN | head -n 1)"
if [ -z "$SERVER_PID" ]; then
  echo "test-macapp-install: could not identify the app-owned server" >&2
  exit 1
fi

kill -TERM "$APP_PID"
wait "$APP_PID" 2>/dev/null || true
APP_PID=""
for _ in $(seq 1 20); do
  if ! curl --max-time 1 -fsS \
      "http://127.0.0.1:$PORT/api/version" >/dev/null 2>&1; then
    SERVER_PID=""
    echo "test-macapp-install: packaged first launch passed"
    exit 0
  fi
  sleep 0.25
done

echo "test-macapp-install: server still running after app exit" >&2
dump_failure_logs
exit 1
