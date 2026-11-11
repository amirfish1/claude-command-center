# First-run matrix

Run `python3 scripts/first-run-matrix/run.py --quick` for one Ubuntu 24.04 curl attempt, or omit `--quick` for all four images and the curl, uvx, pipx and Homebrew routes. The exit code is nonzero when any route exits unsuccessfully or times out; failures are observations, not synthetic passes. `--output PATH` selects the JSON evidence file; route output includes the prerequisite inventory, combined command output and elapsed wall time, including container setup and any image pull. `--timeout N` bounds each route.

A read-only fixture is built from the tracked files in the current working tree, including tracked modifications, and made into a separate temporary Git repository. Curl reads the fixture using a local `file://` URL and the installer clones a local copy of that fixture. This avoids fetching a different revision of CCC. Each container has an isolated home, state and WatchTower store, with telemetry and WatchTower autostart disabled. The containers use `--network none`; no credentials or host services are mounted. They run as root, with noninteractive stdin, so these results do not measure an ordinary user's permissions or interactive prompts. Containers and temporary fixtures are removed after each attempt.

`--remediate` adds separately labeled attempts after installing the documented Linux prerequisites into a temporary image. This build requires network access to distribution package repositories; the measured commands themselves remain offline. `--python-tools` additionally installs pipx and uv from package repositories for a separately labeled tool-remediated run. Docker may retain downloaded base images and build cache. `--image` and `--route` restrict the selection. For a previously built wheel, `--wheel /absolute/path/to/package.whl` replaces the build step with that wheel and executes the documented uvx or pipx command. Without this option the runner executes the documented `uv build --wheel` step first. A supplied wheel is a separate prebuilt-artifact measurement, not proof that the documented build step worked.

Examples:

```sh
python3 scripts/first-run-matrix/run.py --quick --remediate
python3 scripts/first-run-matrix/run.py --quick --route uvx --remediate --python-tools --wheel /tmp/example.whl
```

The current runner does not start a local ntfy service, exercise on-call commands, count prompts, or capture browser screenshots. A successful long-running dashboard would be stopped at the timeout; timeout is not classified as success or readiness. All these are explicit coverage gaps. Offline execution also prevents WatchTower bootstrap and any other installation-time network access; it is an intentional safety boundary, not a faithful online-install success measurement.


An interrupted online-bootstrap experiment is recorded in `online-results.json`.
WatchTower installed successfully, but Docker port publication failed before
readiness. Its unfinished runner is not included. See
[the report](../../docs/first-run-report.md) for coverage and timing limits.
