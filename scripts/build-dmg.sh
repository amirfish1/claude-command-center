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
#   ./scripts/build-dmg.sh                # full release build (sign + notarize + staple)
#   ./scripts/build-dmg.sh 4.3.1          # explicit version
#   ./scripts/build-dmg.sh --fast         # ad-hoc sign, skip notarization
#   ./scripts/build-dmg.sh --fast 4.3.1
#
# --fast mode produces a usable local DMG (ad-hoc codesign, no Developer ID,
# no notarization) in ~10 seconds. Use for iteration. The DMG will trigger
# the standard "unidentified developer" Gatekeeper warning.
#
# Full mode (default) signs with the bundled Developer ID Application
# identity, embeds Sparkle.framework, signs every nested helper (Autoupdate,
# Updater.app, XPCServices) with hardened runtime + timestamp, then submits
# the DMG to Apple notarytool using the `ccc-notary` keychain profile and
# staples the ticket. Takes ~5 minutes (notarization queue).
#
# Requirements: macOS Command Line Tools (swiftc, hdiutil, sips, iconutil,
# plutil, lipo). All ship with Xcode CLT — no full Xcode app needed.
# Sparkle.framework lives in scripts/macapp/vendor/Sparkle.framework
# (vendored from sparkle-project.org/Sparkle/releases).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "build-dmg: macOS-only (uses hdiutil + iconutil)" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Flags + positional args
# ---------------------------------------------------------------------------
FAST_MODE=0
VERSION=""
for arg in "$@"; do
  case "$arg" in
    --fast) FAST_MODE=1 ;;
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    -*)
      echo "build-dmg: unknown flag: $arg" >&2
      exit 1
      ;;
    *)
      if [ -z "$VERSION" ]; then
        VERSION="$arg"
      else
        echo "build-dmg: unexpected arg: $arg" >&2
        exit 2
      fi
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve version
# ---------------------------------------------------------------------------
if [ -z "$VERSION" ]; then
  VERSION="$(grep -E '^version *= *"' "$REPO_ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
fi
if [ -z "$VERSION" ]; then
  echo "build-dmg: could not resolve version (pass as arg or set in pyproject.toml)" >&2
  exit 1
fi
echo "build-dmg: version = $VERSION  mode = $([ $FAST_MODE -eq 1 ] && echo fast || echo full)"

# ---------------------------------------------------------------------------
# Sparkle vendor — fail fast if the framework isn't on disk
# ---------------------------------------------------------------------------
SPARKLE_VENDOR="$REPO_ROOT/scripts/macapp/vendor/Sparkle.framework"
if [ ! -d "$SPARKLE_VENDOR" ]; then
  echo "build-dmg: Sparkle.framework not vendored at $SPARKLE_VENDOR" >&2
  echo "build-dmg: download Sparkle-2.x.x.tar.xz from" >&2
  echo "  https://github.com/sparkle-project/Sparkle/releases" >&2
  echo "  and extract Sparkle.framework + bin/ into scripts/macapp/vendor/" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Resolve signing identity (full mode only)
# ---------------------------------------------------------------------------
SIGN_IDENTITY=""
if [ $FAST_MODE -eq 0 ]; then
  # Pull the first Developer ID Application identity from the keychain.
  # Override with DEVELOPER_ID env var if multiple are installed.
  SIGN_IDENTITY="${DEVELOPER_ID:-$(security find-identity -v -p codesigning 2>/dev/null \
    | grep -E '"Developer ID Application:' | head -1 \
    | sed -E 's/.*"(Developer ID Application:[^"]+)".*/\1/')}"
  if [ -z "$SIGN_IDENTITY" ]; then
    echo "build-dmg: no 'Developer ID Application' identity in keychain." >&2
    echo "build-dmg: install one or pass --fast for an ad-hoc build." >&2
    exit 1
  fi
  echo "build-dmg: signing identity = $SIGN_IDENTITY"
fi

DMG_NAME="ccc-v${VERSION}.dmg"
DMG_PATH="$REPO_ROOT/$DMG_NAME"
VOL_NAME="CCC v${VERSION}"

# ---------------------------------------------------------------------------
# Staging dirs
# ---------------------------------------------------------------------------
WORK_DIR="$(mktemp -d -t ccc-dmg-build)"
trap 'rm -rf "$WORK_DIR"' EXIT
# The bundle's filesystem name is what Finder displays in the DMG and
# in /Applications (CFBundleDisplayName is a softer hint that Finder
# often ignores for .app bundles). So we name the bundle the long
# descriptive name on disk.
APP_BUNDLE_NAME="Command Center for Claude, Codex, Antigravity.app"
APP_DIR="$WORK_DIR/$APP_BUNDLE_NAME"
STAGING_DIR="$WORK_DIR/staging"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources" "$STAGING_DIR"

# ---------------------------------------------------------------------------
# Icon: convert _assets/Claude Command Center.png to .icns
# ---------------------------------------------------------------------------
SRC_ICON="$REPO_ROOT/_assets/ccc-icon-v2.png"
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
  <key>CFBundleDisplayName</key><string>Command Center for Claude, Codex, Antigravity</string>
  <key>CFBundleExecutable</key><string>CCC</string>
  <key>CFBundleIconFile</key><string>CCC</string>
  <key>CFBundleIdentifier</key><string>com.github.claude-command-center</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Command Center for Claude+</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><false/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>MIT — github.com/amirfish1/claude-command-center</string>
  <!-- Sparkle auto-update. Public EdDSA key verifies update signatures;
       its matching private key lives in the maintainer's macOS keychain
       (label "Private key for signing Sparkle updates"). Losing the
       private key means rotating to a new keypair, which breaks
       auto-update for every user still on the old key. -->
  <key>SUFeedURL</key><string>https://ccc.amirfish.ai/appcast.xml</string>
  <key>SUPublicEDKey</key><string>+oU5VeStRaidpogMHUktYpr/JxKuSn9wY1xEgN106lY=</string>
  <key>SUEnableInstallerLauncherService</key><true/>
  <key>SUEnableAutomaticChecks</key><true/>
  <key>SUScheduledCheckInterval</key><integer>3600</integer>
  <key>NSMicrophoneUsageDescription</key><string>Command Center requires microphone access to dictate speech into conversation and group chat text inputs.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>Command Center requires speech recognition permission to dictate spoken words into text inputs.</string>
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
# -F  adds the vendor dir to the framework search path so `import Sparkle`
#     resolves at compile time.
# -rpath @executable_path/../Frameworks tells dyld where to find
#     Sparkle.framework at runtime (the .app's Frameworks directory).
SPARKLE_VENDOR_DIR="$(dirname "$SPARKLE_VENDOR")"
SWIFTC_FLAGS=(-O -F "$SPARKLE_VENDOR_DIR" -Xlinker -rpath -Xlinker "@executable_path/../Frameworks")
swiftc "${SWIFTC_FLAGS[@]}" -target arm64-apple-macos11.0  -o "$ARM_BIN" "$SWIFT_SRC"
swiftc "${SWIFTC_FLAGS[@]}" -target x86_64-apple-macos11.0 -o "$X86_BIN" "$SWIFT_SRC"
lipo -create "$ARM_BIN" "$X86_BIN" -output "$APP_DIR/Contents/MacOS/CCC"
chmod +x "$APP_DIR/Contents/MacOS/CCC"
BIN_SIZE_KB="$(du -k "$APP_DIR/Contents/MacOS/CCC" | awk '{print $1}')"
echo "build-dmg: binary = ${BIN_SIZE_KB} KB (universal)"

# ---------------------------------------------------------------------------
# Copy Sparkle.framework into the bundle BEFORE codesign.
# Sparkle ships as a versioned framework; preserve symlinks (cp -R follows
# the Versions/Current/Sparkle and Versions/B/Sparkle structure correctly).
# We need to copy without dereferencing the symlinks so codesign sees the
# canonical Versions/B layout it expects.
# ---------------------------------------------------------------------------
mkdir -p "$APP_DIR/Contents/Frameworks"
cp -R "$SPARKLE_VENDOR" "$APP_DIR/Contents/Frameworks/"
echo "build-dmg: bundled Sparkle.framework ($(du -sh "$APP_DIR/Contents/Frameworks/Sparkle.framework" | awk '{print $1}'))"

# ---------------------------------------------------------------------------
# Strip extended attributes that Gatekeeper sometimes chokes on
# ---------------------------------------------------------------------------
xattr -cr "$APP_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Codesign.
#
# Sparkle has several nested signed-helper-blobs (Autoupdate, Updater.app,
# XPCServices). Each one needs its own valid signature with the same
# identity, and the framework Versions/B itself must be signed too.
# Sign deepest-first (helpers, then the framework, then the .app) — codesign
# refuses to overwrite a child signature when the parent is already sealed.
#
# Fast mode uses an ad-hoc identity ("-") and skips hardened runtime,
# matching the previous behaviour. This is the iteration path; the result
# DMG is not notarizable, and Gatekeeper will prompt the user.
#
# Full mode uses the Developer ID Application identity with hardened
# runtime + secure timestamp — required for notarytool to accept it.
# ---------------------------------------------------------------------------
SPARKLE_FW="$APP_DIR/Contents/Frameworks/Sparkle.framework"

if [ $FAST_MODE -eq 1 ]; then
  echo "build-dmg: ad-hoc codesign (fast mode)"
  CODESIGN_FLAGS=(--force --sign -)
else
  echo "build-dmg: codesign with hardened runtime + timestamp (full mode)"
  CODESIGN_FLAGS=(--force --options runtime --timestamp --sign "$SIGN_IDENTITY")
fi

# Sign Sparkle helpers from the inside out. The exact set of nested
# signables in Sparkle 2.x:
#   Versions/B/XPCServices/Downloader.xpc
#   Versions/B/XPCServices/Installer.xpc
#   Versions/B/Updater.app
#   Versions/B/Autoupdate            (executable inside Updater.app or root)
# We just walk the directory and sign anything that looks signable.
sign_target() {
  local target="$1"
  if [ ! -e "$target" ]; then return 0; fi
  codesign "${CODESIGN_FLAGS[@]}" "$target" >/dev/null 2>&1 || {
    echo "build-dmg: codesign failed for $target" >&2
    return 1
  }
}

# 1. XPC services (must be sealed before their parent Updater.app).
for xpc in "$SPARKLE_FW/Versions/B/XPCServices/"*.xpc; do
  [ -e "$xpc" ] || continue
  sign_target "$xpc"
done
# 2. Updater.app (uses XPCs as siblings inside the framework, not nested).
sign_target "$SPARKLE_FW/Versions/B/Updater.app"
# 3. Autoupdate binary lives under Versions/B/Autoupdate in Sparkle 2.x.
sign_target "$SPARKLE_FW/Versions/B/Autoupdate"
# 4. Versions/B (the actual versioned framework directory).
sign_target "$SPARKLE_FW/Versions/B"
# 5. Top-level Sparkle.framework (sealing the Versions symlink).
sign_target "$SPARKLE_FW"

# 6. The .app itself, deep so any other nested helpers we missed get sealed.
codesign "${CODESIGN_FLAGS[@]}" --deep "$APP_DIR" >/dev/null 2>&1 || {
  echo "build-dmg: codesign of $APP_DIR failed" >&2
  exit 1
}

# Validate the chain before we bother building the DMG / submitting to Apple.
if ! codesign --verify --deep --strict --verbose=2 "$APP_DIR" 2>&1 | grep -q "satisfies its Designated Requirement"; then
  echo "build-dmg: codesign --verify --deep --strict failed:" >&2
  codesign --verify --deep --strict --verbose=2 "$APP_DIR" >&2 || true
  exit 1
fi
echo "build-dmg: codesign chain verified clean"

# ---------------------------------------------------------------------------
# Stage + build DMG
# ---------------------------------------------------------------------------
cp -R "$APP_DIR" "$STAGING_DIR/"
ln -s /Applications "$STAGING_DIR/Applications"

# Drop a small README the user sees if they explore the DMG.
# Wording depends on whether we signed with a real Developer ID — once
# we're signed + notarized there's no right-click → Open dance to explain.
if [ $FAST_MODE -eq 0 ]; then
  GATEKEEPER_NOTE='Signed with a Developer ID and notarized by Apple — opens
with a single double-click. No "unidentified developer" prompt.'
else
  GATEKEEPER_NOTE='This local test build is ad-hoc signed and not notarized.
Public release builds are signed with a Developer ID and notarized by Apple.'
fi

cat > "$STAGING_DIR/README.txt" <<EOF
Command Center for Claude, Codex, Antigravity — v${VERSION}
One inbox for all your AI agents.

1. Drag "${APP_BUNDLE_NAME%.app}" onto the Applications folder.
2. Open CCC from Launchpad or /Applications.
3. CCC opens as a native Mac window. The dashboard runs locally on
   your machine; nothing leaves your computer.

${GATEKEEPER_NOTE}

First launch only: CCC installs its local source into ~/.ccc and starts
the loopback dashboard server. Progress and recovery actions appear in
the app itself; Terminal and macOS Automation permission are not needed.

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

# ---------------------------------------------------------------------------
# Notarize + staple (full mode only).
#
# The `ccc-notary` keychain profile holds the Apple ID + app-specific
# password + team ID. Set it up once with:
#   xcrun notarytool store-credentials ccc-notary \
#     --apple-id <apple-id> --team-id N6VV8ZKSJS --password <app-pw>
#
# Notarytool waits synchronously when --wait is passed. After a successful
# response we staple the ticket onto the DMG so the .app survives offline
# Gatekeeper checks too.
# ---------------------------------------------------------------------------
if [ $FAST_MODE -eq 0 ]; then
  echo "build-dmg: codesigning DMG itself"
  codesign --force --sign "$SIGN_IDENTITY" --timestamp "$DMG_PATH"

  echo "build-dmg: submitting $DMG_NAME to notarytool (2-15 min typical)…"
  SUBMIT_LOG="$WORK_DIR/notarize-submit.txt"
  if xcrun notarytool submit "$DMG_PATH" \
       --keychain-profile ccc-notary \
       --wait \
       --timeout 30m 2>&1 | tee "$SUBMIT_LOG"; then
    # notarytool exits 0 even on rejected status; check the log to confirm.
    if grep -q "status: Accepted" "$SUBMIT_LOG"; then
      echo "build-dmg: stapling notarization ticket"
      xcrun stapler staple "$DMG_PATH"
      echo "build-dmg: gatekeeper assessment —"
      /usr/sbin/spctl --assess --type install --verbose=2 "$DMG_PATH" 2>&1 | sed 's/^/  /'
      xcrun stapler validate "$DMG_PATH" 2>&1 | sed 's/^/  /'
    else
      SUBMISSION_ID="$(awk '/id:/ {print $2; exit}' "$SUBMIT_LOG")"
      echo "build-dmg: notarization NOT accepted." >&2
      echo "build-dmg: full log: xcrun notarytool log $SUBMISSION_ID --keychain-profile ccc-notary" >&2
      exit 1
    fi
  else
    echo "build-dmg: notarytool submit failed — see $SUBMIT_LOG" >&2
    exit 1
  fi
fi

echo "build-dmg: next steps —"
echo "  open '$DMG_PATH'                            # smoke test locally"
if [ $FAST_MODE -eq 0 ]; then
  echo "  scripts/release-dmg.sh ${VERSION}           # sign DMG for Sparkle + update appcast"
  echo "  gh release upload v${VERSION} '$DMG_PATH' --clobber   # publish to GitHub release"
fi
