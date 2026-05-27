#!/usr/bin/env bash
# Cut a Sparkle-ready DMG release.
#
# Wraps build-dmg.sh, signs the resulting DMG with Sparkle's EdDSA key,
# and updates docs/appcast.xml with a new <item>. The maintainer commits +
# pushes the appcast change separately so users can update.
#
# The DMG itself is NOT pushed by this script — that's `gh release upload`.
#
# Usage:
#   ./scripts/release-dmg.sh                  # version from pyproject.toml
#   ./scripts/release-dmg.sh 4.3.3
#   ./scripts/release-dmg.sh --fast 4.3.3     # ad-hoc build, still signs appcast
#   SKIP_BUILD=1 ./scripts/release-dmg.sh 4.3.3   # reuse an existing DMG
#
# Workflow once this finishes:
#   1. gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."
#   2. gh release upload vX.Y.Z ccc-vX.Y.Z.dmg
#   3. git commit --only docs/appcast.xml -m "chore(release): publish vX.Y.Z appcast"
#   4. git push origin main          # GitHub Pages serves docs/appcast.xml

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
SPARKLE_BIN="$REPO_ROOT/scripts/macapp/vendor/bin"
APPCAST="$REPO_ROOT/docs/appcast.xml"

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
FAST_MODE=0
VERSION=""
for arg in "$@"; do
  case "$arg" in
    --fast) FAST_MODE=1 ;;
    -h|--help)
      sed -n '2,22p' "$0"
      exit 0
      ;;
    *)
      if [ -z "$VERSION" ]; then VERSION="$arg"
      else echo "release-dmg: unexpected arg: $arg" >&2; exit 2
      fi
      ;;
  esac
done

if [ -z "$VERSION" ]; then
  VERSION="$(grep -E '^version *= *"' "$REPO_ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
fi
if [ -z "$VERSION" ]; then
  echo "release-dmg: cannot resolve version" >&2
  exit 1
fi

DMG_NAME="ccc-v${VERSION}.dmg"
DMG_PATH="$REPO_ROOT/$DMG_NAME"

# ---------------------------------------------------------------------------
# Build (unless caller already produced the DMG)
# ---------------------------------------------------------------------------
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  BUILD_ARGS=("$VERSION")
  [ $FAST_MODE -eq 1 ] && BUILD_ARGS=(--fast "$VERSION")
  echo "release-dmg: building DMG via build-dmg.sh ${BUILD_ARGS[*]}"
  "$HERE/build-dmg.sh" "${BUILD_ARGS[@]}"
fi

if [ ! -f "$DMG_PATH" ]; then
  echo "release-dmg: expected $DMG_PATH to exist after build" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Sign the DMG with Sparkle's private key (lives in the maintainer's keychain)
# ---------------------------------------------------------------------------
if [ ! -x "$SPARKLE_BIN/sign_update" ]; then
  echo "release-dmg: $SPARKLE_BIN/sign_update missing — vendor Sparkle first" >&2
  exit 1
fi

echo "release-dmg: signing $DMG_NAME with Sparkle EdDSA key"
SIGN_OUTPUT="$("$SPARKLE_BIN/sign_update" "$DMG_PATH")"
# sign_update prints something like:
#   sparkle:edSignature="..." length="12345"
ED_SIG="$(printf '%s\n' "$SIGN_OUTPUT" | sed -E 's/.*sparkle:edSignature="([^"]+)".*/\1/' | head -1)"
LENGTH="$(printf '%s\n' "$SIGN_OUTPUT" | sed -E 's/.*length="([^"]+)".*/\1/' | head -1)"
if [ -z "$ED_SIG" ] || [ -z "$LENGTH" ]; then
  echo "release-dmg: could not parse sign_update output: $SIGN_OUTPUT" >&2
  exit 1
fi
echo "release-dmg: signature   = $ED_SIG"
echo "release-dmg: file length = $LENGTH bytes"

# ---------------------------------------------------------------------------
# Append a new <item> to docs/appcast.xml. Creates the file on first run.
# ---------------------------------------------------------------------------
PUB_DATE="$(LC_ALL=C date -u '+%a, %d %b %Y %H:%M:%S +0000')"
DOWNLOAD_URL="https://github.com/amirfish1/claude-command-center/releases/download/v${VERSION}/${DMG_NAME}"
RELEASE_NOTES_URL="https://github.com/amirfish1/claude-command-center/releases/tag/v${VERSION}"

ITEM_XML=$(cat <<EOF
        <item>
            <title>v${VERSION}</title>
            <pubDate>${PUB_DATE}</pubDate>
            <sparkle:version>${VERSION}</sparkle:version>
            <sparkle:shortVersionString>${VERSION}</sparkle:shortVersionString>
            <sparkle:minimumSystemVersion>11.0</sparkle:minimumSystemVersion>
            <sparkle:releaseNotesLink>${RELEASE_NOTES_URL}</sparkle:releaseNotesLink>
            <enclosure
                url="${DOWNLOAD_URL}"
                length="${LENGTH}"
                type="application/octet-stream"
                sparkle:edSignature="${ED_SIG}" />
        </item>
EOF
)

mkdir -p "$(dirname "$APPCAST")"
if [ ! -f "$APPCAST" ]; then
  echo "release-dmg: creating fresh $APPCAST"
  cat > "$APPCAST" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"
     xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
    <channel>
        <title>Claude Command Center — Updates</title>
        <link>https://amirfish1.github.io/claude-command-center/appcast.xml</link>
        <description>Sparkle update feed for CCC.app.</description>
        <language>en</language>
${ITEM_XML}
    </channel>
</rss>
EOF
else
  # Insert the new item right after <channel>'s introductory metadata
  # (before any existing <item>). Use python for safe XML-ish editing.
  python3 - "$APPCAST" "$ITEM_XML" <<'PYEOF'
import re, sys
path, item = sys.argv[1], sys.argv[2]
src = open(path).read()
# If an item for this version already exists, replace it.
ver_match = re.search(r'<sparkle:version>([^<]+)</sparkle:version>', item)
ver = ver_match.group(1) if ver_match else None
if ver:
    existing = re.search(
        rf'^\s*<item>(?:(?!</item>).)*<sparkle:version>{re.escape(ver)}</sparkle:version>.*?</item>\s*$',
        src,
        flags=re.MULTILINE | re.DOTALL,
    )
    if existing:
        src = src[:existing.start()] + item.strip() + src[existing.end():]
        open(path, 'w').write(src)
        print(f"release-dmg: replaced existing item for v{ver}")
        raise SystemExit(0)
# Otherwise, insert before the first <item> (or before </channel> if none).
insert_at = src.find('<item>')
if insert_at == -1:
    insert_at = src.find('</channel>')
src = src[:insert_at] + item.lstrip() + '\n' + src[insert_at:]
open(path, 'w').write(src)
PYEOF
  echo "release-dmg: updated $APPCAST with v${VERSION}"
fi

# ---------------------------------------------------------------------------
# Tell the maintainer what to do next
# ---------------------------------------------------------------------------
cat <<EOF

release-dmg: done. Next steps:

  # 1. Upload the DMG to the GitHub release (create the release first if needed):
  gh release create v${VERSION} --title "v${VERSION}" --notes-file <(awk '/^## \[${VERSION}\]/,/^## \[/' CHANGELOG.md | sed '\$d')
  gh release upload v${VERSION} '${DMG_PATH}'

  # 2. Commit the appcast update so users start seeing the new version:
  git commit --only docs/appcast.xml -m "chore(release): publish v${VERSION} appcast"
  git push origin main

  # GitHub Pages must be configured to serve from docs/ on main.
  # Once pushed, the feed is live at:
  #   https://amirfish1.github.io/claude-command-center/appcast.xml
EOF
