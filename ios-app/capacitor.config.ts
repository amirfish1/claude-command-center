import type { CapacitorConfig } from '@capacitor/cli';

/**
 * The native app asks for the server on first launch and stores it on-device.
 * ServerBridgeViewController configures each bridge with that saved address.
 * Bundle local assets so setup and connection recovery work without a server.
 */
const config: CapacitorConfig = {
  appId: 'ai.amirfish.ccc',
  appName: 'CCC',
  webDir: 'www',
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
