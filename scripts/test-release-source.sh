#!/usr/bin/env bash
# Keep source inspection and imports on one revision while other sessions edit.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ccc-release-tests.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

git clone --quiet --shared --no-checkout "$REPO_ROOT" "$TEST_ROOT/repo"
git -C "$TEST_ROOT/repo" checkout --quiet --detach "$SOURCE_SHA"
echo "Testing release source $SOURCE_SHA in $TEST_ROOT/repo"
(cd "$TEST_ROOT/repo" && python3 -m pytest -q --tb=short tests/)

if [ "$(git -C "$REPO_ROOT" rev-parse HEAD)" != "$SOURCE_SHA" ]; then
  echo "Release source changed during tests; retry on the new revision." >&2
  exit 1
fi

DIRTY="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all | grep -vE 'README.md|docs/images' || true)"
if [ -n "$DIRTY" ]; then
  echo "Working tree changed during tests; commit changes and retry." >&2
  exit 1
fi
