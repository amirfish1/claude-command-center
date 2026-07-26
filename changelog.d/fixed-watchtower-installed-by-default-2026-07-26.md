**WatchTower now actually installs with CCC.** The README said WatchTower
shipped "installed by default as CCC's queue engine", but the installer only
probed for a WatchTower checkout you already had at `~/Apps/watchtower` or
`~/dev/watchtower` — every other user silently fell through to the built-in
fallback engine, which files tickets but never dispatches a worker, cannot
import a plan, and issues no delivery receipts. Homebrew, the DMG, and Docker
never even ran the probe.

The installers now fetch WatchTower for real (git clone to `~/.ccc/watchtower`,
installed editable so a later pull upgrades in place; `watchtower-cli` on PyPI
as the fallback), and `run.sh` bootstraps it on first launch — so every install
path ends up with it, not just the two that already had a dev checkout. It
installs into the same interpreter that runs `server.py`, and survives PEP 668
interpreters and virtualenvs. Needs Python 3.11+; on anything older CCC says so
once and stays on the fallback. `CCC_SKIP_WATCHTOWER=1` opts out.
