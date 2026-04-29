**macOS native notifications when Claude needs your attention.** The
`Stop` and `Notification` hooks now fire `osascript display notification`
banners alongside their existing sidecar writes, so you see a system-tray
ping even when CCC isn't focused (or is on another desktop space). Two
events:

- **Claude finished a turn** (Stop hook) → "Ready for your input" banner
  with the session-id prefix as subtitle.
- **Claude needs approval** (Notification hook) → "Claude needs your
  approval" banner with the permission-prompt message as the body.

Opt-out via `CCC_NOTIFY=0` in the shell env. Falls through silently on
non-macOS systems (no `osascript` on PATH). Banners are fire-and-forget
via `subprocess.Popen` — hooks never block on notification delivery.
Browser-side `Notification` API can come later as a follow-up; this
covers the "I'm on my Mac and switched away from CCC" case which is the
most common one.
