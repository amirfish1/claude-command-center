# Mobile access over Tailscale / PWA - plan

## Decision

Productize the existing Tailscale + PWA route. Do not build a native iOS app
for this phase. CCC keeps its simplicity edge by remaining a local web app
served from the user's machine, with an opt-in mobile setup path for trusted
tailnets.

## Goals

1. Keep CCC localhost-only by default.
2. Give first-run users a clear "Mobile access" path from desktop CCC.
3. Detect Tailscale, show the reachable MagicDNS URL, and render a QR code.
4. Persist the opt-in network settings to
   `~/.claude/command-center/network.json`.
5. Guide iPhone users through Safari "Add to Home Screen" so CCC behaves like
   an installed PWA.
6. Optionally support `tailscale serve` for HTTPS inside the user's tailnet.

## Non-goals

- No App Store, TestFlight, or native iOS companion app.
- No automatic LAN, internet, or tailnet exposure on install.
- No public Tailscale Funnel path against unauthenticated CCC.
- No auth system in this phase.
- No change to the documented localhost-first security boundary.

## Current foundation

CCC already has the core pieces:

- `server.py` defaults to `127.0.0.1`.
- `POST /api/network-config` writes only `bind_host`,
  `allowed_origins`, and `trust_tailnet`, then restarts CCC.
- That POST is localhost-only even when a tailnet origin is already trusted.
- `GET /api/network-config` already detects Tailscale with
  `tailscale status --json` and returns MagicDNS/IP origins.
- `static/manifest.webmanifest` and mobile layout support already make CCC
  viable as an Add-to-Home-Screen PWA.

This project should improve the onboarding around those pieces instead of
introducing a parallel access mechanism.

## User flow

1. User opens CCC locally after installing the DMG or cloning the repo.
2. A small first-run affordance offers "Set up mobile access".
3. CCC opens the existing Network access modal in a guided mode.
4. If Tailscale is not installed or not running:
   - show the missing/running state,
   - link to Tailscale install/sign-in,
   - keep the save action disabled for the tailnet shortcut.
5. If Tailscale is running:
   - show the MagicDNS URL, for example
     `http://<device>.<tailnet>.ts.net:8090`,
   - show Tailscale IP fallback URLs as secondary copy,
   - show a QR code for the preferred MagicDNS URL,
   - explain that enabling writes `bind_host: "0.0.0.0"` and
     `trust_tailnet: true` to `network.json`.
6. User clicks "Enable mobile access & restart".
7. CCC saves the config through localhost-only `POST /api/network-config`,
   restarts, and returns to the modal with the QR code still visible.
8. User scans the QR code on iPhone while connected to the same tailnet.
9. The mobile page includes a short Safari-specific Add-to-Home-Screen guide.

## UI shape

Reuse the current Network access modal rather than adding a separate mobile
settings surface. Add a guided "Mobile access" variant with:

- Tailscale status row: missing, stopped, running.
- Preferred URL row with copy button.
- QR code block for the preferred URL.
- Secondary URLs disclosure for Tailscale IPs.
- iPhone setup steps:
  "Open in Safari", "Share", "Add to Home Screen".
- Explicit warning copy:
  "CCC has no auth. Only enable this for a tailnet you trust."

The default controls remain available for advanced users:

- Listen on all network interfaces.
- Trust my Tailscale tailnet.
- Additional allowed origins.

## Backend plan

Keep existing config semantics:

```json
{
  "bind_host": "0.0.0.0",
  "allowed_origins": [],
  "trust_tailnet": true
}
```

Use the existing `GET /api/network-config` payload for most of the guided
flow. Add only small read-only fields if needed:

- `tailnet.preferred_url`: MagicDNS URL if present, otherwise first IP URL.
- `tailnet.https_url`: optional `tailscale serve` URL when configured.
- `tailnet.serve`: status object if CCC can detect `tailscale serve`.

Do not let remote origins call `POST /api/network-config`. The current
localhost-only gate must stay in place.

## Optional HTTPS-in-tailnet

Treat `tailscale serve` as an explicit second step, not part of the default
"Enable mobile access" action.

Possible UX:

1. User enables mobile access over HTTP first.
2. Modal shows "Use HTTPS with Tailscale Serve" when the CLI supports it.
3. User clicks a separate action that runs the minimal `tailscale serve`
   command for the CCC port.
4. CCC shows the HTTPS tailnet URL and a new QR code.

Guardrails:

- Never use Tailscale Funnel in this flow.
- Never expose CCC on the public internet.
- Show the exact command before running it, or provide a copyable command if
  we choose not to execute Tailscale config from CCC.
- Make failure non-fatal; HTTP-over-tailnet remains the baseline.

## First-run behavior

First-run means "the user has not dismissed mobile setup and has no persisted
network config". Store dismissal in CCC state, not in repo files.

Rules:

- Show the mobile setup prompt only on localhost desktop access.
- Do not show it to already-remote clients.
- Do not auto-enable anything.
- Do not write `network.json` until the user clicks the enable action.

## Security requirements

- Default bind remains `127.0.0.1`.
- No install path writes `network.json` automatically.
- No wildcard CORS.
- Same-origin checks remain unchanged.
- `POST /api/network-config` remains localhost-only.
- Every trusted tailnet origin can run commands as the desktop user; the UI
  must say this plainly before saving.
- Public docs must recommend Tailscale Serve, not Funnel, if HTTPS is desired.

## Testing

- Fresh checkout/DMG: CCC starts on localhost with no remote reachability.
- Tailscale missing: modal explains the missing CLI and cannot enable the
  tailnet shortcut.
- Tailscale stopped: modal asks the user to start/sign in.
- Tailscale running: modal shows MagicDNS URL, QR code, and save action.
- Save action writes only the three known keys to `network.json`.
- Remote client can use CCC after restart.
- Remote client cannot call `POST /api/network-config`.
- QR code opens the expected URL on mobile.
- Add-to-Home-Screen launch preserves standalone PWA behavior.

## Files likely touched in the build phase

- `server.py` - optional read-only setup fields, optional serve status/action.
- `static/index.html` - guided mobile setup content in Network access modal.
- `static/app.js` - first-run prompt, QR generation, setup state.
- `static/app.css` - modal layout.
- `README.md` / `SECURITY.md` - short user-facing mobile access docs.
- `changelog.d/added-mobile-access-setup-<date>.md` - implementation snippet.
