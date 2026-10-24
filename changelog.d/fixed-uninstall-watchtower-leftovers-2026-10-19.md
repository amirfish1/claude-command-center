**Uninstall covers WatchTower leftovers.** `scripts/uninstall.sh` now stops the
`ai.watchtower.watcher` LaunchAgent, removes Homebrew-style (including broken)
`~/.local/bin/ccc` links, strips CCC hook entries from Claude/Codex config,
deletes WatchTower skill symlinks and the managed `~/.ccc/watchtower` clone, and
with `--purge-data` also removes `~/.watchtower`.
