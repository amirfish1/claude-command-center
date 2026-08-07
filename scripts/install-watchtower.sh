#!/usr/bin/env bash
# WatchTower install / update — the single implementation, shared by every
# CCC entry point.
#
# Why one file: CCC reaches users through `curl | bash`, Homebrew, the DMG,
# Docker and a plain `git clone`, and each of those used to have its own idea
# of whether WatchTower gets installed. On macOS the honest answer was "no
# path installs it", while the README claimed it shipped by default. What
# every path DOES have in common is that it ends up executing run.sh, so
# run.sh and scripts/install.sh both call this script and get the same chain.
#
# WatchTower is CCC's queue engine, not an optional extra: without it the
# dashboard silently loses worker dispatch (filed tickets never spawn
# anything), plan-to-fleet import, WT-tracked drain and delivery receipts.
# Nothing here is ever fatal — CCC still boots on its built-in fallback.
#
# Usage:
#   ./scripts/install-watchtower.sh        # run the chain
#   source scripts/install-watchtower.sh   # define the functions only (tests)
#
# Environment:
#   CCC_SKIP_WATCHTOWER=1           opt out entirely
#   CCC_PYTHON                      interpreter to install INTO. It must be the
#                                   one that runs server.py: CCC does
#                                   `import watchtower.queue` in-process, so it
#                                   needs the library and not just the `wt`
#                                   binary — which is what rules out pipx.
#   WATCHTOWER_DIR                  explicit local dev checkout
#   WATCHTOWER_INSTALL_DIR          CCC-managed clone (default ~/.ccc/watchtower)
#   WATCHTOWER_REPO_URL             clone source
#   WATCHTOWER_TARBALL_URL          git-less fallback source
#   CCC_WATCHTOWER_FORCE=1          ignore the once-a-day rate limits
#   CCC_WATCHTOWER_LOG_PREFIX       line prefix ("install: " for install.sh)
#   CCC_WATCHTOWER_STATE_DIR        where the marker files live
#   CCC_SKIP_WATCHTOWER_DAEMON=1    install, but do not run `wt start`

WATCHTOWER_REPO_URL="${WATCHTOWER_REPO_URL:-https://github.com/amirfish1/watchtower}"
WATCHTOWER_INSTALL_DIR="${WATCHTOWER_INSTALL_DIR:-$HOME/.ccc/watchtower}"
WATCHTOWER_TARBALL_URL="${WATCHTOWER_TARBALL_URL:-https://github.com/amirfish1/watchtower/archive/refs/heads/main.tar.gz}"
WATCHTOWER_PYPI_NAME="watchtower-cli"
WT_PYTHON="${CCC_PYTHON:-python3}"
WT_STATE_DIR="${CCC_WATCHTOWER_STATE_DIR:-$HOME/.claude/command-center}"
# Touched after a successful check/refresh; read by run.sh to decide whether
# this script is worth forking at all on a given launch.
WT_CHECK_MARKER="$WT_STATE_DIR/watchtower-last-check"
# Touched after a failed install so a restart storm cannot become a pip storm.
WT_FAIL_MARKER="$WT_STATE_DIR/watchtower-bootstrap-failed"
# Set by wt_clone_managed instead of echoed, so log lines can go to stdout.
WT_CLONE_DIR=""

wt_say() {
  printf '%s%s\n' "${CCC_WATCHTOWER_LOG_PREFIX:-watchtower: }" "$*"
}

# --- probes -----------------------------------------------------------------

wt_importable() {
  "$WT_PYTHON" -c 'import watchtower' >/dev/null 2>&1
}

# WatchTower requires 3.11+; CCC itself only requires 3.9. On an older
# interpreter say so once and stay on the fallback engine, rather than letting
# pip emit a wall of resolution errors on every launch.
wt_python_ok() {
  "$WT_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

# Root directory the importable `watchtower` package lives under — a checkout
# for editable installs, site-packages otherwise. This is how we tell "the user
# is running their own tree" from "we installed this".
wt_install_root() {
  "$WT_PYTHON" -c 'import os, watchtower; print(os.path.dirname(os.path.dirname(os.path.abspath(watchtower.__file__))))' 2>/dev/null
}

# Local dev checkouts, in precedence order. A directory only counts if it looks
# like the WatchTower source tree — an empty ~/dev/watchtower would otherwise
# send pip at nothing.
wt_dev_checkouts() {
  local dir
  for dir in "${WATCHTOWER_DIR:-}" "$HOME/Apps/watchtower" "$HOME/dev/watchtower"; do
    if [ -n "$dir" ] && [ -f "$dir/pyproject.toml" ]; then
      printf '%s\n' "$dir"
    fi
  done
}

wt_first_dev_checkout() {
  wt_dev_checkouts | head -n 1
}

wt_is_dev_checkout() {
  local root="$1" candidate
  if [ -z "$root" ] || [ "$root" = "$WATCHTOWER_INSTALL_DIR" ]; then
    return 1
  fi
  while IFS= read -r candidate; do
    if [ "$candidate" = "$root" ]; then
      return 0
    fi
  done <<EOF
$(wt_dev_checkouts)
EOF
  return 1
}

# --- markers ----------------------------------------------------------------

# True when the marker exists and is less than a day old. `find -mtime` is the
# portable way to ask that in a shell; `stat` flags differ on macOS vs GNU.
wt_marker_fresh() {
  if [ "${CCC_WATCHTOWER_FORCE:-0}" = "1" ]; then
    return 1
  fi
  if [ ! -f "$1" ]; then
    return 1
  fi
  [ -n "$(find "$1" -mtime -1 2>/dev/null)" ]
}

wt_touch_marker() {
  mkdir -p "$(dirname "$1")" 2>/dev/null || true
  : > "$1" 2>/dev/null || true
}

# --- install / update primitives --------------------------------------------

# pip into the SAME interpreter that runs server.py. Three attempts, because
# one flag does not fit every interpreter:
#   - inside a venv, --user is rejected outright
#   - Homebrew/Debian pythons are PEP 668 "externally managed" and refuse a
#     plain --user install without --break-system-packages
wt_pip_install() {
  local in_venv
  in_venv="$("$WT_PYTHON" -c 'import sys; print(1 if sys.prefix != sys.base_prefix else 0)' 2>/dev/null || echo 0)"

  if [ "$in_venv" = "1" ]; then
    "$WT_PYTHON" -m pip install --quiet "$@" >/dev/null 2>&1
    return $?
  fi

  if "$WT_PYTHON" -m pip install --user --quiet "$@" >/dev/null 2>&1; then
    return 0
  fi
  # PEP 668. Scoped to --user, so this never writes into the system prefix.
  "$WT_PYTHON" -m pip install --user --break-system-packages --quiet "$@" >/dev/null 2>&1
}

# Clone the public repo into the CCC-managed directory. Sets WT_CLONE_DIR.
wt_clone_managed() {
  WT_CLONE_DIR=""
  if [ -d "$WATCHTOWER_INSTALL_DIR/.git" ]; then
    WT_CLONE_DIR="$WATCHTOWER_INSTALL_DIR"
    return 0
  fi
  if [ -e "$WATCHTOWER_INSTALL_DIR" ]; then
    # Something is already there that we did not create. Never clobber it.
    return 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    return 1
  fi
  wt_say "fetching WatchTower (CCC's queue engine) from $WATCHTOWER_REPO_URL"
  mkdir -p "$(dirname "$WATCHTOWER_INSTALL_DIR")" 2>/dev/null || return 1
  if git clone --depth 1 "$WATCHTOWER_REPO_URL" "$WATCHTOWER_INSTALL_DIR" >/dev/null 2>&1; then
    WT_CLONE_DIR="$WATCHTOWER_INSTALL_DIR"
    return 0
  fi
  rm -rf "$WATCHTOWER_INSTALL_DIR"
  return 1
}

# Fast-forward the CCC-managed clone. It is installed editable, so the pull IS
# the upgrade. The marker is touched BEFORE the pull on purpose: if the network
# is down or the pull hangs, we still back off for a day instead of paying for
# it on every launch.
wt_refresh_managed_clone() {
  if [ ! -d "$WATCHTOWER_INSTALL_DIR/.git" ]; then
    return 0
  fi
  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi
  if git -C "$WATCHTOWER_INSTALL_DIR" pull --ff-only >/dev/null 2>&1; then
    wt_say "updated $WATCHTOWER_INSTALL_DIR"
  fi
  return 0
}

# NEVER pull a dev checkout. ~/Apps/watchtower is somebody's working tree: it
# can hold uncommitted work, a diverged branch or a rebase in progress, and an
# automatic `git pull` there is how you lose an afternoon. Report and stop.
# The comparison is against the already-fetched upstream ref, so it costs no
# network and mutates nothing — not even the remote-tracking branches.
wt_note_dev_checkout_behind() {
  local dir="$1" behind
  if [ ! -d "$dir/.git" ] || ! command -v git >/dev/null 2>&1; then
    return 0
  fi
  behind="$(git -C "$dir" rev-list --count 'HEAD..@{upstream}' 2>/dev/null || true)"
  case "$behind" in
    ''|0) return 0 ;;
  esac
  wt_say "your WatchTower checkout $dir is $behind commit(s) behind its upstream."
  wt_say "not pulling it — it is your working tree. Update it yourself:  git -C $dir pull --ff-only"
  return 0
}

# `wt` lands in the user scripts dir, which is frequently not on PATH.
# `import watchtower` (what CCC actually needs) works regardless, but several
# dashboard surfaces gate on `shutil.which("wt")`, so say how to fix it.
wt_warn_if_wt_not_on_path() {
  if command -v wt >/dev/null 2>&1; then
    return 0
  fi
  local bindir
  bindir="$("$WT_PYTHON" -c 'import sysconfig; print(sysconfig.get_path("scripts", scheme="posix_user"))' 2>/dev/null || true)"
  wt_say "NOTE: the wt command is not on your PATH."
  if [ -n "$bindir" ] && [ -x "$bindir/wt" ]; then
    # SC2016: the literal $PATH belongs in the advice we print, not expanded here.
    # shellcheck disable=SC2016
    wt_say "  add it with:  export PATH=\"$bindir:\$PATH\""
  fi
  wt_say "  CCC works without it, but the queue CLI surfaces stay hidden."
}

# `wt start` writes and loads the LaunchAgent on first run, and is a no-op when
# the daemon is already up, so it is safe on every install/update. Prefer the
# module form when `wt` is not on PATH: the library we just installed always
# is, and it is by construction the same interpreter.
wt_ensure_daemon() {
  case "${CCC_SKIP_WATCHTOWER_DAEMON:-0}" in
    1|true|True|yes|Yes) return 0 ;;
  esac
  if command -v wt >/dev/null 2>&1; then
    wt start >/dev/null 2>&1 || true
  else
    "$WT_PYTHON" -m watchtower.cli start >/dev/null 2>&1 || true
  fi
  return 0
}

# WatchTower's own skill sync (the SKILL.md files that teach an agent the `wt`
# commands) only fires the first time its LaunchAgent plist gets written —
# i.e. once, ever, per machine. Anyone who installed WatchTower before a skill
# existed (or before skills_sync shipped at all) has a plist already on disk
# and never gets a resync: the daemon runs fine, `import watchtower` works,
# but the agent has no idea `wt` exists. Call it unconditionally here instead
# of relying on WatchTower's own first-run gate. `wt skills sync` just
# symlinks bundled skill dirs into each present harness's skills dir
# (~/.claude/skills, ~/.codex/skills, ...) — idempotent and cheap, safe to run
# on every launch.
wt_ensure_skills() {
  if command -v wt >/dev/null 2>&1; then
    wt skills sync >/dev/null 2>&1 || true
  else
    "$WT_PYTHON" -m watchtower.cli skills sync >/dev/null 2>&1 || true
  fi
  return 0
}

# Post-install verification. pip can exit 0 and still leave nothing importable
# (wrong interpreter, --user dir not on sys.path), and that failure mode is the
# one that looks like success in the logs.
wt_finish() {
  if ! wt_importable; then
    return 1
  fi
  wt_say "installed from $1 — watchtower.queue is now available."
  rm -f "$WT_FAIL_MARKER" 2>/dev/null || true
  wt_touch_marker "$WT_CHECK_MARKER"
  wt_warn_if_wt_not_on_path
  wt_ensure_daemon
  wt_ensure_skills
  return 0
}

# --- the chain --------------------------------------------------------------

ccc_install_watchtower() {
  case "${CCC_SKIP_WATCHTOWER:-0}" in
    1|true|True|yes|Yes) return 0 ;;
  esac

  # 1. Already importable — the overwhelmingly common case, and the reason
  #    this is cheap enough to run on every launch. Maintenance (refresh +
  #    daemon) is rate-limited to once a day: CCC restarts often, and a git
  #    pull per restart is a network call per restart.
  if wt_importable; then
    if wt_marker_fresh "$WT_CHECK_MARKER"; then
      return 0
    fi
    wt_touch_marker "$WT_CHECK_MARKER"
    local root
    root="$(wt_install_root || true)"
    if wt_is_dev_checkout "$root"; then
      wt_note_dev_checkout_behind "$root"
    else
      wt_refresh_managed_clone
    fi
    wt_ensure_daemon
    wt_ensure_skills
    return 0
  fi

  # 2. Python floor. Say it once a day, not once a launch: this runs on CCC's
  #    startup path, the answer cannot change between two launches of the same
  #    interpreter, and two lines of unactionable warning on every single start
  #    is how a real notice gets trained into background noise. The same marker
  #    the install failures use, so an interpreter upgrade is picked up on the
  #    same cadence — and CCC_WATCHTOWER_FORCE (an explicit install, or the
  #    in-app updater) still prints it, because there the user just asked.
  if ! wt_python_ok; then
    if ! wt_marker_fresh "$WT_FAIL_MARKER"; then
      local got
      got="$("$WT_PYTHON" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)"
      wt_say "python $got is below WatchTower's 3.11 minimum — skipping WatchTower."
      wt_say "CCC will use its built-in queue engine (no worker dispatch, no plan import)."
      wt_touch_marker "$WT_FAIL_MARKER"
    fi
    return 0
  fi

  # A previous attempt failed within the last day. Retrying every launch would
  # just re-run the same doomed pip resolve, slowly, in front of the user.
  if wt_marker_fresh "$WT_FAIL_MARKER"; then
    return 0
  fi

  wt_say "not installed — bootstrapping CCC's queue engine…"

  # 3. A local dev checkout wins: developers keep working against their own
  #    tree, and an editable install means their edits are what CCC runs.
  local source_dir
  source_dir="$(wt_first_dev_checkout || true)"
  if [ -z "$source_dir" ]; then
    # 4. Otherwise a shallow clone we own, also installed editable so that a
    #    later `git pull --ff-only` is the whole upgrade.
    if wt_clone_managed; then
      source_dir="$WT_CLONE_DIR"
    fi
  fi
  if [ -n "$source_dir" ]; then
    wt_say "installing WatchTower from $source_dir"
    if wt_pip_install -e "$source_dir" && wt_finish "$source_dir"; then
      return 0
    fi
    wt_say "editable install from $source_dir failed; falling back to a source tarball"
  fi

  # 5. Tarball of the same branch — for machines with no usable git.
  wt_say "installing WatchTower from $WATCHTOWER_TARBALL_URL"
  if wt_pip_install "$WATCHTOWER_TARBALL_URL" && wt_finish "$WATCHTOWER_TARBALL_URL"; then
    return 0
  fi

  # 6. PyPI LAST, deliberately. `watchtower-cli` is pinned at the version set
  #    in WatchTower's first commit, hundreds of commits behind main, so it
  #    would look like a successful install and be badly wrong. It is a floor,
  #    not the target.
  wt_say "installing WatchTower from PyPI ($WATCHTOWER_PYPI_NAME) — last resort, this release lags main"
  if wt_pip_install "$WATCHTOWER_PYPI_NAME" && wt_finish "PyPI ($WATCHTOWER_PYPI_NAME)"; then
    return 0
  fi

  # 7. Loud, with the exact command to retry. The tarball is the current
  #    source, so that is what we hand the user — not the stale PyPI release.
  wt_say "WARNING: could not install WatchTower. CCC falls back to its built-in"
  wt_say "  queue engine — tickets can be filed, but no worker is dispatched."
  wt_say "  Retry manually:  $WT_PYTHON -m pip install --user $WATCHTOWER_TARBALL_URL"
  wt_touch_marker "$WT_FAIL_MARKER"
  return 0
}

# Only auto-run when executed, not when sourced (tests source us to call the
# individual steps).
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  ccc_install_watchtower "$@"
fi
