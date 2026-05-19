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

service_domain() {
  echo "gui/$(id -u)"
}

service_target() {
  echo "$(service_domain)/$PLIST_LABEL"
}

xml_escape() {
  local val="$1"
  val="${val//&/&amp;}"
  val="${val//</&lt;}"
  val="${val//>/&gt;}"
  printf '%s' "$val"
}

append_env_entry() {
  local key="$1"
  local val="$2"
  if [ -z "$val" ]; then
    return
  fi
  val="$(xml_escape "$val")"
  env_block+="    <key>$key</key>"$'\n'
  env_block+="    <string>$val</string>"$'\n'
}

append_path_dir() {
  local dir="$1"
  case ":$service_path:" in
    *":$dir:"*) ;;
    *) service_path="${service_path:+$service_path:}$dir" ;;
  esac
}

launchctl_supports_bootstrap() {
  local help
  help="$(launchctl help 2>&1 || true)"
  case "$help" in
    *bootstrap*) return 0 ;;
    *) return 1 ;;
  esac
}

unload_service() {
  if launchctl_supports_bootstrap; then
    launchctl bootout "$(service_target)" >/dev/null 2>&1 \
      || launchctl bootout "$(service_domain)" "$PLIST_PATH" >/dev/null 2>&1 \
      || true
  fi
  launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
}

load_service() {
  if launchctl_supports_bootstrap; then
    launchctl bootstrap "$(service_domain)" "$PLIST_PATH"
    launchctl enable "$(service_target)" >/dev/null 2>&1 || true
    launchctl kickstart -k "$(service_target)" >/dev/null 2>&1 || true
  else
    launchctl load "$PLIST_PATH"
  fi
}

write_plist() {
  local target_port="$1"
  mkdir -p "$(dirname "$PLIST_PATH")" "$SERVICE_LOG_DIR"

  local env_block=""
  local service_path="${PATH:-}"
  append_path_dir "/opt/homebrew/bin"
  append_path_dir "/usr/local/bin"
  append_path_dir "/usr/bin"
  append_path_dir "/bin"
  append_env_entry "PATH" "$service_path"
  append_env_entry "PORT" "$target_port"
  append_env_entry "VERCEL_PROJECT" "${VERCEL_PROJECT:-}"
  while IFS='=' read -r var val; do
    case "$var" in
      CCC_*) append_env_entry "$var" "$val" ;;
    esac
  done < <(env | sort)

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
  <key>SoftResourceLimits</key>
  <dict>
    <key>NumberOfFiles</key>
    <integer>2048</integer>
  </dict>
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
  unload_service
  sleep 0.3

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
  echo "  target: $(service_target)"
  echo "  port  : $target_port"
  echo "  logs  : $SERVICE_LOG_DIR/service.{out,err}.log"

  write_plist "$target_port"
  load_service

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
  unload_service
  if launchctl_supports_bootstrap; then
    launchctl disable "$(service_target)" >/dev/null 2>&1 || true
  fi
  rm -f "$PLIST_PATH"
  echo "✓ Service removed."
}

service_status() {
  if [ "$(uname -s)" != "Darwin" ]; then
    echo "Error: --service-status supports macOS only." >&2
    exit 1
  fi

  echo "CCC launchd agent"
  echo "  path  : $PLIST_PATH"
  echo "  target: $(service_target)"
  if [ -f "$PLIST_PATH" ]; then
    echo "  state : installed"
  else
    echo "  state : not installed"
  fi

  if launchctl print "$(service_target)" >/dev/null 2>&1; then
    echo "  loaded: yes"
  else
    echo "  loaded: no"
  fi
}

case "${1:-}" in
  --install-service) install_service; exit 0 ;;
  --uninstall-service) uninstall_service; exit 0 ;;
  --service-status) service_status; exit 0 ;;
  --app)
    # Shortcut: open the dashboard as a chromeless app-style window.
    # Delegates to scripts/open-app.sh; remaining args are forwarded.
    shift
    exec "$HERE/scripts/open-app.sh" "$@"
    ;;
  --help|-h)
    cat <<'EOF'
Usage: ./run.sh [OPTION]

  (no args)            Run CCC in the foreground
  --install-service    Install as a launchd agent that starts at login
  --uninstall-service  Remove the launchd agent
  --service-status     Show launchd install/load status
  --app [...]          Open the dashboard in a chromeless app window.
                       Forwards extra args to scripts/open-app.sh.
                       Example: ./run.sh --app --size 1600x1000
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
