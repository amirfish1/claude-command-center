**WatchTower is now actually installed on every path.** One shared
`scripts/install-watchtower.sh` owns the chain — existing local checkout,
then a shallow clone at `~/.ccc/watchtower`, then a source tarball, and only
as a last resort the lagging `watchtower-cli` release on PyPI — installing
into the same interpreter that runs `server.py` and finishing with `wt start`
so the daemon survives reboot. `run.sh` and `scripts/install.sh` both call it,
so Homebrew, the DMG, Docker and a plain `git clone` get the same result as a
`curl` install. The CCC-managed clone fast-forwards at most once a day; a
checkout of your own is never pulled, only reported when it falls behind.
