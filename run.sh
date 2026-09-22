#!/usr/bin/env bash
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
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
WORKER_PLIST_LABEL="com.github.claude-command-center.worker"
WORKER_PLIST_PATH="$HOME/Library/LaunchAgents/${WORKER_PLIST_LABEL}.plist"
# Optional: only installed when a `kimi` CLI is found on PATH at install time.
# This is Kimi's own daemon (`kimi web`), not CCC code — CCC's kap transport
# (ccc_server/kap.py) adopts whatever live instance it finds registered under
# ~/.kimi-code/server/instances/, so keeping it running is what lets that
# transport engage instead of silently falling back to the ACP path.
KIMI_WEB_PLIST_LABEL="com.github.claude-command-center.kimi-web"
KIMI_WEB_PLIST_PATH="$HOME/Library/LaunchAgents/${KIMI_WEB_PLIST_LABEL}.plist"
SERVICE_LOG_DIR="$HOME/.claude/command-center/logs"
# Linux (systemd user service) equivalents of the launchd agent above.
SYSTEMD_UNIT_NAME="ccc.service"
SYSTEMD_UNIT_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/${SYSTEMD_UNIT_NAME}"
WORKER_SYSTEMD_UNIT_NAME="ccc-worker.service"
WORKER_SYSTEMD_UNIT_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/${WORKER_SYSTEMD_UNIT_NAME}"
KIMI_WEB_SYSTEMD_UNIT_NAME="ccc-kimi-web.service"
KIMI_WEB_SYSTEMD_UNIT_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/${KIMI_WEB_SYSTEMD_UNIT_NAME}"

is_port_bound() {
  (echo > "/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
}

service_domain() {
  echo "gui/$(id -u)"
}

service_target() {
  echo "$(service_domain)/$PLIST_LABEL"
}

worker_service_target() {
  echo "$(service_domain)/$WORKER_PLIST_LABEL"
}

kimi_web_service_target() {
  echo "$(service_domain)/$KIMI_WEB_PLIST_LABEL"
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

load_worker_service() {
  # Never restart a healthy worker merely because the dashboard plist changed:
  # it owns durable agent execution across dashboard upgrades.
  if launchctl print "$(worker_service_target)" >/dev/null 2>&1; then
    return
  fi
  if launchctl_supports_bootstrap; then
    launchctl bootstrap "$(service_domain)" "$WORKER_PLIST_PATH"
    launchctl enable "$(worker_service_target)" >/dev/null 2>&1 || true
  else
    launchctl load "$WORKER_PLIST_PATH"
  fi
}

unload_worker_service() {
  if launchctl_supports_bootstrap; then
    launchctl bootout "$(worker_service_target)" >/dev/null 2>&1 \
      || launchctl bootout "$(service_domain)" "$WORKER_PLIST_PATH" >/dev/null 2>&1 \
      || true
  fi
  launchctl unload "$WORKER_PLIST_PATH" >/dev/null 2>&1 || true
}

load_kimi_web_service() {
  # Same rule as the worker: a CCC dashboard reinstall must not bounce an
  # already-running kimi-web daemon and drop whatever session it's serving.
  if launchctl print "$(kimi_web_service_target)" >/dev/null 2>&1; then
    return
  fi
  if launchctl_supports_bootstrap; then
    launchctl bootstrap "$(service_domain)" "$KIMI_WEB_PLIST_PATH"
    launchctl enable "$(kimi_web_service_target)" >/dev/null 2>&1 || true
  else
    launchctl load "$KIMI_WEB_PLIST_PATH"
  fi
}

unload_kimi_web_service() {
  if launchctl_supports_bootstrap; then
    launchctl bootout "$(kimi_web_service_target)" >/dev/null 2>&1 \
      || launchctl bootout "$(service_domain)" "$KIMI_WEB_PLIST_PATH" >/dev/null 2>&1 \
      || true
  fi
  launchctl unload "$KIMI_WEB_PLIST_PATH" >/dev/null 2>&1 || true
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

  local python_bin
  python_bin="$(command -v python3)"
  cat > "$WORKER_PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$WORKER_PLIST_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$python_bin</string>
    <string>$HERE/ccc_worker.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$HERE</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$SERVICE_LOG_DIR/worker.out.log</string>
  <key>StandardErrorPath</key>
  <string>$SERVICE_LOG_DIR/worker.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
$env_block  </dict>
</dict>
</plist>
EOF
  if command -v plutil >/dev/null 2>&1; then
    plutil -lint "$WORKER_PLIST_PATH" >/dev/null
  fi
}

# Resolves the `kimi` CLI once at install time and bakes the full path into
# the plist, same reason the worker plist bakes a resolved python3: a launchd
# job's PATH is minimal and must not depend on inheriting this shell's PATH.
# Returns 1 (writes nothing) when kimi isn't installed — the caller treats
# that as "skip, not an error", since most CCC installs won't have Kimi.
write_kimi_web_plist() {
  local kimi_bin
  kimi_bin="$(command -v kimi 2>/dev/null || true)"
  if [ -z "$kimi_bin" ]; then
    return 1
  fi
  mkdir -p "$(dirname "$KIMI_WEB_PLIST_PATH")" "$SERVICE_LOG_DIR"
  cat > "$KIMI_WEB_PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$KIMI_WEB_PLIST_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$kimi_bin</string>
    <string>web</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$SERVICE_LOG_DIR/kimi-web.out.log</string>
  <key>StandardErrorPath</key>
  <string>$SERVICE_LOG_DIR/kimi-web.err.log</string>
</dict>
</plist>
EOF
  if command -v plutil >/dev/null 2>&1; then
    plutil -lint "$KIMI_WEB_PLIST_PATH" >/dev/null
  fi
  return 0
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
After=network-online.target $WORKER_SYSTEMD_UNIT_NAME
Wants=network-online.target $WORKER_SYSTEMD_UNIT_NAME

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

  cat > "$WORKER_SYSTEMD_UNIT_PATH" <<EOF
[Unit]
Description=Claude Command Center persistent execution worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HERE
ExecStart=/usr/bin/env python3 $HERE/ccc_worker.py
Restart=always
RestartSec=2
LimitNOFILE=2048
${env_lines}
[Install]
WantedBy=default.target
EOF
}

# Same gate as write_kimi_web_plist: skip (return 1) rather than error when
# the `kimi` CLI isn't on PATH.
write_kimi_web_systemd_unit() {
  local kimi_bin
  kimi_bin="$(command -v kimi 2>/dev/null || true)"
  if [ -z "$kimi_bin" ]; then
    return 1
  fi
  mkdir -p "$(dirname "$KIMI_WEB_SYSTEMD_UNIT_PATH")" "$SERVICE_LOG_DIR"
  cat > "$KIMI_WEB_SYSTEMD_UNIT_PATH" <<EOF
[Unit]
Description=Kimi Code kap-server (web) daemon, adopted by CCC's kap transport
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$kimi_bin web
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
  return 0
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

  # Stop any previous version of the units first so re-install is idempotent
  # and the port check below sees the real "is something else holding it"
  # answer — same reason install_service (macOS) unloads before checking.
  # Without this, re-running --install-service to repair a partial install
  # (e.g. a missing worker unit) always fails: the dashboard unit it already
  # installed is still bound to the port.
  systemctl --user stop "$SYSTEMD_UNIT_NAME" "$WORKER_SYSTEMD_UNIT_NAME" >/dev/null 2>&1 || true
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

  echo "→ Installing CCC as a systemd user service"
  echo "  unit  : $SYSTEMD_UNIT_PATH"
  echo "  worker: $WORKER_SYSTEMD_UNIT_PATH"
  echo "  port  : $target_port"

  write_systemd_unit "$target_port"
  systemctl --user daemon-reload
  systemctl --user enable --now "$WORKER_SYSTEMD_UNIT_NAME"
  systemctl --user enable --now "$SYSTEMD_UNIT_NAME"

  if write_kimi_web_systemd_unit; then
    echo "  kimi  : $KIMI_WEB_SYSTEMD_UNIT_PATH (kimi CLI found — enabling kap connector daemon)"
    systemctl --user daemon-reload
    systemctl --user enable --now "$KIMI_WEB_SYSTEMD_UNIT_NAME" >/dev/null 2>&1 || true
  fi

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
  if [ ! -f "$SYSTEMD_UNIT_PATH" ] && [ ! -f "$WORKER_SYSTEMD_UNIT_PATH" ]; then
    echo "Service is not installed."
    exit 0
  fi
  echo "→ Removing CCC systemd user service"
  systemctl --user disable --now "$SYSTEMD_UNIT_NAME" >/dev/null 2>&1 || true
  systemctl --user disable --now "$WORKER_SYSTEMD_UNIT_NAME" >/dev/null 2>&1 || true
  systemctl --user disable --now "$KIMI_WEB_SYSTEMD_UNIT_NAME" >/dev/null 2>&1 || true
  rm -f "$SYSTEMD_UNIT_PATH" "$WORKER_SYSTEMD_UNIT_PATH" "$KIMI_WEB_SYSTEMD_UNIT_PATH"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  echo "✓ Service removed."
}

service_status_linux() {
  echo "CCC systemd user service"
  echo "  unit  : $SYSTEMD_UNIT_PATH"
  echo "  worker: $WORKER_SYSTEMD_UNIT_PATH"
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
  if systemd_available && systemctl --user is-active "$WORKER_SYSTEMD_UNIT_NAME" >/dev/null 2>&1; then
    echo "  worker active: yes"
  else
    echo "  worker active: no"
  fi
  if [ -f "$KIMI_WEB_SYSTEMD_UNIT_PATH" ]; then
    echo "  kimi-web unit : $KIMI_WEB_SYSTEMD_UNIT_PATH"
    if systemd_available && systemctl --user is-active "$KIMI_WEB_SYSTEMD_UNIT_NAME" >/dev/null 2>&1; then
      echo "  kimi-web active: yes"
    else
      echo "  kimi-web active: no"
    fi
  else
    echo "  kimi-web unit : not installed (kimi CLI not found at install time)"
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
  echo "  worker: $WORKER_PLIST_PATH"
  echo "  target: $(service_target)"
  echo "  port  : $target_port"
  echo "  logs  : $SERVICE_LOG_DIR/service.{out,err}.log"

  write_plist "$target_port"
  load_worker_service
  load_service

  if write_kimi_web_plist; then
    echo "  kimi  : $KIMI_WEB_PLIST_PATH (kimi CLI found — enabling kap connector daemon)"
    load_kimi_web_service
  fi

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
  if [ ! -f "$PLIST_PATH" ] && [ ! -f "$WORKER_PLIST_PATH" ]; then
    echo "Service is not installed."
    exit 0
  fi
  echo "→ Removing CCC launchd agent"
  unload_service
  unload_worker_service
  unload_kimi_web_service
  if launchctl_supports_bootstrap; then
    launchctl disable "$(service_target)" >/dev/null 2>&1 || true
  fi
  rm -f "$PLIST_PATH" "$WORKER_PLIST_PATH" "$KIMI_WEB_PLIST_PATH"
  echo "✓ Service removed."
}

service_status() {
  if [ "$(uname -s)" != "Darwin" ]; then
    service_status_linux
    return
  fi

  echo "CCC launchd agent"
  echo "  path  : $PLIST_PATH"
  echo "  worker: $WORKER_PLIST_PATH"
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
  if launchctl print "$(worker_service_target)" >/dev/null 2>&1; then
    echo "  worker loaded: yes"
  else
    echo "  worker loaded: no"
  fi

  if [ -f "$KIMI_WEB_PLIST_PATH" ]; then
    echo "  kimi-web plist: $KIMI_WEB_PLIST_PATH"
    if launchctl print "$(kimi_web_service_target)" >/dev/null 2>&1; then
      echo "  kimi-web loaded: yes"
    else
      echo "  kimi-web loaded: no"
    fi
  else
    echo "  kimi-web plist: not installed (kimi CLI not found at install time)"
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
                       (launchd on macOS, systemd user service on Linux).
                       If a `kimi` CLI is found on PATH, also installs a
                       supervised `kimi web` daemon so CCC's kap transport
                       (ccc_server/kap.py) has something to adopt instead
                       of silently falling back to the ACP path.
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

# A launchd service has a minimal PATH, so select a known-compatible Python
# 3.9+ interpreter explicitly instead of relying on its inherited environment.
PYTHON="$HERE/.venv/bin/python3"
for candidate in "$PYTHON" "${CCC_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ] \
    && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done

if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  echo "Error: CCC requires Python 3.9+. Set CCC_PYTHON to a compatible interpreter." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# WatchTower bootstrap
# ---------------------------------------------------------------------------
# WatchTower is CCC's queue engine — without it the dashboard silently loses
# worker dispatch (filed tickets never spawn anything), plan-to-fleet import,
# WT-tracked drain, and delivery receipts. The installers install it, but they
# are not the only way in: Homebrew, the DMG, Docker, and a plain `git clone`
# all reach the product through THIS script. Bootstrapping here is what makes
# "installed by default" true for every path rather than just one of them.
#
# The chain itself lives in scripts/install-watchtower.sh, shared with
# scripts/install.sh so there is exactly one definition of it. This wrapper's
# only job is to keep the happy path free: an import check plus a stat, no
# fork of the installer and no network on a launch where nothing is due.
ensure_watchtower() {
  case "${CCC_SKIP_WATCHTOWER:-0}" in
    1|true|True|yes|Yes) return 0 ;;
  esac
  local script="$HERE/scripts/install-watchtower.sh"
  if [ ! -f "$script" ]; then
    return 0
  fi
  local ccc_version
  ccc_version="$(grep -m1 '^__version__ = ' "$HERE/server.py" 2>/dev/null | sed -E 's/^__version__ = "(.*)"$/\1/')"
  # Fast path: already importable, already checked today, AND CCC has not
  # been upgraded since the last check. That third condition is what makes a
  # fresh `brew upgrade`/Sparkle/`git pull` install pick up WatchTower right
  # away instead of waiting out the rest of today's rate-limit window — see
  # wt_ccc_version_changed in install-watchtower.sh, which owns this marker.
  if "$PYTHON" -c 'import watchtower.queue' >/dev/null 2>&1; then
    local marker="$HOME/.claude/command-center/watchtower-last-check"
    local version_marker="$HOME/.claude/command-center/watchtower-last-ccc-version"
    if [ -f "$marker" ] && [ -n "$(find "$marker" -mtime -1 2>/dev/null)" ] \
      && { [ -z "$ccc_version" ] || [ "$(cat "$version_marker" 2>/dev/null || true)" = "$ccc_version" ]; }; then
      return 0
    fi
  fi
  CCC_PYTHON="$PYTHON" CCC_WATCHTOWER_LOG_PREFIX="  watchtower: " CCC_VERSION="$ccc_version" \
    bash "$script" || true
}
ensure_watchtower

# ---------------------------------------------------------------------------
# ccc CLI: symlink the repo-root client onto PATH. Shared with
# scripts/install.sh (see scripts/link-ccc-cli.sh) so a plain `git clone` +
# `./run.sh` — which never touches install.sh — still ends up with `ccc` on
# PATH, not just the curl-installer path.
# ---------------------------------------------------------------------------
link_ccc_cli_script="$HERE/scripts/link-ccc-cli.sh"
if [ -f "$link_ccc_cli_script" ]; then
  CCC_CLI_SOURCE_DIR="$HERE" CCC_CLI_LOG_PREFIX="  ccc-cli: " \
    bash "$link_ccc_cli_script" || true
fi
unset link_ccc_cli_script

# Foreground installs do not have launchd/systemd to start the independent
# execution worker. Ensure one is healthy before replacing this shell with the
# restartable dashboard. `nohup` + a separate session keeps it alive across the
# dashboard's in-place exec restart (and across a closed terminal).
case "${CCC_CONTROL_PLANE_ENGINES:-1}" in
  0|false|False|no|No) ;;
  *)
    worker_health="$("$PYTHON" "$HERE/ccc_worker.py" --health 2>/dev/null || true)"
    read -r existing_worker_pid existing_worker_idle worker_compatible <<EOF
$(printf '%s' "$worker_health" | "$PYTHON" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    data = {}
worker = data.get("worker") if isinstance(data.get("worker"), dict) else {}
capabilities = worker.get("capabilities") or []
idle = not any(int(data.get(key) or 0) for key in (
    "active", "queued", "uncertain",
))
print(
    int(worker.get("pid") or 0),
    int(bool(data.get("ok")) and idle),
    int("engine-execution-v1" in capabilities),
)
')
EOF
    if [ "${worker_compatible:-0}" != "1" ] \
      && [ "${existing_worker_idle:-0}" = "1" ] \
      && [ "${existing_worker_pid:-0}" -gt 1 ] 2>/dev/null; then
      # A pre-engine-control worker owns no live work. Retire that exact PID;
      # launchd/systemd may replace it, otherwise the detached start below
      # does. Never roll an older worker with unresolved work.
      kill "$existing_worker_pid" >/dev/null 2>&1 || true
      for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        sleep 0.1
        worker_health="$("$PYTHON" "$HERE/ccc_worker.py" --health 2>/dev/null || true)"
        if printf '%s' "$worker_health" | "$PYTHON" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
caps = (data.get("worker") or {}).get("capabilities") or []
raise SystemExit(0 if "engine-execution-v1" in caps else 1)
'; then
          worker_compatible=1
          break
        fi
      done
    fi
    if [ "${worker_compatible:-0}" != "1" ] \
      && "$PYTHON" "$HERE/ccc_worker.py" --health >/dev/null 2>&1; then
      echo "⚠ Older persistent worker still has unresolved work; using compatibility execution." >&2
    elif [ "${worker_compatible:-0}" != "1" ]; then
      nohup "$PYTHON" "$HERE/ccc_worker.py" \
        >>"$SERVICE_LOG_DIR/worker.out.log" \
        2>>"$SERVICE_LOG_DIR/worker.err.log" </dev/null &
      worker_pid=$!
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        if "$PYTHON" "$HERE/ccc_worker.py" --health >/dev/null 2>&1; then
          echo "  worker   : persistent (pid $worker_pid)"
          break
        fi
        sleep 0.1
      done
      if ! "$PYTHON" "$HERE/ccc_worker.py" --health >/dev/null 2>&1; then
        echo "⚠ Persistent worker did not become ready; dashboard uses legacy execution." >&2
      fi
    fi
    # A long-running worker keeps executing the server.py it imported at first
    # engine RPC -- upgrades change the code on disk UNDER it, and the policy
    # above (never restart a healthy worker) means the new code would never
    # take effect. Its health reports the loaded module's version; if that
    # lags the repo, kickstart exactly once per upgrade. Queued work becomes
    # 'uncertain' and is reclaimed via Settings -> Maintenance -> Reconcile.
    # A worker that never imported server (server_version null) needs nothing:
    # its first RPC loads the new code from disk.
    if [ "${worker_compatible:-0}" = "1" ]; then
      read -r worker_server_version worker_content_hash repo_version repo_content_hash <<EOF
$(printf '%s' "$worker_health" | "$PYTHON" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    data = {}
worker = data.get("worker") if isinstance(data.get("worker"), dict) else {}
# Key ABSENT means a pre-version-reporting worker -- definitionally stale.
# Key present but null means a current worker that never imported server.
sv = "absent" if "server_version" not in worker else (worker.get("server_version") or "")
sh = worker.get("server_content_hash") or ""
print(sv, sh, end=" ")
' 2>/dev/null; "$PYTHON" "$HERE/ccc_server/content_hash.py" "$HERE")
EOF
      worker_stale_version=0
      worker_stale_hash=0
      if [ -n "$repo_version" ] && { [ "$worker_server_version" = "absent" ] \
        || { [ -n "$worker_server_version" ] && [ "$worker_server_version" != "$repo_version" ]; }; }; then
        worker_stale_version=1
      fi
      if [ -n "$repo_content_hash" ] && [ -n "$worker_content_hash" ] \
        && [ "$worker_content_hash" != "$repo_content_hash" ]; then
        worker_stale_hash=1
      fi
      if { [ "$worker_stale_version" = "1" ] || [ "$worker_stale_hash" = "1" ]; } \
        && [ "${existing_worker_idle:-0}" != "1" ]; then
        # Never roll a worker that owns active/queued/uncertain work: source
        # files change far more often than worker behaviour, and a restart
        # cuts off live turns. The hourly maintenance tick retries once idle.
        echo "→ Worker code is stale but the worker is busy — restart deferred until it is idle"
      elif [ "$worker_stale_version" = "1" ] || [ "$worker_stale_hash" = "1" ]; then
        if [ "$worker_stale_version" = "1" ]; then
          echo "→ Worker runs server.py ${worker_server_version:-never-imported} but the repo is v$repo_version — restarting worker"
        else
          echo "→ Worker runs an older copy of server.py — restarting worker"
        fi
        echo "  Queued work will show as 'needs reconciliation' in Settings → Maintenance."
        if ! launchctl kickstart -k "$(worker_service_target)" >/dev/null 2>&1; then
          # No launchd worker service on this install path (brew service or
          # DMG app spawn): kill the stale worker AND immediately replace it,
          # or the dashboard runs workerless (legacy execution) until the
          # next launch.
          kill "$existing_worker_pid" >/dev/null 2>&1 || true
          sleep 0.5
          if ! "$PYTHON" "$HERE/ccc_worker.py" --health >/dev/null 2>&1; then
            nohup "$PYTHON" "$HERE/ccc_worker.py" \
              >>"$SERVICE_LOG_DIR/worker.out.log" \
              2>>"$SERVICE_LOG_DIR/worker.err.log" </dev/null &
            for _ in 1 2 3 4 5 6 7 8 9 10; do
              if "$PYTHON" "$HERE/ccc_worker.py" --health >/dev/null 2>&1; then
                echo "  worker   : restarted on v$repo_version"
                break
              fi
              sleep 0.2
            done
          fi
        else
          # kickstart -k returns before the old worker is gone. Do not hand
          # control to the dashboard until a NEW worker pid answers health, so
          # restarting only the dashboard never boots it against a dying
          # worker (or into the workerless legacy path). This is what makes
          # "restart the worker first, then the dashboard" unnecessary.
          for _ in $(seq 1 75); do
            new_worker_pid="$("$PYTHON" "$HERE/ccc_worker.py" --health 2>/dev/null | "$PYTHON" -c '
import json, sys
try:
    print(int((json.load(sys.stdin).get("worker") or {}).get("pid") or 0))
except Exception:
    print(0)
' 2>/dev/null || true)"
            if [ "${new_worker_pid:-0}" -gt 1 ] 2>/dev/null \
              && [ "$new_worker_pid" != "${existing_worker_pid:-0}" ]; then
              echo "  worker   : restarted on v$repo_version (pid $new_worker_pid)"
              break
            fi
            sleep 0.2
          done
        fi
      fi
    fi
    ;;
esac

exec "$PYTHON" "$HERE/server.py"
