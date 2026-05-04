#!/usr/bin/env bash
# Claude Command Center launcher.
#
# Usage:
#   ./run.sh                       # port 8090
#   PORT=9000 ./run.sh
#   CCC_BIND_HOST=0.0.0.0 ./run.sh # advanced: expose on LAN (no auth — see SECURITY.md)
#   CCC_BIND_HOST=0.0.0.0 \
#     CCC_ALLOWED_ORIGIN=http://my-mac.tailnet.ts.net:8090 ./run.sh
#                                  # advanced: reach the UI from a phone over Tailscale.
#                                  # Comma-separated; exact match against the browser Origin.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLIST_LABEL="com.github.claude-command-center"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
SERVICE_LOG_DIR="$HOME/.claude/command-center/logs"

is_port_bound() {
  (echo > "/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
}

write_plist() {
  local target_port="$1"
  mkdir -p "$(dirname "$PLIST_PATH")" "$SERVICE_LOG_DIR"

  local env_block=""
  env_block+="    <key>PATH</key>"$'\n'
  env_block+="    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>"$'\n'
  for var in PORT CCC_BIND_HOST CCC_ALLOWED_ORIGIN CCC_TRUST_TAILNET CCC_TITLE_STRIP CCC_ORG_PATTERNS VERCEL_PROJECT CCC_SKIP_SKILL_INSTALL; do
    local val="${!var:-}"
    if [ -n "$val" ]; then
      val="${val//&/&amp;}"; val="${val//</&lt;}"; val="${val//>/&gt;}"
      env_block+="    <key>$var</key>"$'\n'
      env_block+="    <string>$val</string>"$'\n'
    fi
  done

  cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$PLIST_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HERE/run.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$HERE</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$SERVICE_LOG_DIR/service.out.log</string>
  <key>StandardErrorPath</key>
  <string>$SERVICE_LOG_DIR/service.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
$env_block  </dict>
</dict>
</plist>
EOF

  if command -v plutil >/dev/null 2>&1; then
    plutil -lint "$PLIST_PATH" >/dev/null
  fi
}

install_service() {
  if [ "$(uname -s)" != "Darwin" ]; then
    echo "Error: --install-service supports macOS only." >&2
    exit 1
  fi

  local target_port="${PORT:-8090}"

  # Unload any previous version first so re-install is idempotent and the port
  # check below sees the real "is something else holding it" answer.
  if [ -f "$PLIST_PATH" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    sleep 0.3
  fi

  if is_port_bound "$target_port"; then
    cat >&2 <<EOF
Error: port $target_port is already in use — looks like CCC (or something else)
is running outside the service. Stop it first, then re-run:

  pkill -f 'python3.*server\\.py'   # if it's a foreground ./run.sh
  ./run.sh --install-service
EOF
    exit 1
  fi

  echo "→ Installing CCC as a launchd agent"
  echo "  plist : $PLIST_PATH"
  echo "  port  : $target_port"
  echo "  logs  : $SERVICE_LOG_DIR/service.{out,err}.log"

  write_plist "$target_port"
  launchctl load "$PLIST_PATH"

  for _ in 1 2 3 4 5; do
    sleep 0.5
    if is_port_bound "$target_port"; then
      echo "✓ Service started. Open: http://localhost:$target_port"
      echo "  Uninstall: ./run.sh --uninstall-service"
      return 0
    fi
  done

  echo "⚠ Plist loaded but port $target_port didn't bind in 2.5s." >&2
  echo "  Check: $SERVICE_LOG_DIR/service.err.log" >&2
  exit 1
}

uninstall_service() {
  if [ ! -f "$PLIST_PATH" ]; then
    echo "Service is not installed (no plist at $PLIST_PATH)."
    exit 0
  fi
  echo "→ Removing CCC launchd agent"
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  rm -f "$PLIST_PATH"
  echo "✓ Service removed."
}

case "${1:-}" in
  --install-service) install_service; exit 0 ;;
  --uninstall-service) uninstall_service; exit 0 ;;
  --help|-h)
    cat <<'EOF'
Usage: ./run.sh [OPTION]

  (no args)            Run CCC in the foreground
  --install-service    Install as a launchd agent that starts at login
  --uninstall-service  Remove the launchd agent
  --help, -h           Show this help

Env vars (PORT, CCC_BIND_HOST, CCC_ALLOWED_ORIGIN, etc.)
set when running --install-service are baked into the agent's environment.
EOF
    exit 0
    ;;
esac

export PORT="${PORT:-8090}"
# CCC_BIND_HOST is intentionally NOT defaulted here. server.py resolves
# the bind across env, ~/.claude/command-center/network.json, and a built-in
# 127.0.0.1 default — exporting a value here would clobber the JSON layer.

mkdir -p "$SERVICE_LOG_DIR"

echo "→ Command Center"
echo "  port     : $PORT"
echo "  bind     : ${CCC_BIND_HOST:-(default 127.0.0.1, or from network.json)}"
echo "  url      : http://localhost:$PORT"

exec python3 "$HERE/server.py"
