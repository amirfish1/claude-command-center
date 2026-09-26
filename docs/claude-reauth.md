# Claude re-authenticate

When a node's Claude Code login dies, every Claude session on that node stops
with one of:

- `Failed to authenticate: OAuth session expired and could not be refreshed`
- `Not logged in · Please run /login`
- `claude is not authenticated`

CCC can run the login on the affected node from the browser, whether the node
is this machine or a paired federation peer. You never need to ssh in.

This is a preview feature. Turn it on in **Settings > Experimental > Claude
re-authenticate** (or `CCC_FF_CLAUDE_REAUTH=1`).

## Where it shows up

- **Session rows.** Claude Code writes a synthetic `authentication_failed`
  turn into the transcript. Rows whose last turn is that error get a
  **Re-authenticate** chip.
- **Fleet page.** Each node in the health strip gets a **Re-authenticate (N)**
  button when N of its sessions have that error. Use this for peers.

## What happens when you click it

1. CCC runs `claude auth login` inside a detached tmux session (`ccc-claude-auth`)
   on the node that owns the failing sessions. It sets `BROWSER=true` so that
   node does not open a browser of its own.
2. The sign-in URL the CLI prints opens in a new tab in **your** browser.
3. You approve, and the page shows a one-time code (`<code>#<state>`). Paste
   it into the CCC box and submit.
4. CCC pastes the code into the same tmux session, then checks
   `claude auth status` (`loggedIn: true`) and runs `claude -p "reply with exactly: ok"`.
5. On success, every flagged session is sent a message telling it to retry the
   step that failed. WatchTower workers are told to re-run `wt claim`.

Clicking Start again while an attempt is waiting returns the **same** URL. Each
code is tied to its attempt's PKCE state, so starting a new login would make
the code you are about to paste useless. If a code is rejected, the attempt is
used up. Click **Start over** to get a fresh page.

## Requirements and settings

- `tmux` on the node that is logging in. A login run with nohup or with stdin
  redirected from `/dev/null` can never receive the code. Hardened Linux hosts
  also block keystroke injection (`legacy_tiocsti=0`), so tmux is the only
  reliable way to get the code into the prompt.
- The login runs as the user that runs that node's CCC, which is normally the
  user its workers run as. If workers run as a different account, set
  `CCC_CLAUDE_AUTH_USER=<user>` for CCC. The login, status check and test
  prompt then run through `sudo -n -u <user> -H`, which needs passwordless sudo
  for that account.
- `CCC_CLAUDE_BIN` picks the `claude` binary, as it does elsewhere in CCC.

Do not copy `~/.claude/.credentials.json` from another machine. Refresh tokens
rotate, so a copied login breaks as soon as either machine refreshes. Each
machine needs its own login.

## API

All endpoints are same-origin POSTs, except `status`, which also accepts GET.
Each one takes an optional `node_id`. When `node_id` names a paired peer, the
call is sent through the federation route envelope (`claude_auth_*` actions)
and runs on that peer.

| Endpoint | Body | Result |
|---|---|---|
| `/api/claude-auth/status` | `{node_id?}` | `{status: {logged_in, email, auth_method}, attempt: {state, url, ...}, tmux}` |
| `/api/claude-auth/start` | `{node_id?, force?}` | `{ok, attempt_id, url, reused}` |
| `/api/claude-auth/submit` | `{node_id?, attempt_id, code, smoke?}` | `{ok, logged_in, email, smoke: {ok, detail}}` |
| `/api/claude-auth/cancel` | `{node_id?}` | `{ok}` |
| `/api/claude-auth/nudge` | `{node_id?, session_ids: [...]}` | `{ok, nudged, results}` |

The one-time code is sent to tmux over stdin (`load-buffer -`), so it never
appears in a process list. CCC does not log or store it, and never returns it
in a response. Error messages built from the terminal have the code, URLs and
any long token-like strings removed first.

On a peer, a routed call is authorised by the pairing secret, so it does not
need that peer's own Experimental toggle. Only the node you are using needs
the preview turned on.
