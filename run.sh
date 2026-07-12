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

# Optional machine-local env file — not part of the repo, never committed.
# launchctl/systemd `setenv`-style overrides (e.g. soak flags like
# CCC_CHAT_ORCHESTRATOR/CCC_MESSAGING_BACKEND) don't survive a reboot; this
# file does. Sourced before --install-service snapshots CCC_* into the
# plist/unit, so vars set here are baked in the same way a real env var
# would be.
CONFIG_LOCAL_ENV="$HOME/.claude/command-center/config.local.env"
if [ -f "$CONFIG_LOCAL_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_LOCAL_ENV"
  set +a
fi

PLIST_LABEL="com.github.claude-command-center"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
SERVICE_LOG_DIR="$HOME/.claude/command-center/logs"
# Linux (systemd user service) equivalents of the launchd agent above.
SYSTEMD_UNIT_NAME="ccc.service"
SYSTEMD_UNIT_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/${SYSTEMD_UNIT_NAME}"

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

# ── Linux: systemd user service ─────────────────────────────────────────────
systemd_available() {
  command -v systemctl >/dev/null 2>&1
}

write_systemd_unit() {
  local target_port="$1"
  mkdir -p "$(dirname "$SYSTEMD_UNIT_PATH")" "$SERVICE_LOG_DIR"

  # PORT plus any CCC_* (and VERCEL_PROJECT) from the current env, baked in as
  # Environment= lines so the service runs with the same config as this shell.
  local env_lines=""
  env_lines+="Environment=\"PORT=$target_port\""$'\n'
  if [ -n "${VERCEL_PROJECT:-}" ]; then
    env_lines+="Environment=\"VERCEL_PROJECT=$VERCEL_PROJECT\""$'\n'
  fi
  while IFS='=' read -r var val; do
    case "$var" in
      CCC_*) env_lines+="Environment=\"$var=$val\""$'\n' ;;
    esac
  done < <(env | sort)

  # Logs go to journald (journalctl --user -u ccc). Kept version-safe: no
  # append: directive, which needs systemd v240+.
  cat > "$SYSTEMD_UNIT_PATH" <<EOF
[Unit]
Description=Claude Command Center
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HERE
ExecStart=$HERE/run.sh
Restart=on-failure
RestartSec=2
LimitNOFILE=2048
${env_lines}
[Install]
WantedBy=default.target
EOF
}

install_service_linux() {
  if ! systemd_available; then
    cat >&2 <<EOF
Error: systemctl not found. --install-service needs systemd user services.
Run CCC in the foreground, or under your own process manager, instead:

  nohup ./run.sh > "$SERVICE_LOG_DIR/service.out.log" 2>&1 &
EOF
    exit 1
  fi

  local target_port="${PORT:-8090}"

  if is_port_bound "$target_port"; then
    cat >&2 <<EOF
Error: port $target_port is already in use — looks like CCC (or something else)
is running outside the service. Stop it first, then re-run:

  pkill -f 'python3.*server\\.py'   # if it's a foreground ./run.sh
  ./run.sh --install-service
EOF
    exit 1
  fi

  echo "→ Installing CCC as a systemd user service"
  echo "  unit  : $SYSTEMD_UNIT_PATH"
  echo "  port  : $target_port"

  write_systemd_unit "$target_port"
  systemctl --user daemon-reload
  systemctl --user enable --now "$SYSTEMD_UNIT_NAME"

  for _ in 1 2 3 4 5; do
    sleep 0.5
    if is_port_bound "$target_port"; then
      echo "✓ Service started. Open: http://localhost:$target_port"
      echo "  Status   : systemctl --user status $SYSTEMD_UNIT_NAME"
      echo "  Logs     : journalctl --user -u $SYSTEMD_UNIT_NAME -f"
      echo "  Uninstall: ./run.sh --uninstall-service"
      echo
      echo "Headless box, or want it running after you log out and at boot?"
      echo "Enable lingering once (needs sudo):"
      echo "  sudo loginctl enable-linger $USER"
      return 0
    fi
  done

  echo "⚠ Unit started but port $target_port didn't bind in 2.5s." >&2
  echo "  Check: journalctl --user -u $SYSTEMD_UNIT_NAME" >&2
  exit 1
}

uninstall_service_linux() {
  if ! systemd_available; then
    echo "systemctl not found; nothing to uninstall."
    exit 0
  fi
  if [ ! -f "$SYSTEMD_UNIT_PATH" ]; then
    echo "Service is not installed (no unit at $SYSTEMD_UNIT_PATH)."
    exit 0
  fi
  echo "→ Removing CCC systemd user service"
  systemctl --user disable --now "$SYSTEMD_UNIT_NAME" >/dev/null 2>&1 || true
  rm -f "$SYSTEMD_UNIT_PATH"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  echo "✓ Service removed."
}

service_status_linux() {
  echo "CCC systemd user service"
  echo "  unit  : $SYSTEMD_UNIT_PATH"
  if [ -f "$SYSTEMD_UNIT_PATH" ]; then
    echo "  state : installed"
  else
    echo "  state : not installed"
  fi
  if systemd_available && systemctl --user is-active "$SYSTEMD_UNIT_NAME" >/dev/null 2>&1; then
    echo "  active: yes"
  else
    echo "  active: no"
  fi
}

install_service() {
  if [ "$(uname -s)" != "Darwin" ]; then
    install_service_linux
    return
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
  if [ "$(uname -s)" != "Darwin" ]; then
    uninstall_service_linux
    return
  fi
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
    service_status_linux
    return
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
  --install-service    Install as a background service that starts at login
                       (launchd on macOS, systemd user service on Linux)
  --uninstall-service  Remove the background service
  --service-status     Show service install/load status
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

# A launchd service has a minimal PATH and macOS commonly resolves python3 to
# the Xcode-provided 3.9 there.  server.py requires Python 3.10+ for its
# union-type annotations, so select a known-compatible interpreter explicitly.
PYTHON="$HERE/.venv/bin/python3"
for candidate in "$PYTHON" "${CCC_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ] \
    && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done

if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "Error: CCC requires Python 3.10+. Set CCC_PYTHON to a compatible interpreter." >&2
  exit 1
fi

exec "$PYTHON" "$HERE/server.py"
