# Hermes VM Deployment: CCC Service & Port Architecture

This document records the service architecture, systemd configuration, and port conventions for Claude Command Center (CCC) running on the **hermes VM**.

## Port Architecture: 8091 vs 8090

On the `hermes` host, port **8090** is held by the primary Hermes agent gateway (`hermes_cli.main gateway run`).

Running CCC on its default port 8090 caused port collisions and routed requests destined for CCC's Decision Inbox to the Hermes agent gateway instead (resulting in HTTP 404s).

### Decision

- **Hermes agent gateway owns port 8090** as the primary host agent gateway.
- **Claude Command Center owns port 8091** durably on the hermes host.
- `bym-studio-digest`'s `DECISION_INBOX_URL` points to `http://127.0.0.1:8091/api/decision-inbox`.

## Systemd Units: `/etc/systemd/system/` Convention

Previously, CCC on hermes was run under user systemd (`~/.config/systemd/user/ccc.service`), which was invisible to root administration sessions (`systemctl status` / `systemctl list-units`), had no memory bounds, and left port 8091 undocumented.

Following the convention established by `bym-dev.service` and `bym-digest-*`:
1. **System units in `/etc/systemd/system/`**:
   - `ccc.service`
   - `ccc-worker.service`
2. **Explicit User & Group**: `User=hermes`, `Group=hermes`.
3. **Blast-Radius Containment**:
   - `MemoryHigh=6G`, `MemoryMax=8G`, `MemorySwapMax=1G` for `ccc.service`.
   - Prevents runaway memory leaks or subprocesses from swap-thrashing the VM.
4. **Process Survival on Restart (`KillMode=process`)**:
   - Systemd signals only the main server/worker PID on restart, allowing spawned engine sessions and workers in detached process groups to survive reloads.
5. **Persistence Across Reboots**: Units are enabled under `multi-user.target`.

## Installing & Managing

On the hermes VM (as root or with sudo):

```bash
# Install and start services:
cd /home/hermes/Apps/claude-command-center
./systemd/install.sh

# Check status:
systemctl status ccc.service
systemctl status ccc-worker.service

# View logs:
journalctl -u ccc.service -f
journalctl -u ccc-worker.service -f
```
