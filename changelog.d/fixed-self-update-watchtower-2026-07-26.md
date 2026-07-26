**"Check for updates" now updates WatchTower too.** The in-app updater used
to fetch and reset CCC's own checkout and stop there, leaving the queue engine
— the thing that dispatches workers — on whatever revision it was installed
at. It now runs the shared `scripts/install-watchtower.sh`, bounces the
WatchTower daemon (`wt stop && wt start`, without which the freshly-pulled
code sits on disk while the old modules keep running), and then restarts CCC
as before. If `wt workers` shows anyone still working, the daemon restart is
deferred rather than killing an agent mid-ticket, and the response says so.
The pre-flight refusals on a dirty tree or a non-`main` branch are unchanged.
