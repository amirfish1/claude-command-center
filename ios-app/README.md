# CCC iOS shell

A Capacitor wrapper that loads your CCC phone URL in a WKWebView. It exists so
the app can hide the iOS prev/next/done bar above the keyboard, which a web
page in Safari cannot do. Everything else is the normal CCC UI.

Prereqs: a Mac with Xcode and CocoaPods, and CCC reachable from the phone (see
[phone access](../docs/phone-access.md)). The generated `ios/` Xcode project is
git-ignored; recreate it with the steps below.

```bash
cd ios-app
npm install
export CCC_IOS_SERVER_URL=https://<your-node>.<tailnet>.ts.net:8443/
npx cap add ios      # first time only
npx cap sync ios
npx cap open ios     # set your signing team, then Run on the device
```

The phone still needs Tailscale signed in to the same account.

How the bar is hidden: `static/app.js` detects the `CCC-iOS` user agent and
calls `Keyboard.setAccessoryBarVisible({ isVisible: false })`, and drops the
56px lift it otherwise adds above the bar.
