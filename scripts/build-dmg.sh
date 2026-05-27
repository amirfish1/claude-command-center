#!/usr/bin/env bash
# Build a macOS .dmg installer for CCC.
#
# Output: ccc-v<version>.dmg in the repo root.
#
# The DMG contains:
#   - CCC.app           — a native Cocoa/WKWebView shell (Swift, ~150KB
#                         universal binary) that hosts the localhost
#                         dashboard inside a real Mac window. Compiled
#                         from scripts/macapp/main.swift.
#   - Applications      — symlink target so the user can drag CCC.app onto it.
#
# CCC.app does NOT bundle Python or the Claude CLI. It expects them on PATH
# (same prereqs as curl install). On first launch (no ~/.ccc/claude-command-center
# on disk) it spawns a Terminal window with the bundled install.sh — same
# UX as the curl install, since we need user consent to clone into $HOME.
#
# This is the "click-to-install" path, alongside curl-bash and brew tap.
# All three paths share scripts/install.sh, so behaviour stays consistent.
#
# Usage:
#   ./scripts/build-dmg.sh                # version pulled from pyproject.toml
#   ./scripts/build-dmg.sh 4.3.1          # explicit version
#
# Requirements: macOS Command Line Tools (swiftc, hdiutil, sips, iconutil,
# plutil, lipo). All ship with Xcode CLT — no full Xcode app needed.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "build-dmg: macOS-only (uses hdiutil + iconutil)" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Resolve version
# ---------------------------------------------------------------------------
VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(grep -E '^version *= *"' "$REPO_ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
fi
if [ -z "$VERSION" ]; then
  echo "build-dmg: could not resolve version (pass as arg or set in pyproject.toml)" >&2
  exit 1
fi
echo "build-dmg: version = $VERSION"

DMG_NAME="ccc-v${VERSION}.dmg"
DMG_PATH="$REPO_ROOT/$DMG_NAME"
VOL_NAME="CCC v${VERSION}"

# ---------------------------------------------------------------------------
# Staging dirs
# ---------------------------------------------------------------------------
WORK_DIR="$(mktemp -d -t ccc-dmg-build)"
trap 'rm -rf "$WORK_DIR"' EXIT
APP_DIR="$WORK_DIR/CCC.app"
STAGING_DIR="$WORK_DIR/staging"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources" "$STAGING_DIR"

# ---------------------------------------------------------------------------
# Icon: convert _assets/Claude Command Center.png to .icns
# ---------------------------------------------------------------------------
SRC_ICON="$REPO_ROOT/_assets/Claude Command Center.png"
if [ -f "$SRC_ICON" ]; then
  echo "build-dmg: rendering icon from $SRC_ICON"
  ICONSET="$WORK_DIR/CCC.iconset"
  mkdir -p "$ICONSET"
  sips -z 16   16   "$SRC_ICON" --out "$ICONSET/icon_16x16.png"      >/dev/null
  sips -z 32   32   "$SRC_ICON" --out "$ICONSET/icon_16x16@2x.png"   >/dev/null
  sips -z 32   32   "$SRC_ICON" --out "$ICONSET/icon_32x32.png"      >/dev/null
  sips -z 64   64   "$SRC_ICON" --out "$ICONSET/icon_32x32@2x.png"   >/dev/null
  sips -z 128  128  "$SRC_ICON" --out "$ICONSET/icon_128x128.png"    >/dev/null
  sips -z 256  256  "$SRC_ICON" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
  sips -z 256  256  "$SRC_ICON" --out "$ICONSET/icon_256x256.png"    >/dev/null
  sips -z 512  512  "$SRC_ICON" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
  sips -z 512  512  "$SRC_ICON" --out "$ICONSET/icon_512x512.png"    >/dev/null
  sips -z 1024 1024 "$SRC_ICON" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
  iconutil -c icns "$ICONSET" -o "$APP_DIR/Contents/Resources/CCC.icns"
else
  echo "build-dmg: no icon at $SRC_ICON — DMG will use default Finder icon"
fi

# ---------------------------------------------------------------------------
# Bundle the installer script
# ---------------------------------------------------------------------------
cp "$REPO_ROOT/scripts/install.sh" "$APP_DIR/Contents/Resources/install.sh"
chmod +x "$APP_DIR/Contents/Resources/install.sh"

# ---------------------------------------------------------------------------
# Info.plist
# ---------------------------------------------------------------------------
cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleDisplayName</key><string>Claude Command Center</string>
  <key>CFBundleExecutable</key><string>CCC</string>
  <key>CFBundleIconFile</key><string>CCC</string>
  <key>CFBundleIdentifier</key><string>com.github.claude-command-center</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>CCC</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><false/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>MIT — github.com/amirfish1/claude-command-center</string>
</dict>
</plist>
EOF
plutil -lint "$APP_DIR/Contents/Info.plist" >/dev/null

# ---------------------------------------------------------------------------
# Executable: compile main.swift to a universal (arm64 + x86_64) binary
# ---------------------------------------------------------------------------
SWIFT_SRC="$REPO_ROOT/scripts/macapp/main.swift"
if [ ! -f "$SWIFT_SRC" ]; then
  echo "build-dmg: $SWIFT_SRC not found" >&2
  exit 1
fi
if ! command -v swiftc >/dev/null 2>&1; then
  echo "build-dmg: swiftc not found. Install Xcode CLT: xcode-select --install" >&2
  exit 1
fi

echo "build-dmg: compiling main.swift (arm64 + x86_64 universal)"
ARM_BIN="$WORK_DIR/CCC-arm64"
X86_BIN="$WORK_DIR/CCC-x86_64"
swiftc -O -target arm64-apple-macos11.0 -o "$ARM_BIN" "$SWIFT_SRC"
swiftc -O -target x86_64-apple-macos11.0 -o "$X86_BIN" "$SWIFT_SRC"
lipo -create "$ARM_BIN" "$X86_BIN" -output "$APP_DIR/Contents/MacOS/CCC"
chmod +x "$APP_DIR/Contents/MacOS/CCC"
BIN_SIZE_KB="$(du -k "$APP_DIR/Contents/MacOS/CCC" | awk '{print $1}')"
echo "build-dmg: binary = ${BIN_SIZE_KB} KB (universal)"

# ---------------------------------------------------------------------------
# Strip extended attributes that Gatekeeper sometimes chokes on
# ---------------------------------------------------------------------------
xattr -cr "$APP_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Ad-hoc codesign — does not satisfy notarization but does prevent the
# "App is damaged" error after quarantine on Apple Silicon. Optional; the
# DMG still works without it, the user just has to right-click → Open.
# ---------------------------------------------------------------------------
if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP_DIR" >/dev/null 2>&1 || \
    echo "build-dmg: ad-hoc codesign failed (non-fatal)"
fi

# ---------------------------------------------------------------------------
# Stage + build DMG
# ---------------------------------------------------------------------------
cp -R "$APP_DIR" "$STAGING_DIR/"
ln -s /Applications "$STAGING_DIR/Applications"

# Drop a small README the user sees if they explore the DMG
cat > "$STAGING_DIR/README.txt" <<EOF
Claude Command Center — v${VERSION}

1. Drag CCC.app onto the Applications folder.
2. Open CCC from Launchpad or /Applications.
3. CCC opens as a native Mac window. The dashboard runs locally on
   your machine; nothing leaves your computer.

First launch: macOS may say "CCC is from an unidentified developer".
Right-click CCC.app in Applications → Open → Open. This only happens
once. CCC is open source and unsigned (no Apple developer account).

First launch only: CCC needs to clone its source into ~/.ccc and
verify the Claude Code CLI is installed. A short Terminal window
appears for this; close it once the CCC window loads.

Curl and Homebrew install paths are also available — see
https://github.com/amirfish1/claude-command-center
EOF

echo "build-dmg: assembling $DMG_NAME"
rm -f "$DMG_PATH"
hdiutil create \
  -volname "$VOL_NAME" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  -fs HFS+ \
  -imagekey zlib-level=9 \
  "$DMG_PATH" >/dev/null

SIZE_KB="$(du -k "$DMG_PATH" | awk '{print $1}')"
echo "build-dmg: wrote $DMG_PATH (${SIZE_KB} KB)"
echo "build-dmg: next steps —"
echo "  open '$DMG_PATH'                       # smoke test locally"
echo "  gh release upload v${VERSION} '$DMG_PATH'   # publish to GitHub release"
