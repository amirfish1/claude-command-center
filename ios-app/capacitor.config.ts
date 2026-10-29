import type { CapacitorConfig } from '@capacitor/cli';

/**
 * Thin native shell around CCC's phone UI (see docs/phone-access.md).
 *
 * No web assets are bundled: the WKWebView loads your own CCC over Tailscale.
 * The point of the shell is native control Safari doesn't give a web page,
 * chiefly hiding the prev/next/done accessory bar above the keyboard.
 *
 * Set your phone URL at sync time (kept out of the repo on purpose):
 *   CCC_IOS_SERVER_URL=https://my-laptop.example-tailnet.ts.net:8443/ npx cap sync ios
 */
const SERVER_URL = process.env.CCC_IOS_SERVER_URL;
if (!SERVER_URL) {
  throw new Error('Set CCC_IOS_SERVER_URL to your CCC phone URL (Settings > Phone access).');
}

const config: CapacitorConfig = {
  appId: 'ai.amirfish.ccc',
  appName: 'CCC',
  webDir: 'www',
  server: {
    url: SERVER_URL,
    allowNavigation: [new URL(SERVER_URL).hostname],
  },
  ios: {
    limitsNavigationsToAppBoundDomains: false,
    // Lets static/app.js recognise the shell (substring "CCC-iOS").
    appendUserAgent: 'CCC-iOS',
    contentInset: 'never',
  },
  plugins: {
    Keyboard: {
      // Don't let Capacitor resize the webview: WKWebView never shrinks
      // visualViewport for the keyboard anyway, so app.js drives --app-vh
      // from the plugin's keyboardWillShow keyboardHeight instead — one
      // discrete jump rather than re-laying out through the animation.
      resize: 'none',
    },
  },
};

export default config;
