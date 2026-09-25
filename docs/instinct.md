# Instinct: a daily brief you didn't have to ask for

Instinct watches your repos, CCC sessions, and WatchTower queues, then writes
one HTML page each morning that answers three questions:

- **What changed.** Commits on local branches since the last brief, grouped by
  Conventional Commit type. For the files those commits touched, it quotes the
  [Hunch](https://github.com/davesheffer/hunch) "why": recorded decisions,
  rejected alternatives, and file-scoped invariants.
- **What's stuck.** Sessions waiting on you (the same live feed as *Needs Your
  Attention*), tickets blocked on a human answer or product gate, queues that
  stopped moving, and commits nobody pushed.
- **What to do next.** A ranked action list with the exact command to run, and
  **proposed tickets**. Proposals are a dry run: each one comes with a
  ready-to-paste `wt add` command, and Instinct never files anything itself.

It is read-only and cheap. Each repo costs two `git` subprocesses. CCC costs
one `GET /api/attention?scope=live`, and WatchTower costs three `wt … --json`
calls. Hunch's committed `.hunch/` graph is read straight from disk, and no
model is called. A source it can't reach appears under **Blind spots**, and the
rest of the brief still renders.

## Try it

```bash
python3 -m ccc_server.instinct brief --since 48
# 47 commit(s) across 2 repo(s), 8 thing(s) blocked on you, 11 new ticket idea(s).
# ~/.claude/command-center/instinct/brief-2026-09-25.html
```

Open the printed path, or `latest.html` in the same folder. Other options:

| Flag | Effect |
|---|---|
| `--since HOURS` | Override the window. The default is "since the last brief", capped at a week. |
| `--json` | Also print the brief as JSON, for other agents to consume. |
| `--no-ccc` / `--no-wt` | Skip a source. |
| `--save-snapshot F` / `--snapshot F` | Record the raw inputs, or re-render from them. Useful for demos and bug reports. |
| `--publish` | Run your `publish_command` hook (see below). |

Each run writes `brief-DATE.html`, `brief-DATE.json`, `proposals-DATE.json`,
and `latest.html`, with `0600` permissions in a `0700` directory. A brief
contains session names and ticket text, so treat it as private.

## Configure

```bash
python3 -m ccc_server.instinct init-config   # writes ~/.claude/command-center/instinct.json
```

| Key | Default | Meaning |
|---|---|---|
| `repos` | `[]` | Repos to always watch. |
| `auto_discover_repos` | `true` | Also watch repos where CCC saw sessions in the last 7 days. |
| `max_repos` | `12` | Upper bound on repos per brief. |
| `ccc_url` | `$CCC_URL` or `http://127.0.0.1:$PORT` | Your local dashboard. |
| `repo_queues` | `{}` | `{"/path/to/repo": "QUEUE"}`: where proposals for that repo go. When a repo isn't listed, Instinct matches its `origin` against WatchTower's `github_repo` queue config. If nothing matches, the command shows `<QUEUE>`. |
| `stuck_queue_days` | `3` | A non-empty queue with no progress for this long is flagged. |
| `stale_blocked_days` | `14` | Tickets blocked longer than this are rolled into one "sweep" action instead of cluttering the top of the list. |
| `unpushed_hours` | `12` | Flags commits that have sat unpushed this long. |
| `hotspot_fix_count` | `3` | Proposes a root-cause ticket when this many `fix` commits touch one file (tests excluded). |
| `publish_command` | `null` | An argv list, for example `["~/bin/publish-page", "{html}", "--title", "{title}"]`. The last line of stdout is taken as the URL. It runs only with `--publish`. |

## Proposal types

| Signal | Proposal |
|---|---|
| ≥ N `fix` commits touched one file | *Fix hotspot*: find the shared root cause and add a regression test |
| A `revert` commit | *Re-land or close out*: record why it was abandoned |
| A file that anchors Hunch decisions changed after they were recorded | *Re-verify N Hunch decisions* (one ticket per repo) |
| A queue has been stalled for days | *Triage the backlog* |

A proposal seen in the last 7 days is labelled "still open from an earlier
brief" instead of "new", so the brief doesn't nag.

## Run it daily

```bash
scripts/instinct-schedule.sh              # print the systemd timer / LaunchAgent
scripts/instinct-schedule.sh --install    # enable it (INSTINCT_AT=07:30 to change the time)
scripts/instinct-schedule.sh --uninstall
```

`instinct.py` is a standalone CLI. The dashboard doesn't import it, so
enabling it needs no restart.
