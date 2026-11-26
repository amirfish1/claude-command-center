#!/usr/bin/env bash
# release-daily.sh — the once-a-day release gate. Run it on the machine that can
# sign and notarize (it calls scripts/cut-release.sh); trigger it from a timer
# or over ssh from another host.
#
# It WAIVES (exit 0, nothing released) unless all of these hold:
#   1. this repo and every repo in $CCC_RELEASE_REPOS pass the BLOCK checks of
#      scripts/repo-clean-check.sh (on main, no uncommitted/untracked files, no
#      unpushed commits on main). Stale worktrees/branches only file a
#      REPO-FIXER ticket. A second blocked day in a row reports FAILED (alert).
#   2. changelog.d/ holds at least $CCC_RELEASE_MIN_ENTRIES entries (default 3),
#      at least one of them "added-*" or "fixed-*"
#   3. the last release tag is at least 24h old
# Otherwise it cuts the next version: minor if any "added-*" entry, else patch.
#
# Usage: ./scripts/release-daily.sh [--dry-run]
# Env:   CCC_RELEASE_REPOS  space-separated sibling repos to require clean+pushed
#                           (default: $HOME/Apps/watchtower)
#        CCC_RELEASE_MIN_ENTRIES  default 3
# Exit:  0 released or waived, 1 release attempt failed.
#        CCC_RELEASE_CUT_FLAGS    extra cut-release.sh flags (default: --skip-dmg off macOS)
# Log:   ~/Library/Logs/ccc-release/ (macOS) or ~/.local/state/ccc-release/
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
if [ "${CCC_RELEASE_DAILY_LOCKED:-}" != 1 ]; then
  exec python3 "$REPO_ROOT/scripts/release_backfill.py" daily "$@"
fi
# Also accepts the flag via SSH_ORIGINAL_COMMAND, for a forced-command ssh key.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
# A mode file lets release-trigger.sh pass --dry-run through launchd (consumed once).
MODE_FILE="${CCC_RELEASE_MODE_FILE:-$HOME/.claude/command-center/release-daily.mode}"
MODE="$(cat "$MODE_FILE" 2>/dev/null || true)"
DRY=0
{ [ "${1:-}" = "--dry-run" ] || [ "${SSH_ORIGINAL_COMMAND:-}" = "--dry-run" ] || [ "$MODE" = "--dry-run" ]; } && DRY=1
MIN="${CCC_RELEASE_MIN_ENTRIES:-3}"
EXTRA_REPOS="${CCC_RELEASE_REPOS:-$HOME/Apps/watchtower}"
if [ "$(uname -s)" = "Darwin" ]; then LOG_DIR="$HOME/Library/Logs/ccc-release"; else LOG_DIR="$HOME/.local/state/ccc-release"; fi
LOG_DIR="${CCC_RELEASE_LOG_DIR:-$LOG_DIR}"
export CCC_RELEASE_LOG_DIR="$LOG_DIR"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"
say() { echo "$(date '+%H:%M:%S') $*" | tee -a "$LOG"; }
waive() { say "WAIVED: $*"; exit 0; }

# Hosts without signing support cut source releases and queue signed Mac assets.
CUT_FLAGS="${CCC_RELEASE_CUT_FLAGS:-}"
if [ -z "${CCC_RELEASE_CUT_FLAGS+x}" ] && [ "$(uname -s)" != "Darwin" ]; then CUT_FLAGS="--skip-dmg"; fi

# A remote trigger requests one existing tag, never an unrelated new release.
if [[ "$MODE" = backfill* ]]; then
  if [[ "$MODE" =~ ^backfill\ (v[0-9]+\.[0-9]+\.[0-9]+)\ ([a-f0-9]{32})$ ]]; then
    backfill_tag="${BASH_REMATCH[1]}"; backfill_token="${BASH_REMATCH[2]}"
    if [ "$DRY" = 1 ]; then
      exec bash "$REPO_ROOT/scripts/cut-release.sh" --dmg-only "$backfill_tag" --dry-run
    fi
    rm -f "$MODE_FILE"
    if bash "$REPO_ROOT/scripts/cut-release.sh" --dmg-only "$backfill_tag"; then
      say "BACKFILL-OK $backfill_tag $backfill_token"
      exit 0
    fi
    say "BACKFILL-FAILED $backfill_tag $backfill_token"
  else
    say "FAILED: invalid backfill mode"
  fi
  exit 1
fi
[ "$DRY" = 1 ] || rm -f "$MODE_FILE"

# Run before every waive gate, including dirty-tree and age waivers. Backfill
# builds/publication use isolated worktrees and do not consume pending changes.
# macOS bash 3.2 treats an empty "${arr[@]}" as unbound under set -u.
backfill_args=()
[ "$DRY" = 0 ] || backfill_args+=(--dry-run)
python3 "$REPO_ROOT/scripts/release_backfill.py" pending ${backfill_args[@]+"${backfill_args[@]}"} || {
  say "FAILED: pending DMG backfill state could not be processed"; exit 1;
}

STREAK_FILE="$LOG_DIR/waive-streak"
WARNINGS=""
# Housekeeping goes to REPO-FIXER without stopping the release. Keep an
# unchanged warning on its existing ticket for as long as that ticket is open.
file_repofixer() {  # $1=kind $2=repo $3=details
  local repo_name key fingerprint
  local ticket_args=()
  repo_name="$(basename "$2")"
  [ "$DRY" = 1 ] && { say "[dry-run] would track REPO-FIXER ticket: $1 in $repo_name"; return 0; }
  command -v wt >/dev/null 2>&1 || return 0
  key="release-gate-$1-$repo_name"
  if [ "$1" = "stale worktrees/branches" ]; then
    fingerprint="$(printf '%s' "$3" | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
    key="$key-$fingerprint"
    # Skip when every warned path/branch is already listed by an open ticket
    # for this repo: a shrinking inventory (cleanup in progress) is not news.
    if wt ls -q REPO-FIXER --status all --limit 0 --json 2>/dev/null |
      RF_TITLE="release gate: $1 in $repo_name" RF_WARNS="$3" python3 -c '
import json, os, sys
def items(text):
    out = set()
    for line in text.splitlines():
        if not line.startswith("WARN ") or ": " not in line[5:]:
            continue
        label, _, rest = line[5:].split(": ", 1)[1].partition(": ")
        out.update((label, item) for item in rest.split())
    return out
want = items(os.environ["RF_WARNS"])
have = set()
for ticket in json.load(sys.stdin):
    if ticket.get("status") != "closed" and ticket.get("title") == os.environ["RF_TITLE"]:
        have |= items(ticket.get("note") or "")
sys.exit(not (want and want <= have))' 2>/dev/null; then
      return 0
    fi
    ticket_args+=(--client-id "$key")
  fi
  wt add -q REPO-FIXER --no-dispatch --title "release gate: $1 in $repo_name" --note "$3" \
    --dedupe-key "$key-$(date +%Y-%m-%d)" ${ticket_args[@]+"${ticket_args[@]}"} >/dev/null 2>&1 || true
}
# Blocks on real risk (uncommitted work, unpushed main). Warnings only file a ticket.
check_repo() {
  local out blocks warns
  out="$("$REPO_ROOT/scripts/repo-clean-check.sh" "$1" 2>&1)"; local rc=$?
  warns="$(printf '%s\n' "$out" | grep '^WARN ' || true)"
  [ -z "$warns" ] || { WARNINGS="$WARNINGS$warns
"; file_repofixer "stale worktrees/branches" "$1" "$warns"; }
  if [ $rc -ne 0 ]; then
    blocks="$(printf '%s\n' "$out" | grep '^BLOCK ' || true)"
    file_repofixer "uncommitted or unpushed work blocking release" "$1" "$blocks
Find the owning sessions with the clean-all-repos skill and get them to commit and push."
    # Escalate instead of waiving forever: a second day running becomes a FAILED
    # verdict, which the trigger turns into a WhatsApp ping.
    local n=$(( $(cat "$STREAK_FILE" 2>/dev/null || echo 0) + 1 )); [ "$DRY" = 1 ] || echo "$n" > "$STREAK_FILE"
    if [ "$n" -ge 2 ]; then say "FAILED: release blocked $n days running; $blocks"; exit 1; fi
    waive "$1 has work that is not landed (REPO-FIXER ticket filed):
$blocks"
  fi
}
check_repo "$REPO_ROOT"
for r in $EXTRA_REPOS; do
  check_repo "$r"
  # The clean check fetches tags but does not advance a stale local main.
  # Sync before computing a sibling version to avoid reusing a published tag.
  git -C "$r" pull --ff-only --quiet origin main 2>/dev/null || waive "cannot fast-forward $r"
done

git pull --ff-only --quiet origin main 2>/dev/null || waive "cannot fast-forward $REPO_ROOT"

source_commit="$(git rev-parse HEAD)" || exit 1
entries="$(git ls-tree --name-only "$source_commit:changelog.d" 2>/dev/null | grep -Ev '^README' | grep -E '\.md$' || true)"
n="$(printf '%s' "$entries" | grep -c . || true)"
[ "$n" -ge "$MIN" ] || waive "only $n changelog entries (need $MIN)"
printf '%s\n' "$entries" | grep -Eq '^(added|fixed)-' || waive "no added-/fixed- entries among $n"

tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*' "$source_commit" 2>/dev/null || true)"
if [ -n "$tag" ]; then
  age=$(( $(date +%s) - $(git log -1 --format=%ct "$tag") ))
  [ "$age" -ge 86400 ] || waive "last release $tag is only $((age/3600))h old"
fi
prev="${tag#v}"; prev="${prev:-0.0.0}"
IFS=. read -r MA MI PA <<< "$prev"
if printf '%s\n' "$entries" | grep -q '^added-'; then NEXT="$MA.$((MI+1)).0"; else NEXT="$MA.$MI.$((PA+1))"; fi

rm -f "$STREAK_FILE"
[ -z "$WARNINGS" ] || say "proceeding despite housekeeping warnings:\n$WARNINGS"
say "ENOUGH: $n entries since $tag; cutting v$NEXT (dry-run=$DRY)"
if [ "$DRY" = 1 ]; then
  ./scripts/cut-release.sh "$NEXT" $CUT_FLAGS --dry-run >>"$LOG" 2>&1; rc=$?
else
  source_args=()
  for flag in $CUT_FLAGS; do
    [ "$flag" != --skip-dmg ] || source_args=(--source-commit "$source_commit")
  done
  ./scripts/cut-release.sh "$NEXT" $CUT_FLAGS ${source_args[@]+"${source_args[@]}"} >>"$LOG" 2>&1; rc=$?
fi
if [ "$rc" = 0 ]; then say "$([ "$DRY" = 1 ] && echo DRY-RUN-OK || echo RELEASED): v$NEXT"; else say "FAILED: cut-release exit $rc (see $LOG)"; fi
[ "$rc" = 0 ] || exit 1

# Also publish each sibling repo that has its own cut-release.sh and commits
# since its last tag (WatchTower: patch bump). A failure here is reported last so
# the verdict line is FAILED even though CCC itself shipped.
for r in $EXTRA_REPOS; do
  [ -x "$r/scripts/cut-release.sh" ] || continue
  # Sessions keep committing in the shared sibling clone during the CCC cut, so
  # pin the sibling release to what origin has now; its cut tests that revision
  # in its own worktree and never publishes the clone's later local state.
  git -C "$r" fetch --quiet --no-tags origin main || { say "FAILED: cannot fetch $(basename "$r") (CCC v$NEXT did ship)"; exit 1; }
  wsrc="$(git -C "$r" rev-parse FETCH_HEAD)"
  wtag="$(git -C "$r" describe --tags --abbrev=0 --match 'v[0-9]*' "$wsrc" 2>/dev/null || true)"
  [ -n "$wtag" ] || continue
  if [ "$(git -C "$r" rev-list --count "$wtag".."$wsrc")" = 0 ]; then say "$(basename "$r"): no commits since $wtag, not releasing"; continue; fi
  say "releasing $(basename "$r") (patch bump from $wtag at ${wsrc:0:12}, dry-run=$DRY)"
  if [ "$DRY" = 1 ]; then (cd "$r" && ./scripts/cut-release.sh patch --dry-run) >>"$LOG" 2>&1; wrc=$?
  else (cd "$r" && ./scripts/cut-release.sh patch --source-commit "$wsrc") >>"$LOG" 2>&1; wrc=$?; fi
  if [ "$wrc" = 0 ]; then say "$([ "$DRY" = 1 ] && echo DRY-RUN-OK || echo RELEASED): $(basename "$r") patch"; else say "FAILED: $(basename "$r") cut-release exit $wrc (CCC v$NEXT $([ "$DRY" = 1 ] && echo dry-run-only || echo 'did ship'))"; exit 1; fi
done
exit 0
