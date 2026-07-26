#!/usr/bin/env bash
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
#     once the port answers.

set -euo pipefail

REPO_URL="${CCC_REPO_URL:-https://github.com/amirfish1/claude-command-center}"
INSTALL_DIR="${CCC_INSTALL_DIR:-$HOME/.ccc/claude-command-center}"
PORT="${PORT:-8090}"
DASHBOARD_URL="http://localhost:${PORT}"
SOURCE_FILE="$HOME/.claude/command-center/install-source"
INSTALL_STAGING=""
PYTHON3="${CCC_PYTHON:-python3}"

VALID_CHANNELS="readme landing-hero hn ph devto yt gh-trending dmg unknown"

err() {
  printf 'install: %s\n' "$*" >&2
}

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
    err "claude CLI not on PATH — install from https://docs.claude.com/en/docs/claude-code if you want Claude Code sessions. CCC will still start; Codex / Gemini / Antigravity sessions don't need it."
  fi
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
    printf 'install: updating existing checkout at %s\n' "$INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
    return
  fi

  if [ -e "$INSTALL_DIR" ]; then
    err "install destination exists but is not a Git checkout: ${INSTALL_DIR}. Move it aside or choose CCC_INSTALL_DIR, then retry. No files were changed."
    return 1
  fi

  local parent staging
  parent="$(dirname "$INSTALL_DIR")"
  staging="${INSTALL_DIR}.installing.$$"
  mkdir -p "$parent"
  INSTALL_STAGING="$staging"

  printf 'install: cloning %s to %s\n' "$REPO_URL" "$INSTALL_DIR"
  if ! git clone "$REPO_URL" "$staging"; then
    cleanup_install_staging
    INSTALL_STAGING=""
    err "clone failed; no partial installation was published"
    return 1
  fi

  # A concurrent installer may have published while this clone was running.
  # Never turn its checkout into a parent directory or overwrite it.
  if [ -e "$INSTALL_DIR" ]; then
    cleanup_install_staging
    INSTALL_STAGING=""
    err "another installer published ${INSTALL_DIR}; leaving it untouched"
    return 1
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
open_when_ready() {
  if is_app_install; then
    return 0
  fi

  # Background watcher: poll the port, then `open` the URL.
  # Bounded by ~60 seconds so we never wedge if the server fails to start.
  (
    for _ in $(seq 1 60); do
      if (echo > "/dev/tcp/127.0.0.1/${PORT}") >/dev/null 2>&1; then
        if command -v open >/dev/null 2>&1; then
          open "$DASHBOARD_URL" >/dev/null 2>&1 || true
        elif command -v xdg-open >/dev/null 2>&1; then
          xdg-open "$DASHBOARD_URL" >/dev/null 2>&1 || true
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
  printf 'install: Install CCC as a background service so it keeps running after this Terminal closes? [Y/n] '
  if read -r choice < /dev/tty; then
    case "$choice" in
      [nN][oO]|[nN])
        return 1
        ;;
    esac
  fi
  return 0
}

launch_server() {
  if is_app_install; then
    printf 'install: launching CCC for the native app on port %s\n' "$PORT"
    cd "$INSTALL_DIR"
    exec ./run.sh
  fi

  if ask_install_service; then
    printf 'install: installing launchd service...\n'
    open_when_ready
    cd "$INSTALL_DIR"
    ./run.sh --install-service
    printf 'install: CCC successfully installed as a background service!\n'
    exit 0
  else
    printf 'install: launching ./run.sh on port %s\n' "$PORT"
    printf 'install: (Tip: to run CCC in the background and persist after reboot, run: ./run.sh --install-service)\n'
    open_when_ready
    cd "$INSTALL_DIR"
    exec ./run.sh
  fi
}

# ---------------------------------------------------------------------------
# WT-26: install WatchTower alongside CCC so watchtower.queue is importable
# ---------------------------------------------------------------------------
# WatchTower is CCC's queue engine, not an optional extra: without it the
# dashboard silently loses worker dispatch (filed tickets never spawn anything),
# plan-to-fleet import, WT-tracked drain, and delivery receipts. The original
# version of this function only probed for a pre-existing local checkout, so
# every user who was not already a WatchTower developer got the degraded
# fallback while the README advertised the opposite. Install it for real.
#
# Source precedence:
#   1. A local checkout ($WATCHTOWER_DIR, ~/Apps/watchtower, ~/dev/watchtower) —
#      developers keep working against their own tree.
#   2. A git clone of the public repo into ~/.ccc/watchtower, installed
#      editable so a later `git pull` upgrades in place (same model as CCC's
#      own checkout).
#   3. A source tarball of the same repo, for machines with no usable git.
#   4. PyPI (`watchtower-cli`) as the last resort. Deliberately last: the
#      published release lags the repo, so it is a floor, not the target.
WATCHTOWER_REPO_URL="${WATCHTOWER_REPO_URL:-https://github.com/amirfish1/watchtower}"
WATCHTOWER_INSTALL_DIR="${WATCHTOWER_INSTALL_DIR:-$HOME/.ccc/watchtower}"
WATCHTOWER_TARBALL_URL="${WATCHTOWER_TARBALL_URL:-https://github.com/amirfish1/watchtower/archive/refs/heads/main.tar.gz}"
WATCHTOWER_PYPI_NAME="watchtower-cli"

# WatchTower requires Python 3.11+; CCC only requires 3.9. On an older
# interpreter, say so once and leave CCC on its fallback engine rather than
# letting pip emit a wall of resolution errors.
watchtower_python_ok() {
  "$PYTHON3" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

# pip into the SAME interpreter that runs server.py — CCC needs `import
# watchtower` in-process, so pipx/venv isolation would defeat the purpose.
# Three attempts, because one flag does not fit every interpreter:
#   - inside a venv, --user is rejected outright
#   - Homebrew/Debian pythons are PEP 668 "externally managed" and refuse a
#     plain --user install without --break-system-packages
wt_pip_install() {
  local in_venv
  in_venv="$("$PYTHON3" -c 'import sys; print(1 if sys.prefix != sys.base_prefix else 0)' 2>/dev/null || echo 0)"

  if [ "$in_venv" = "1" ]; then
    "$PYTHON3" -m pip install --quiet "$@" && return 0
    return 1
  fi

  "$PYTHON3" -m pip install --user --quiet "$@" && return 0
  # PEP 668. Scoped to --user, so this never writes into the system prefix.
  "$PYTHON3" -m pip install --user --break-system-packages --quiet "$@" && return 0
  return 1
}

# Clone-or-pull the public WatchTower repo. Echoes the directory on success.
watchtower_sync_clone() {
  if [ -d "$WATCHTOWER_INSTALL_DIR/.git" ]; then
    git -C "$WATCHTOWER_INSTALL_DIR" pull --ff-only >/dev/null 2>&1 || true
    printf '%s' "$WATCHTOWER_INSTALL_DIR"
    return 0
  fi
  if [ -e "$WATCHTOWER_INSTALL_DIR" ]; then
    # Something is already there that we did not create. Never clobber it.
    return 1
  fi
  mkdir -p "$(dirname "$WATCHTOWER_INSTALL_DIR")" || return 1
  if git clone --depth 1 "$WATCHTOWER_REPO_URL" "$WATCHTOWER_INSTALL_DIR" >/dev/null 2>&1; then
    printf '%s' "$WATCHTOWER_INSTALL_DIR"
    return 0
  fi
  rm -rf "$WATCHTOWER_INSTALL_DIR"
  return 1
}

# `wt` lands in the user scripts dir, which is frequently not on PATH.
# `import watchtower` (what CCC actually needs) works regardless, but several
# dashboard surfaces gate on `shutil.which("wt")`, so tell the user how to fix it.
watchtower_warn_if_wt_not_on_path() {
  command -v wt >/dev/null 2>&1 && return 0
  local bindir
  bindir="$("$PYTHON3" -c 'import sysconfig; print(sysconfig.get_path("scripts", scheme="posix_user"))' 2>/dev/null || true)"
  printf 'install: NOTE: the wt command is not on your PATH.\n'
  if [ -n "$bindir" ] && [ -x "$bindir/wt" ]; then
    # SC2016: the literal $PATH belongs in the advice we print, not expanded here.
    # shellcheck disable=SC2016
    printf 'install:   add it with:  export PATH="%s:$PATH"\n' "$bindir"
  fi
  printf 'install:   CCC works without it, but the queue CLI surfaces stay hidden.\n'
}

install_watchtower() {
  if "$PYTHON3" -c 'import watchtower' >/dev/null 2>&1 && command -v wt >/dev/null 2>&1; then
    # Already wired up. If we own the checkout it was installed from, still
    # fast-forward it — the install is editable, so a pull IS the upgrade.
    if [ -d "$WATCHTOWER_INSTALL_DIR/.git" ]; then
      git -C "$WATCHTOWER_INSTALL_DIR" pull --ff-only >/dev/null 2>&1 || true
      printf 'install: WatchTower up to date (%s).\n' "$WATCHTOWER_INSTALL_DIR"
    else
      printf 'install: WatchTower already installed — skipping.\n'
    fi
    return 0
  fi

  if ! watchtower_python_ok; then
    local got
    got="$("$PYTHON3" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)"
    printf 'install: python3 %s is below WatchTower'"'"'s 3.11 minimum — skipping WatchTower.\n' "$got"
    printf 'install: CCC will use its built-in queue engine (no worker dispatch, no plan import).\n'
    return 0
  fi

  local wt_dir=""
  if [ -n "${WATCHTOWER_DIR:-}" ] && [ -d "$WATCHTOWER_DIR" ]; then
    wt_dir="$WATCHTOWER_DIR"
  elif [ -d "$HOME/Apps/watchtower" ]; then
    wt_dir="$HOME/Apps/watchtower"
  elif [ -d "$HOME/dev/watchtower" ]; then
    wt_dir="$HOME/dev/watchtower"
  else
    printf 'install: fetching WatchTower (CCC'"'"'s queue engine) from %s\n' "$WATCHTOWER_REPO_URL"
    wt_dir="$(watchtower_sync_clone || true)"
  fi

  if [ -n "$wt_dir" ]; then
    printf 'install: installing WatchTower from %s\n' "$wt_dir"
    if wt_pip_install -e "$wt_dir"; then
      printf 'install: WatchTower installed — watchtower.queue is now available.\n'
      watchtower_warn_if_wt_not_on_path
      return 0
    fi
    printf 'install: editable install failed; falling back to a source tarball\n'
  fi

  printf 'install: installing WatchTower from %s\n' "$WATCHTOWER_TARBALL_URL"
  if wt_pip_install "$WATCHTOWER_TARBALL_URL"; then
    printf 'install: WatchTower installed — watchtower.queue is now available.\n'
    watchtower_warn_if_wt_not_on_path
    return 0
  fi

  printf 'install: installing WatchTower from PyPI (%s)\n' "$WATCHTOWER_PYPI_NAME"
  if wt_pip_install "$WATCHTOWER_PYPI_NAME"; then
    printf 'install: WatchTower installed — watchtower.queue is now available.\n'
    watchtower_warn_if_wt_not_on_path
    return 0
  fi

  printf 'install: WARNING: could not install WatchTower. CCC falls back to its built-in\n'
  printf 'install:   queue engine — tickets can be filed but no worker is dispatched.\n'
  printf 'install:   Retry manually:  %s -m pip install --user %s\n' "$PYTHON3" "$WATCHTOWER_PYPI_NAME"
  return 0
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
main() {
  require_supported_platform
  require_git
  require_python3
  warn_if_no_claude_cli

  local channel
  channel="$(parse_channel "$@")"
  persist_channel "$channel"
  printf 'install: attribution channel = %s\n' "$channel"

  sync_repo
  install_watchtower  # WT-26: bundle WT as CCC's queue engine
  launch_server
}

# Only auto-run when executed, not when sourced (tests source us for
# direct `parse_channel` calls).
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  main "$@"
fi
