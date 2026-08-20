**Unattended usage-limit auto-resume no longer sends `continue`.** Hitting a
rate/usage-limit wall still shows the countdown banner, but CCC will not
inject a follow-up or spawn a continuation when the timer elapses. Codex also
stops re-queueing an already-accepted turn just because app-server events
were not observed — that retry loop was burning weekly quota overnight.
