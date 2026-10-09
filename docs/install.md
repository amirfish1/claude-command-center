# Install and run CCC

[Back to the README](../README.md#quickstart) · [First-run guide](onboarding.md)

## Requirements

Git and Python 3.9+ run the dashboard. Install a supported agent CLI to launch
sessions. The dashboard starts without one; the [first-run wizard](onboarding.md)
helps you install the tools for a free first task. Optional tools are
[`gh`](https://cli.github.com/) for GitHub integration and `vercel` for deploy status.

CCC runs on macOS, Linux, and Windows. Native desktop conveniences vary by
platform. Windows can run in PowerShell; WSL2 is the Linux service option.

## Choose an installer

### curl

Clones into `~/.ccc/claude-command-center` and runs in the foreground. Re-run
it to update the checkout.

```bash
curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | CCC_FROM=readme bash
```

From an existing clone, use `./scripts/install.sh --from=readme` instead.

### Python runners (preview)

Build a wheel with `uv build --wheel` from a source checkout, then try it
without a permanent CCC install. This preview is not on PyPI yet.

```bash
uvx --from dist/claude_command_center-5.37.0-py3-none-any.whl claude-command-center
# Or, with pipx:
pipx run --spec dist/claude_command_center-5.37.0-py3-none-any.whl claude-command-center
```

Open `http://localhost:8090` and leave the terminal open. Press Ctrl+C to stop.
WatchTower, the queue engine, is set up on first launch; Bash and a connection
are needed for that step. State stays in `~/.claude/command-center/` and the
usual agent folders, not the runner's cache. Once published, the short commands
will be `uvx claude-command-center` and `pipx run claude-command-center`.

### Windows PowerShell

Clones into `%USERPROFILE%\.ccc\claude-command-center` and runs in the
foreground. Re-running updates the checkout.

```powershell
irm https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.ps1 | iex
```

### Homebrew

Installs into the Cellar, puts `ccc` on `PATH`, and uses a brew-managed Python.

```bash
brew tap amirfish1/ccc
brew install ccc
ccc
```

For a background service, use `brew services start ccc`. Update with
`brew upgrade ccc`.

### macOS app

Download the [latest DMG](https://github.com/amirfish1/claude-command-center/releases/latest),
drag `CCC.app` to Applications, and open it. The signed, notarized app installs
its local source into `~/.ccc/claude-command-center`, shows progress, and opens
the dashboard when its local server is ready. It updates through Sparkle.

If installation fails, the app shows the log and offers Retry, Open Log, and
Quit. It does not automate Terminal or request macOS Automation access.

## From source on macOS

```bash
git clone https://github.com/amirfish1/claude-command-center
cd claude-command-center
./run.sh
```

Open [http://localhost:8090](http://localhost:8090) and pick a repo before
starting repo-scoped actions. Keep the terminal open while using the
foreground server; Ctrl-C stops it.

For a service that starts now and at login:

```bash
./run.sh --install-service
./run.sh --service-status
```

This writes separate dashboard and persistent-worker launch agents under
`~/Library/LaunchAgents/`. The worker has its own lifecycle, so a dashboard
restart does not close worker-owned agent transports.

The installer records the `PORT` and `CCC_*` environment values you set at
installation time. Re-run it to change those values or pick up a change to the
service definition, not for an ordinary update at the same checkout path.
Remove the services with `./run.sh --uninstall-service`.

Logs are under `~/.claude/command-center/logs/`: dashboard logs are
`service.out.log` and `service.err.log`; worker logs are `worker.out.log` and
`worker.err.log`. See [Configuration](configuration.md) for persistent settings.

## Running on Windows

Native Windows uses the same Python server for the dashboard, session
reading, repo picker, and agent spawns.

```powershell
git clone https://github.com/amirfish1/claude-command-center
cd claude-command-center
.\run.ps1
```

Open [http://localhost:8090](http://localhost:8090) and pick a repo. To use a
Chromium app window, run `.\run.ps1 --app`.

Native Windows service installation is not implemented. Keep PowerShell open
or use your own process manager. Screenshots, jump-to-terminal, the native
folder picker, Finder reveal, and desktop deep links are hidden on Windows.

For Linux-style service management, use WSL2 and the instructions below.

## Running on Linux and WSL2

The session list, transcript reading, spawn, and follow-up paths work on
Linux. On a Linux desktop, the native folder picker uses `zenity`, `kdialog`,
or `yad` when available. On a headless box, Browse falls back to an in-browser
picker for the server's files. macOS-only screenshots, jump-to-terminal, and
desktop deep links are hidden.

Install Python, Git, and your agent CLIs inside the Linux machine or WSL distro:

```bash
git clone https://github.com/amirfish1/claude-command-center
cd claude-command-center
./run.sh
```

WSL2 users can open `http://localhost:8090` from their Windows browser.

For systemd user services:

```bash
./run.sh --install-service
./run.sh --service-status
journalctl --user -u ccc -f
```

The installer writes independent `ccc.service` and `ccc-worker.service` units
under `~/.config/systemd/user/`. Restarting the dashboard leaves the worker
running. Check both with `systemctl --user status ccc ccc-worker`, and remove
them with `./run.sh --uninstall-service`.

On a headless box without an active login, `sudo loginctl enable-linger $USER`
lets the user service survive logout and start at boot. WSL2 needs systemd
enabled for `--install-service`. If `systemctl --user` is unavailable, run
`./run.sh` in the foreground or use your own process manager. A foreground
launch also ensures a detached worker is running; its logs are
`~/.claude/command-center/logs/worker.{out,err}.log`.

To connect from another machine, read [SECURITY.md](../SECURITY.md) before
changing the bind address or allowed origins. [Phone access](phone-access.md)
is the guided trusted-network option.

## WatchTower queue engine

[WatchTower](https://github.com/amirfish1/watchtower) (`wt`) handles ticket
lifecycle, worker dispatch, plan imports, and delivery receipts. The installers
and `run.sh` bootstrap it so curl, PowerShell, Homebrew, the app, Docker, and
source installs share the same path.

`scripts/install-watchtower.sh` first looks for an existing checkout at
`$WATCHTOWER_DIR`, `~/Apps/watchtower`, or `~/dev/watchtower`. Next it tries a
shallow clone at `~/.ccc/watchtower`, then a source tarball, then the
`watchtower-cli` PyPI package as a last resort. That package can lag the repo.

WatchTower installs into the Python interpreter that runs `server.py`, since
CCC imports `watchtower.queue` in-process. A separate pipx install does not
serve that import. The source activation path supports CCC's Python 3.9+
floor even if package metadata requires a newer interpreter. WatchTower is a
hard dependency: CCC stops with a clear startup error if `watchtower.queue`
cannot be imported. There is no standalone queue fallback. The installer runs
`wt start` to keep the daemon available across login and reboot.

CCC fast-forwards its own WatchTower checkout at most once a day. It never
pulls a checkout you own; if yours is behind upstream, it tells you and leaves
it alone. `CCC_SKIP_WATCHTOWER=1` skips the installer, not the server's import
requirement; use it only when WatchTower is already importable. If `wt` is
installed but not on `PATH`, the dashboard can still import its package;
CLI surfaces are hidden and the installer tells you which directory to add.
See [Queues](QUEUES.md).

## Your agent config

First launch copies CCC's hook scripts into
`~/.claude/command-center/hooks/`. Registering hooks in agent settings or
installing skills requires your approval. The dashboard shows the proposed
changes; headless users can run `ccc consent`.

Other settings are preserved, symlinks stay symlinks, and files are backed up
before editing. An update that changes an approved item asks again. Remove
CCC's additions in **Settings > Maintenance > Agent config access**, or with
`ccc consent revoke all`. See the [full consent guide](agent-config-consent.md)
for each file and feature affected.
