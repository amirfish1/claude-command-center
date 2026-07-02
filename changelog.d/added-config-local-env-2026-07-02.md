`run.sh` now sources `~/.claude/command-center/config.local.env` if present, so machine-local env vars (e.g. soak flags) survive a reboot instead of relying on `launchctl setenv`.
