# Dashboard restart audit

The dashboard writes `START`, `RESTART` and `STOP` events to the local
`logs/activity.log`, using the existing activity log format. Events include a
process generation, service invocation, PID, uptime, caller and reason. CLI
restart requests report their PID and parent PID; HTTP caller fields are
diagnostic claims, not authenticated identities. Automatic updates and network
binding changes have distinct reasons. A restart cancelled during draining
does not produce an executed `RESTART` event.

On Linux, memory evidence separates dashboard RSS and peak RSS from the
service cgroup's current/peak charge, anonymous memory, file cache, memory
limits, OOM counters and largest resident processes. A cgroup peak includes
children and cache; it is not the dashboard's peak RSS. The dashboard saves a
small snapshot every 30 seconds and logs memory samples when the cgroup peak
grows by at least 64 MiB. A service invocation can span several in-place exec
restarts; process generations distinguish them.

Systemd service templates include an independent `ExecStopPost` hook. It writes
`STOP_POST` even when the dashboard is killed by OOM or SIGKILL, with systemd's
`SERVICE_RESULT`, `EXIT_CODE` and `EXIT_STATUS`. Reinstall existing service
definitions to adopt the hook; Python changes also need a dashboard restart.
An existing installation can add the equivalent `ExecStopPost` in a service
drop-in and reload systemd's unit definitions without restarting the service.

The hook retains the last process sample and its age, separately from cgroup
memory measured after stopping. Exact process RSS at an uncatchable kill is
unavailable. Python signal handlers cannot identify the sender, and systemd
does not expose the client that requested a stop to `ExecStopPost`; these
fields explicitly remain unknown. For requests through CCC, the preceding
`RESTART` event supplies the initiating HTTP/CLI or updater context. Avoid
using direct service-manager restarts for routine changes.
