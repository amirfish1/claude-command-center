#!/usr/bin/env bash
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
# Claude Command Center one-command installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | CCC_FROM=hn bash
#   curl -fsSL .../install.sh | bash               # channel defaults to unknown
#   ./install.sh --from=readme                     # direct invocation after git clone
#
# Behaviour:
#   - Supports macOS and Linux. Windows users can use scripts/install.ps1
#     natively, or run this script inside WSL2 for the Linux service path.
#   - Clones to ~/.ccc/claude-command-center if absent, git pulls if present.
#   - Verifies git and python3 are on PATH.
#   - Persists an attribution channel to ~/.claude/command-center/install-source.
#   - Launches ./run.sh in the foreground and opens http://localhost:8090
#     once the port answers. First-time installs open /?onboarding=1, the
#     guided setup tour; users who already finished it land on the dashboard.

set -euo pipefail

REPO_URL="${CCC_REPO_URL:-https://github.com/amirfish1/claude-command-center}"
INSTALL_DIR="${CCC_INSTALL_DIR:-$HOME/.ccc/claude-command-center}"
PORT_EXPLICIT="${PORT:+1}"
PORT="${PORT:-8090}"
DASHBOARD_URL="http://localhost:${PORT}"
SOURCE_FILE="$HOME/.claude/command-center/install-source"
INSTALL_STAGING=""
PYTHON3="${CCC_PYTHON:-python3}"

VALID_CHANNELS="readme landing-hero hn ph devto yt gh-trending dmg unknown"

err() {
  printf 'install: %s\n' "$*" >&2
}

# ---------------------------------------------------------------------------
# Novice-facing progress UI
# ---------------------------------------------------------------------------
# Numbered steps tell a first-time user where they are in the install; the
# "install:" detail lines stay so logs keep their meaning. Colour only when
# stdout is a terminal and NO_COLOR is not set; under `curl | bash` output is
# still a TTY, so the steps do show in the normal case.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  _C_STEP=$'\033[1;38;5;208m'
  _C_DIM=$'\033[2m'
  _C_OK=$'\033[1;32m'
  _C_RESET=$'\033[0m'
else
  _C_STEP=""
  _C_DIM=""
  _C_OK=""
  _C_RESET=""
fi

step() { printf '%s==>%s %s\n' "$_C_STEP" "$_C_RESET" "$*"; }
note() { printf '%s     %s%s\n' "$_C_DIM" "$*" "$_C_RESET"; }
ok()   { printf '%s     ✓ %s%s\n' "$_C_OK" "$*" "$_C_RESET"; }

cleanup_install_staging() {
  if [ -n "$INSTALL_STAGING" ] && [ -e "$INSTALL_STAGING" ]; then
    rm -rf "$INSTALL_STAGING"
  fi
}

trap cleanup_install_staging EXIT HUP INT TERM

is_app_install() {
  [ "${CCC_INSTALL_MODE:-}" = "app" ]
}

# ---------------------------------------------------------------------------
# Attribution channel
# ---------------------------------------------------------------------------
# Resolution order (highest precedence first):
#   1. --from=<channel> CLI flag (for direct ./install.sh invocation)
#   2. CCC_FROM env var (for `curl ... | CCC_FROM=hn bash` pipe invocation)
#   3. default 'unknown'
#
# We can't recover the URL from $0 under `curl ... | bash` because bash sets
# $0 to "bash" or "-", not the source URL. Hence the env-var hand-off.
parse_channel() {
  local raw=""
  if [ -n "${CCC_FROM:-}" ]; then
    raw="$CCC_FROM"
  fi
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
require_supported_platform() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || printf 'unknown')"
  case "$uname_s" in
    Darwin|Linux) return 0 ;;
    *)
      err "CCC install supports macOS or Linux. On Windows, use scripts/install.ps1 in PowerShell, or run this script inside WSL2 for the Linux service path; unsupported OS: ${uname_s}"
      exit 2
      ;;
  esac
}

# ---------------------------------------------------------------------------
# Prereq checks
# ---------------------------------------------------------------------------
require_python3() {
  if ! command -v "$PYTHON3" >/dev/null 2>&1; then
    err "python3 not found on PATH. Install Python 3, then re-run this installer."
    exit 1
  fi
  if ! "$PYTHON3" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    got="$("$PYTHON3" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)"
    err "python3 ${got} found, but CCC requires Python 3.9+. Install a newer python3, then re-run this installer."
    exit 1
  fi
}

warn_if_no_claude_cli() {
  # Don't hard-exit if `claude` isn't installed: CCC also drives Codex,
  # Gemini, and Antigravity sessions, and the dashboard itself is useful
  # without any engine on PATH (the user gets a clear in-UI hint to
  # install). Hard-exiting here used to silently drop DMG users who
  # downloaded out of curiosity without a Claude Code install — install.sh
  # would print to a Terminal they already closed and the .app's only
  # signal was a "didn't start in 60s" fatal.
  if ! command -v claude >/dev/null 2>&1; then
    note "claude CLI not found yet. The setup tour can install it for you."
  fi
}

# Until Apple's Command Line Tools are installed, macOS ships /usr/bin/git and
# /usr/bin/python3 as stubs: `command -v` finds them, but running one only pops
# an "install developer tools" dialog and fails, which surfaced as a confusing
# "python3 unknown found" or a failed clone on a blank Mac.
require_macos_clt() {
  [ "$(uname -s)" = "Darwin" ] || return 0
  # Every real macOS has xcode-select; without it this isn't Apple's stub setup.
  command -v xcode-select >/dev/null 2>&1 || return 0
  xcode-select -p >/dev/null 2>&1 && return 0
  local c stubs=""
  for c in git "$PYTHON3"; do
    case "$(command -v "$c" 2>/dev/null)" in
      /usr/bin/*) stubs="${stubs} ${c}" ;;
    esac
  done
  [ -n "$stubs" ] || return 0
  err "Apple's Command Line Tools aren't installed, so${stubs} can't run yet. Run: xcode-select --install (or accept the dialog macOS shows), wait for it to finish, then re-run this installer."
  exit 1
}

require_git() {
  if ! command -v git >/dev/null 2>&1; then
    err "git not found on PATH. Install git, then re-run this installer."
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Fetch / update repo
# ---------------------------------------------------------------------------
sync_repo() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    note "updating the copy at $INSTALL_DIR"
    if git -C "$INSTALL_DIR" pull --ff-only; then
      return
    fi
    # History no longer fast-forwards (e.g. an upstream rewrite) or the
    # checkout is otherwise broken. Don't leave the user stuck on a crashed
    # installer — reclone fresh and replace it.
    err "existing checkout at ${INSTALL_DIR} could not fast-forward; recloning fresh"
    clone_into_install_dir replace
    return
  fi

  if [ -e "$INSTALL_DIR" ]; then
    err "install destination exists but is not a Git checkout: ${INSTALL_DIR}. Move it aside or choose CCC_INSTALL_DIR, then retry. No files were changed."
    return 1
  fi

  clone_into_install_dir fresh
}

# Clone into a staging dir next to INSTALL_DIR, then atomically publish it.
#   mode=fresh:   INSTALL_DIR must not exist yet. If a concurrent installer
#                 published it while we were cloning, leave that untouched
#                 rather than overwrite it.
#   mode=replace: INSTALL_DIR is expected to already exist (a broken or
#                 diverged checkout) and gets replaced.
clone_into_install_dir() {
  local mode="$1" parent staging
  parent="$(dirname "$INSTALL_DIR")"
  staging="${INSTALL_DIR}.installing.$$"
  mkdir -p "$parent"
  INSTALL_STAGING="$staging"

  note "cloning $REPO_URL"
  if ! git clone "$REPO_URL" "$staging"; then
    cleanup_install_staging
    INSTALL_STAGING=""
    err "clone failed; no partial installation was published"
    return 1
  fi

  if [ "$mode" = "fresh" ] && [ -e "$INSTALL_DIR" ]; then
    cleanup_install_staging
    INSTALL_STAGING=""
    err "another installer published ${INSTALL_DIR}; leaving it untouched"
    return 1
  fi

  if [ "$mode" = "replace" ]; then
    rm -rf "$INSTALL_DIR"
  fi

  if ! mv "$staging" "$INSTALL_DIR"; then
    cleanup_install_staging
    INSTALL_STAGING=""
    err "could not publish completed checkout at ${INSTALL_DIR}"
    return 1
  fi
  INSTALL_STAGING=""
}

# ---------------------------------------------------------------------------
# Launch + open browser
# ---------------------------------------------------------------------------

# The URL the browser opens once the port answers. First-time installs land
# on the guided setup tour (/?onboarding=1); anyone who already finished
# onboarding gets the dashboard itself. The check reads the same
# onboarding.json the dashboard writes, so re-running this installer after a
# finished setup does not replay the tour, while an interrupted setup picks
# up where it left off.
dashboard_open_url() {
  local state="$HOME/.claude/command-center/onboarding.json"
  if [ -f "$state" ] && grep -Eq '"completed"[[:space:]]*:[[:space:]]*true' "$state" 2>/dev/null; then
    printf '%s' "$DASHBOARD_URL"
  else
    printf '%s/?onboarding=1' "$DASHBOARD_URL"
  fi
}

open_when_ready() {
  if is_app_install; then
    return 0
  fi

  local url="$1"

  # Background watcher: poll the port, then `open` the URL.
  # Bounded by ~60 seconds so we never wedge if the server fails to start.
  (
    for _ in $(seq 1 60); do
      if (echo > "/dev/tcp/127.0.0.1/${PORT}") >/dev/null 2>&1; then
        if command -v open >/dev/null 2>&1; then
          open "$url" >/dev/null 2>&1 || true
        elif command -v xdg-open >/dev/null 2>&1; then
          xdg-open "$url" >/dev/null 2>&1 || true
        fi
        exit 0
      fi
      sleep 1
    done
  ) &
}

ask_install_service() {
  # Default to YES on interactive terminals: most users want CCC to keep
  # running after they close this Terminal window, and the alternative
  # (foreground server tied to Terminal) is a frequent "where did CCC go"
  # source for DMG users. Non-interactive runs (CI, headless curl|bash
  # without a TTY) stay in foreground — auto-installing services without
  # the user watching would be surprising.
  if [ ! -t 1 ] || [ ! -c /dev/tty ]; then
    return 1
  fi

  local choice
  printf '%s     ?%s Keep CCC running in the background after you close this window? [Y/n] ' "$_C_STEP" "$_C_RESET"
  if read -r choice < /dev/tty; then
    case "$choice" in
      [nN][oO]|[nN])
        return 1
        ;;
    esac
  fi
  return 0
}

# A clean machine can already have something else on 8090. Without a
# PORT from the user, move to the next free port instead of crashing at
# bind time; with one, run.sh stops with a clear message.
pick_port() {
  local picker="$INSTALL_DIR/scripts/port_preflight.py" chosen
  [ -n "$PORT_EXPLICIT" ] && return 0
  [ -f "$picker" ] || return 0
  chosen="$("$PYTHON3" "$picker" "$PORT" --pick 2>/dev/null || true)"
  if [ -n "$chosen" ] && [ "$chosen" != "$PORT" ]; then
    note "port $PORT is in use by another program; using port $chosen"
    PORT="$chosen"
    DASHBOARD_URL="http://localhost:${PORT}"
  fi
  export PORT
}

launch_server() {
  if is_app_install; then
    note "launching CCC for the native app on port $PORT"
    cd "$INSTALL_DIR"
    exec ./run.sh
  fi

  pick_port

  local url open_hint
  url="$(dashboard_open_url)"
  case "$url" in
    *\?onboarding=1) open_hint="your browser will open a short setup tour in a moment" ;;
    *)             open_hint="your browser will open the dashboard in a moment" ;;
  esac

  if ask_install_service; then
    note "setting CCC up as a background service"
    open_when_ready "$url"
    cd "$INSTALL_DIR"
    ./run.sh --install-service
    printf '\n'
    ok "CCC is installed and running in the background"
    note "$open_hint"
    note "dashboard: $DASHBOARD_URL"
    exit 0
  else
    note "starting CCC in this window on port $PORT"
    note "keep this window open; stop CCC with Ctrl+C"
    note "tip: ./run.sh --install-service keeps CCC running in the background"
    note "$open_hint"
    open_when_ready "$url"
    cd "$INSTALL_DIR"
    exec ./run.sh
  fi
}

# ---------------------------------------------------------------------------
# WT-26: install WatchTower alongside CCC so watchtower.queue is importable
# ---------------------------------------------------------------------------
# The chain (dev checkout -> managed clone -> tarball -> PyPI last) lives in
# scripts/install-watchtower.sh, which run.sh calls too — one definition, so a
# curl install and a Homebrew install cannot drift apart.
#
# Resolved at call time, not at load time: this script is routinely run as
# `curl ... | bash` with no checkout on disk at all, and only after sync_repo
# has run does $INSTALL_DIR/scripts/ exist.
install_watchtower() {
  local here script=""
  here="$(if cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null; then pwd; fi)"
  local candidate
  for candidate in "$here/install-watchtower.sh" \
                   "$INSTALL_DIR/scripts/install-watchtower.sh"; do
    if [ -f "$candidate" ]; then
      script="$candidate"
      break
    fi
  done
  if [ -z "$script" ]; then
    printf 'install: WARNING: scripts/install-watchtower.sh not found — skipping WatchTower.\n'
    printf 'install:   CCC will use its built-in queue engine (no worker dispatch).\n'
    return 0
  fi
  # CCC_WATCHTOWER_FORCE: an explicit install is a user asking for this now,
  # so it must not be silently skipped by the once-a-day rate limits.
  # CCC_VERSION: records what we just installed, so the very next routine
  # `run.sh` launch (no force) does not redundantly re-force on the same
  # version — see wt_ccc_version_changed in install-watchtower.sh.
  CCC_PYTHON="$PYTHON3" \
  CCC_WATCHTOWER_LOG_PREFIX="install: " \
  CCC_WATCHTOWER_FORCE=1 \
  CCC_VERSION="$(grep -m1 '^__version__ = ' "$INSTALL_DIR/server.py" 2>/dev/null | sed -E 's/^__version__ = "(.*)"$/\1/')" \
    bash "$script" || true
}

# ---------------------------------------------------------------------------
# ccc CLI: symlink the repo-root client onto PATH so `ccc sessions` works
# from anywhere. The chain lives in scripts/link-ccc-cli.sh, which run.sh
# calls too — one definition, so a curl install and a plain `git clone` +
# `./run.sh` cannot drift apart. Resolved at call time, not at load time:
# this script is routinely run as `curl ... | bash` with no checkout on disk
# at all, and only after sync_repo has run does $INSTALL_DIR/scripts/ exist.
# ---------------------------------------------------------------------------
link_ccc_cli() {
  local here script=""
  here="$(if cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null; then pwd; fi)"
  local candidate
  for candidate in "$here/link-ccc-cli.sh" \
                   "$INSTALL_DIR/scripts/link-ccc-cli.sh"; do
    if [ -f "$candidate" ]; then
      script="$candidate"
      break
    fi
  done
  if [ -z "$script" ]; then
    return 0
  fi
  CCC_CLI_SOURCE_DIR="$INSTALL_DIR" CCC_CLI_LOG_PREFIX="install: " \
    bash "$script" || true
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
main() {
  printf '\n'
  step "Claude Command Center"
  note "one board for every AI coding agent on this computer"
  printf '\n'

  step "Step 1 of 4: checking this computer"
  require_supported_platform
  require_macos_clt
  require_git
  require_python3
  ok "git and python3 are ready"
  warn_if_no_claude_cli

  local channel
  channel="$(parse_channel "$@")"
  persist_channel "$channel"
  printf 'install: attribution channel = %s\n' "$channel"

  step "Step 2 of 4: downloading CCC"
  sync_repo
  ok "CCC is in place at $INSTALL_DIR"

  step "Step 3 of 4: installing helper tools"
  install_watchtower  # WT-26: bundle WT as CCC's queue engine
  link_ccc_cli        # put `ccc` on PATH

  step "Step 4 of 4: starting CCC"
  launch_server
}

# Only auto-run when executed, not when sourced (tests source us for
# direct `parse_channel` calls).
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  main "$@"
fi
