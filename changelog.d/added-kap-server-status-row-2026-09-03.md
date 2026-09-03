**Kimi kap server row in System status.** The panel now shows the `kimi web`
daemon CCC's kap transport routes through: state (online/degraded/offline/idle),
pid, version, port, and heartbeat age. The row is read-only — CCC adopts this
daemon, it never supervises it, so there is no Restart button. It only appears
once kap routing is on or a daemon has ever registered, flags the
"two live daemons, newest heartbeat wins" ambiguity (a 0.40.1 TUI embeds a kap
server too), and only raises the attention banner when kap routing is enabled.
