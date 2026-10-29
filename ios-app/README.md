# CCC iOS shell

A Capacitor wrapper that loads your CCC phone URL in a WKWebView. It hides the
iOS prev/next/done bar above the keyboard and provides native server settings.

On first launch, **Connect to CCC** asks for the HTTPS address from the desktop
dashboard's **Settings > Phone access**. Tap **Connect** to save the address on
this device and open CCC. Later launches reconnect to the saved server.

**Settings** in the native navigation bar lets you change the address. It stays
available when the server is offline. **Retry** reloads the configured server;
a bundled error page explains how to recover from connection failures.
Addresses must be HTTPS server roots (an optional port is supported), without
embedded credentials, paths, queries, or fragments. Server authentication, if
enabled, happens in the dashboard after connecting. Tailscale addresses require
the phone to have access to the same tailnet.

## Build

Prereqs: a Mac with Xcode and CocoaPods, and CCC reachable from the phone (see
[phone access](../docs/phone-access.md)). The Xcode project is tracked; generated
assets and CocoaPods dependencies are ignored.

```bash
cd ios-app
npm install
npx cap sync ios
npx cap open ios     # set your signing team, then Run on the device
```

No server URL or per-user build configuration is required. Existing installs
using a build-time URL will also see setup once after upgrading; paste the same
phone-access address to reconnect. Native changes require a new iOS build and
installation; updating the CCC dashboard alone does not update the app shell.

The storyboard starts `ServerNavigationController`, which shows setup before
creating any remote bridge. `ServerBridgeViewController` supplies the selected
origin and navigation hostname to Capacitor at runtime. Switching servers
creates a fresh bridge. The `CCC-iOS` user agent still lets `static/app.js` call
`Keyboard.setAccessoryBarVisible({ isVisible: false })` and adjust its keyboard
layout as before.

## Verify

The **iOS server setup** CI workflow builds the app and runs native Simulator UI
tests for first launch, invalid addresses, offline recovery, changing servers,
and persistence after relaunch. Screenshots are attached to the uploaded
`ios-server-setup-results` Xcode result bundle.

On a Mac, run the same UI checks after syncing dependencies:

```bash
xcodebuild test -workspace ios/App/App.xcworkspace -scheme App \
  -destination 'platform=iOS Simulator,name=iPhone 16' CODE_SIGNING_ALLOWED=NO
```

Use an installed iPhone Simulator name if yours differs. The Foundation-only
validation and persistence checks can also run without a Simulator:

```bash
swiftc ios/App/App/ServerAddress.swift tests/main.swift -o /tmp/ccc-server-address-tests
/tmp/ccc-server-address-tests
```
