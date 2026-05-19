#!/usr/bin/env bash
# Open the Claude Command Center dashboard in a chromeless app-style browser
# window — no tab strip, no address bar, dock-pinnable, sized for the kanban.
#
# Usage:
#   ./scripts/open-app.sh                # PORT defaults to 8090
#   PORT=9000 ./scripts/open-app.sh
#   ./scripts/open-app.sh --browser brave
#   ./scripts/open-app.sh --size 1600x1000
#   ./scripts/open-app.sh --dry-run      # print what would run, don't launch
#
# Browser priority (macOS): Google Chrome -> Microsoft Edge -> Brave -> Chromium.
# Any Chromium-based browser supports the `--app=URL` flag, which strips
# tabs/URL bar and gives the page its own Dock icon.
#
# On non-macOS we fall back to `chrome --app=URL` / `chromium --app=URL` on
# PATH. Documented as a best-effort path; macOS is the supported target.

set -euo pipefail

PORT="${PORT:-8090}"
HOST="${CCC_APP_HOST:-127.0.0.1}"
URL="${CCC_APP_URL:-http://${HOST}:${PORT}}"
SIZE="1400,900"
BROWSER_PREF=""
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: scripts/open-app.sh [OPTIONS]

Open the CCC dashboard as a chromeless desktop-style window.

Options:
  --browser <name>   Force a specific Chromium browser. One of:
                       chrome | edge | brave | chromium
                     Default: first one found, in that order.
  --size WxH         Window size, e.g. 1600x1000. Default 1400x900.
  --url URL          Override the URL. Default http://$HOST:$PORT.
  --dry-run          Print the command that would run; don't launch.
  -h, --help         Show this help.

Environment:
  PORT               Port the server is bound to. Default 8090.
  CCC_APP_HOST       Host to load. Default 127.0.0.1.
  CCC_APP_URL        Full URL override. Wins over PORT/CCC_APP_HOST.

Linux:
  This script tries `google-chrome`, `chromium`, `microsoft-edge`, and
  `brave-browser` on PATH with `--app=$URL`. macOS is the supported target.

Windows:
  Equivalent one-liner (PowerShell):
    Start-Process chrome.exe "--app=http://127.0.0.1:8090"
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --browser)
      BROWSER_PREF="${2:-}"; shift 2 ;;
    --browser=*)
      BROWSER_PREF="${1#--browser=}"; shift ;;
    --size)
      SIZE="${2:-}"; shift 2 ;;
    --size=*)
      SIZE="${1#--size=}"; shift ;;
    --url)
      URL="${2:-}"; shift 2 ;;
    --url=*)
      URL="${1#--url=}"; shift ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

# Normalize "1400x900" -> "1400,900" (Chrome wants comma).
SIZE="${SIZE//x/,}"
SIZE="${SIZE// /}"

uname_s="$(uname -s 2>/dev/null || echo unknown)"

# A Chromium-based browser is required for --app=. Resolve one based on
# preference and OS. On macOS, prefer `open -na "$AppName" --args` so the
# window gets its own Dock entry. Elsewhere fall back to PATH lookup.
mac_app_for() {
  case "$1" in
    chrome)   echo "Google Chrome" ;;
    edge)     echo "Microsoft Edge" ;;
    brave)    echo "Brave Browser" ;;
    chromium) echo "Chromium" ;;
    *) return 1 ;;
  esac
}

mac_app_installed() {
  local app="$1"
  [ -d "/Applications/${app}.app" ] || [ -d "$HOME/Applications/${app}.app" ]
}

pick_mac_app() {
  local order=("chrome" "edge" "brave" "chromium")
  if [ -n "$BROWSER_PREF" ]; then
    order=("$BROWSER_PREF")
  fi
  for key in "${order[@]}"; do
    local app
    if app="$(mac_app_for "$key")" && mac_app_installed "$app"; then
      printf '%s\n' "$app"
      return 0
    fi
  done
  return 1
}

linux_cmd_for() {
  case "$1" in
    chrome)   echo "google-chrome" ;;
    edge)     echo "microsoft-edge" ;;
    brave)    echo "brave-browser" ;;
    chromium) echo "chromium" ;;
    *) return 1 ;;
  esac
}

pick_linux_cmd() {
  local order=("chrome" "chromium" "edge" "brave")
  if [ -n "$BROWSER_PREF" ]; then
    order=("$BROWSER_PREF")
  fi
  for key in "${order[@]}"; do
    local cmd
    if cmd="$(linux_cmd_for "$key")" && command -v "$cmd" >/dev/null 2>&1; then
      printf '%s\n' "$cmd"
      return 0
    fi
  done
  return 1
}

if [ "$uname_s" = "Darwin" ]; then
  if ! app="$(pick_mac_app)"; then
    echo "Error: no Chromium-based browser found in /Applications." >&2
    echo "Install Google Chrome, Edge, Brave, or Chromium and re-run." >&2
    exit 1
  fi
  set -- open -na "$app" --args "--app=$URL" "--window-size=$SIZE"
else
  if ! cmd="$(pick_linux_cmd)"; then
    echo "Error: no Chromium-based browser on PATH (looked for" \
         "google-chrome, chromium, microsoft-edge, brave-browser)." >&2
    exit 1
  fi
  set -- "$cmd" "--app=$URL" "--window-size=$SIZE"
fi

if [ "$DRY_RUN" = 1 ]; then
  printf '%s' "would launch:"
  for arg in "$@"; do
    printf ' %q' "$arg"
  done
  printf '\n'
  exit 0
fi

exec "$@"
