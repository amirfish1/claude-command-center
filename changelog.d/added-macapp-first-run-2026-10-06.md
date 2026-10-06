**Mac app first run.** A brand-new CCC.app opens straight into the
guided onboarding flow (`/?onboarding=1`) on first launch, shows a real
splash screen (icon, tagline, live setup status, gentle tips) while the
server starts, and can turn dashboard notifications into native macOS
banners via the new `window.webkit.messageHandlers.cccNotify` bridge.
Returning users get the plain dashboard as before.
