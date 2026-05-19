#!/usr/bin/env bash
# Claude Command Center one-command installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash
#   curl -fsSL ".../install.sh?from=hn" | bash
#   ./install.sh --from=readme
#
# Behaviour:
#   - macOS only. Linux exits fast with a one-line pointer to the Docker issue.
#   - Clones to ~/.ccc/claude-command-center if absent, git pulls if present.
#   - Verifies python3 and the `claude` CLI are on PATH.
#   - Persists an attribution channel to ~/.claude/command-center/install-source.
#   - Launches ./run.sh in the foreground and opens http://localhost:8090
#     once the port answers.

set -euo pipefail

REPO_URL="https://github.com/amirfish1/claude-command-center"
INSTALL_DIR="$HOME/.ccc/claude-command-center"
PORT="${PORT:-8090}"
DASHBOARD_URL="http://localhost:${PORT}"
SOURCE_FILE="$HOME/.claude/command-center/install-source"

VALID_CHANNELS="readme landing-hero hn ph devto yt gh-trending unknown"

err() {
  printf 'install: %s\n' "$*" >&2
}

# ---------------------------------------------------------------------------
# Attribution channel
# ---------------------------------------------------------------------------
# Parsed from either:
#   - a query string on $0 (when curled like ".../install.sh?from=hn | bash"
#     some shells preserve the URL in $0; we look defensively)
#   - --from=<channel> argv
parse_channel() {
  local raw=""
  case "${0:-}" in
    *\?from=*) raw="${0#*\?from=}"; raw="${raw%%[&# ]*}" ;;
  esac
  for arg in "$@"; do
    case "$arg" in
      --from=*) raw="${arg#--from=}" ;;
    esac
  done
  if [ -z "$raw" ]; then
    printf 'unknown'
    return
  fi
  for valid in $VALID_CHANNELS; do
    if [ "$raw" = "$valid" ]; then
      printf '%s' "$valid"
      return
    fi
  done
  printf 'unknown'
}

persist_channel() {
  local channel="$1"
  local dir
  dir="$(dirname "$SOURCE_FILE")"
  mkdir -p "$dir"
  printf '%s\n' "$channel" > "$SOURCE_FILE"
}

# ---------------------------------------------------------------------------
# Platform gate
# ---------------------------------------------------------------------------
require_macos() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || printf 'unknown')"
  if [ "$uname_s" != "Darwin" ]; then
    err "CCC is macOS-only; see the Docker issue ${REPO_URL}/issues/54 once it ships"
    exit 2
  fi
}

# ---------------------------------------------------------------------------
# Prereq checks
# ---------------------------------------------------------------------------
require_python3() {
  if ! command -v python3 >/dev/null 2>&1; then
    err "python3 not found on PATH. Install Xcode CLT: xcode-select --install"
    exit 1
  fi
}

require_claude_cli() {
  if ! command -v claude >/dev/null 2>&1; then
    err "claude CLI not found on PATH. Install from https://docs.claude.com/en/docs/claude-code"
    exit 1
  fi
}

require_git() {
  if ! command -v git >/dev/null 2>&1; then
    err "git not found on PATH. Install Xcode CLT: xcode-select --install"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Fetch / update repo
# ---------------------------------------------------------------------------
sync_repo() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    printf 'install: updating existing checkout at %s\n' "$INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
  else
    printf 'install: cloning %s to %s\n' "$REPO_URL" "$INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
}

# ---------------------------------------------------------------------------
# Launch + open browser
# ---------------------------------------------------------------------------
open_when_ready() {
  # Background watcher: poll the port, then `open` the URL.
  # Bounded by ~60 seconds so we never wedge if the server fails to start.
  (
    for _ in $(seq 1 60); do
      if (echo > "/dev/tcp/127.0.0.1/${PORT}") >/dev/null 2>&1; then
        open "$DASHBOARD_URL" >/dev/null 2>&1 || true
        exit 0
      fi
      sleep 1
    done
  ) &
}

launch_server() {
  printf 'install: launching ./run.sh on port %s\n' "$PORT"
  open_when_ready
  cd "$INSTALL_DIR"
  exec ./run.sh
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
main() {
  require_macos
  require_git
  require_python3
  require_claude_cli

  local channel
  channel="$(parse_channel "$@")"
  persist_channel "$channel"
  printf 'install: attribution channel = %s\n' "$channel"

  sync_repo
  launch_server
}

main "$@"
