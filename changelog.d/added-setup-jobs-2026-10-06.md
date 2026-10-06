- Added a first-run setup service: `GET /api/setup/plan` detects everything a
  fresh machine needs (Apple tools, Git, Python, Node.js, Claude Code, GitHub
  CLI, plus the free-model steps), `POST /api/setup/run` installs them in a
  streamed background job, and `GET /api/setup/jobs/<id>` polls progress. A
  new `/setup` page renders the one-click wizard (`window.cccSetup`) with
  per-step consent, a live log, and a cancel button. Node.js installs are
  downloaded from nodejs.org into `~/.ccc/runtime/node` and verified against
  the official SHASUMS256 checksums, no admin password required.
