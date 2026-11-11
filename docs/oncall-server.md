# On-call on an always-on Linux server

This path keeps the dashboard, control-plane worker and WatchTower running
without a laptop. On-call is a preview and stays off until setup enables it.
It needs a server you control, Docker with Compose, and a repository the agent
can edit. Agent model usage and server hosting are separate costs.

## Start with three commands

From a clone of this repository on Linux:

```sh
docker compose -f compose.oncall.yml --profile ntfy up -d --build
docker compose -f compose.oncall.yml exec -T ccc git clone YOUR_REPOSITORY /state/project
docker compose -f compose.oncall.yml exec -T ccc ccc oncall setup --yes --repo /state/project --queue BUGS --channel ntfy --set server=http://127.0.0.1:8080
```

Supply your repository URL. Private repositories need your own Git credentials.
These commands configure alerts, not an agent login. Complete the agent setup
below before expecting fixes. Setup sends a test message and fails if it cannot
send. Every answer can be supplied with `--repo`, `--queue`, `--channel`, and
repeated `--set FIELD=VALUE`. `--yes` opts into the preview and suppresses prompts.
Use `--vault FIELD=ENTRY` for existing vault secrets, or `--secret-stdin FIELD`
for one secret on standard input. Missing required values fail with a message.
Without a terminal, enabling the preview requires `--yes`.

The dashboard listens on 127.0.0.1:8090. The Linux Compose service uses host
networking so loopback remains loopback. It publishes no dashboard port and
changes no origin or local-only check. Do not change the bind address to make
Docker port publishing work. Use the existing remote-access options in
[On-call](oncall.md). Docker Desktop host networking is not verified here.

The optional ntfy profile listens on host loopback port 8080. Its cache shares
the state volume. This local service alone cannot send push notifications to a
phone away from the server. Use your own secured ntfy endpoint and supply its
URL with `--set server=...` for remote delivery. Follow ntfy's authentication
and TLS setup before exposing it. Topic possession grants reply authority.

## Supply the agent

The image includes WatchTower pinned to a source commit, Python, Git and basic
shell tools. It does not contain Codex or Claude Code, or any login. Install a
Linux Codex executable for the server's architecture and mount it read-only:

```yaml
# Save as compose.agents.yml and add -f compose.agents.yml to Compose commands.
services:
  ccc:
    environment:
      PATH: /opt/codex/bin:/state/bin:/usr/local/bin:/usr/bin:/bin
    volumes:
      - /absolute/path/to/codex-release/bin:/opt/codex/bin:ro
      - /absolute/path/to/server-codex-home:/state/.codex
```

The override prepends `/opt/codex/bin` to PATH. Mount the whole release
bin directory, including `codex-code-mode-host` when the release provides it.
Make this directory and its executables readable and executable by UID 1000.
Mounting only `codex` can start the model while leaving its command tools broken.

Use a dedicated copy of the Codex home with `auth.json`, writable by UID 1000.
Codex may refresh its login. A read-only auth file can prevent refresh. Do not
bake credentials into an image. Use the complete standalone Linux release; a JavaScript
launcher also needs its matching Node runtime and package tree, which this
image does not provide. Configure the queue explicitly:

```sh
docker compose -f compose.oncall.yml exec -T ccc wt config -q BUGS --engine codex --model gpt-6.1-terra --effort medium --codex-transport exec
```

The Codex mount is a credential input outside the state volume; include that
separate directory in your secure backup. Alternatively copy the dedicated
Codex home into `/state/.codex` to keep all runtime state in the one volume.
Keep application checkouts under `/state` for the same reason. The image runs
as UID 1000. Bind mounts must allow that user to write.

Claude Code is not supplied or tested in this container path. macOS Keychain
logins do not transfer to Linux. No Claude Code credentials are read or copied
by these proofs. A native Linux Claude Code install may work with its own
supported login procedure, but this document makes no verified support claim.
iMessage, desktop terminals and macOS UI tools are unavailable in Linux.
Workers share the service user's authority; this is not a worker sandbox.
Read [the security limits](oncall-security.md) before accepting hostile reports.

## Native service, without Docker

Install Python 3.11 or newer, the WatchTower revision pinned in
`Dockerfile.oncall`, and the agent CLI in the same user's PATH. From a source checkout run:

```sh
./ccc service install
./ccc service status
./ccc service uninstall
```

On Linux these manage `~/.config/systemd/user/ccc-oncall.service`. The service
starts the same foreground supervisor. Logs go to the user journal:
`journalctl --user -u ccc-oncall.service`. Its unit records this checkout,
Python executable and PATH. Keep them in place; uninstall and install again
if their locations change. It refuses to overwrite a different unit.
An administrator must enable lingering for this user if it must start at boot
and survive logout: `loginctl enable-linger USER`. No root system service is
installed. Do not run this alongside another CCC instance for the same checkout.

After the native service starts, use `./ccc oncall setup --yes` with the same
repository, queue and channel flags shown above, without the Compose prefix.
The native path needs your own ntfy endpoint or another supported channel.

On macOS `ccc service install` prints a launchd plist only. It does not load it.
The output is not tested here. Status and uninstall are Linux-only.

## Updates and backups

Keep the same Compose project name and volume. Pull the reviewed source update,
then run `docker compose -f compose.oncall.yml up -d --build ccc`. This replaces
the container. Stop or finish active workers before planned updates. A restart
is not an exactly-once delivery guarantee; see [recovery limits](oncall-reliability.md).
The supervisor sends logs to stdout; use `docker compose -f compose.oncall.yml logs`.
Agent transcripts and queue logs also remain under `/state`.

For a consistent backup, stop both services, then archive the volume:

```sh
docker compose -f compose.oncall.yml --profile ntfy stop
docker compose -f compose.oncall.yml run --rm --no-deps --entrypoint tar ccc -C /state -czf - . > oncall-state.tgz
docker compose -f compose.oncall.yml --profile ntfy start
```

Encrypt and restrict the archive: it contains reports, transcripts and possibly
credentials. Restore into an empty volume as UID 1000, then start the services.
Do not run two copies against the same volume. Backup restoration is not yet
covered by the proof. The optional ntfy cache uses the volume root alongside
CCC's home directories.

## Reproduce the checks

```sh
python3 scripts/oncall-server-proof.py --quick
python3 scripts/oncall-server-proof.py
python3 scripts/oncall-service-proof.py
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
```

The default server proof allows paid `gpt-6.1-terra` workers at medium effort,
including a replacement after restart.
It copies only Codex auth into temporary storage, runs local ntfy, files a bug,
restarts the container after observing a worker, and checks the fix and notice.
Quick mode does not use a login or run an agent. The scripts print measurements
and remove containers and volumes on ordinary completion or failure. SIGKILL
or host loss cannot run cleanup. The service proof requires a Docker engine
capable of running systemd in a privileged container with a private cgroup
namespace. It never invokes the host service manager.

An earlier regression run passed 518 tests in 104.34 seconds on the build
host. No performance bound was changed. Image and idle measurements below are
specific to the proof fixture, not sizing guarantees for active agents.

The latest unrestricted regression run had 517 passes and one failure in
334.17 seconds. The full demo took 4.462 seconds against its unchanged
two-second startup budget. Its behavior checks passed. Host load was high,
but that alone does not establish the cause. This performance check remains
unresolved. An intermediate restricted helper run could not create local
sockets and does not establish product failures or a passing regression run.

The container supervisor gives WatchTower a fresh runtime PID file on each
launch. The native user service keeps the usual PID file so CLI clients can
find the watcher.
This avoids a reproduced restart failure where a saved PID matched a new
container process and WatchTower incorrectly reported an existing watcher.
Ticket and report state still live on the volume. This workaround does not
change WatchTower's worker recovery policy.

The latest Linux user-service proof passed installation, status, dashboard
health and uninstallation inside a systemd container. No host service was
changed.

Use the `exec` transport in the queue command above. An earlier WatchTower
revision held a dead Codex claim after restart because a persistent registry
entry was treated as proof of life. The image now pins a revision with the
upstream correction. The proof exercises exec recovery; it does not verify
hosted app-server recovery.

The latest complete paid proof passed headless setup, loopback-only dashboard
binding, saved setup across a restart, and paid worker recovery. The restart
was triggered while the ticket was in progress, before a fix commit existed.
After restart, the `gpt-6.1-terra` worker completed the fix, its test passed,
and the ntfy notice passed. The complete proof took 371.76 seconds. Its image
size was 248,196,773 bytes (about 237 MiB). After 60 quiet seconds, its
`docker stats` sample was 327 MiB and 1.00% of one CPU.

An earlier quick run after 60 quiet seconds used 390 MiB and 31.19% of one CPU
in `docker stats`. That earlier image was 248,176,114 bytes (about 237 MiB).
Unlike the complete paid proof, the quick run did not run a fixing agent.
These are individual samples on a busy shared Linux build host.
Neither sample is a CPU average or an upper memory bound. Active agents need
additional memory and model usage. The first immediate post-restart sample was higher:
410.4 MiB and 80.26% CPU. Allow for startup work; this is not a near-zero-CPU
idle service. Hosting charges depend on your server provider. Model usage is
billed through the agent login you supply. No image was published.

The quick run took 212.13 seconds with a cached image, including startup,
setup, restart and the quiet interval. A cold build also downloads packages.
Compose v2.39.4 validated the configuration using a temporary downloaded binary.
Runtime checks use Docker commands directly. The Compose launch, macOS plist, remote phone delivery,
backup restore and long-running operation remain unverified.

Two earlier full regression runs had 517 passes and one failure in the
unchanged two-second demo startup budget while host load was high. The focused demo check passed on
a subsequent run. These timing failures were not fixed by weakening the bound.
