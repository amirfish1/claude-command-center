#!/usr/bin/env bash
# Record every film chapter flow against the demo server (Mac).
# Usage: film/record-all.sh [ch-glob]   e.g. film/record-all.sh 'ch3*'
set -u
cd "$(dirname "$0")/.."
OUT=${FILM_OUT:-$HOME/Desktop/ccc-film-rough/chapters}
LS=${FILM_LS:-$HOME/Desktop/ccc-film-rough/ls.json}
BASE=${FILM_BASE:-http://127.0.0.1:8097}
mkdir -p "$OUT"
for f in film/${1:-ch*}.js; do
  n=$(basename "$f" .js)
  echo "== $n"
  timeout 300 node record.js --base "$BASE" --flow "$f" --ls "$LS" \
    --out "$OUT/$n.mp4" --poster "$OUT/$n.png" 2>&1 | grep -E "film\]|wrote|rror" | tail -5
done
