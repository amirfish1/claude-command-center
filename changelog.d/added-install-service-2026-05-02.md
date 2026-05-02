**`./run.sh --install-service` (macOS).** Installs CCC as a launchd
agent under `~/Library/LaunchAgents/com.github.claude-command-center.plist`
so it starts at login and survives reboots. Bakes in whatever `PORT` and
`CCC_*` env vars were set when you ran it. Re-run to update config;
remove with `./run.sh --uninstall-service`. Logs go to
`~/.claude/command-center/logs/service.{out,err}.log`.

Refuses to install if the target port is already bound by something
other than a previous version of the agent — avoids silent crash loops
where launchd's `KeepAlive=true` would mask a port collision and retry
forever. Post-load, polls the port for up to 2.5s to verify the service
actually came up, instead of trusting `launchctl load`'s return code.

The README's Quickstart now documents both commands as the canonical
flow: `./run.sh` to try it, `./run.sh --install-service` to keep it.
