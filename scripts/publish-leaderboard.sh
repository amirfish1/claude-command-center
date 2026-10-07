#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$REPO_ROOT/site/leaderboard"
DEST_DIR="$REPO_ROOT/docs/leaderboard"
MODE=dry-run

usage() {
  cat <<'EOF'
Usage: scripts/publish-leaderboard.sh [--dry-run | --apply]
                                    [--source-dir DIR] [--dest-dir DIR]

Default: dry-run. Validate the reviewed snapshot and show the two local copies
without writing anything. --apply copies index.html and data.json from
site/leaderboard into docs/leaderboard for a later, human-reviewed Pages commit.

This never exports new results, uploads, commits, pushes, deploys, or schedules
anything. Review the model IDs and scores before choosing --apply.
EOF
}

fail() { printf 'leaderboard: %s\n' "$1" >&2; exit 1; }
while (( $# )); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --dry-run) MODE=dry-run; shift ;;
    --apply) MODE=apply; shift ;;
    --source-dir|--dest-dir)
      (( $# >= 2 )) && [[ -n "$2" ]] || fail "$1 needs a directory."
      if [[ "$1" == --source-dir ]]; then SOURCE_DIR="$2"; else DEST_DIR="$2"; fi
      shift 2 ;;
    *) fail "Unknown option: $1. Use --help." ;;
  esac
done

SOURCE_DIR="${SOURCE_DIR%/}"
DEST_DIR="${DEST_DIR%/}"
[[ -n "$SOURCE_DIR" && -n "$DEST_DIR" ]] || fail "Use explicit non-root directories."
[[ ! -L "$SOURCE_DIR" && ! -L "$DEST_DIR" ]] || fail "Source and destination cannot be symlinks."
[[ -d "$SOURCE_DIR" ]] || fail "The source directory is missing."
[[ ! -e "$DEST_DIR" || -d "$DEST_DIR" ]] || fail "The destination must be a directory."

for filename in index.html data.json; do
  [[ -f "$SOURCE_DIR/$filename" && ! -L "$SOURCE_DIR/$filename" ]] || fail "The source $filename must be a regular file, not a symlink."
  [[ ! -L "$DEST_DIR/$filename" ]] || fail "The destination $filename cannot be a symlink."
  [[ ! -e "$DEST_DIR/$filename" || -f "$DEST_DIR/$filename" ]] || fail "The destination $filename must be a regular file."
done

python3 - "$SOURCE_DIR" "$DEST_DIR" <<'PY'
import sys
from pathlib import Path
source, destination = (Path(value) for value in sys.argv[1:])
if source.resolve() == destination.resolve():
    sys.exit("leaderboard: source and destination must be different directories.")
for directory in (source, destination):
    if any(parent.is_symlink() for parent in directory.absolute().parents):
        sys.exit("leaderboard: directory parents cannot be symlinks.")
PY
python3 "$REPO_ROOT/scripts/export-leaderboard.py" --validate "$SOURCE_DIR/data.json"

if [[ "$MODE" == dry-run ]]; then
  printf 'Dry run. No files changed.\n'
  for filename in index.html data.json; do
    printf 'Would copy %s -> %s\n' "$SOURCE_DIR/$filename" "$DEST_DIR/$filename"
  done
  printf 'Review the snapshot, then use --apply to make these local copies.\n'
  exit 0
fi

mkdir -p "$DEST_DIR"
for filename in index.html data.json; do
  cp "$SOURCE_DIR/$filename" "$DEST_DIR/$filename"
done
printf 'Copied index.html and data.json locally. Nothing was uploaded.\n'
printf 'Review the diff before committing. No publish job was scheduled.\n'
