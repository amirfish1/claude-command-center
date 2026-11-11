# Team chat alerts

These channels are owner decision channels. They do not accept customer reports.
The preview stays off until you enable it. Tokens use the existing Vault or
`--secret-stdin token`; do not put a token in `--set` or a shell argument.
Adding a channel sends a test. Reply `ready` in its thread within 90 seconds.
To retry: `ccc oncall test CHANNEL --roundtrip`.

## Slack

Grant the bot `chat:write`, `im:write`, `im:read`, and `im:history`.
Run `ccc oncall channel add slack --vault token=slack-bot --set owner=USER_ID`.
CCC opens a direct message. Reply in the alert thread. Public channels and group
DMs are refused. No interactive buttons or public callback URL are used.
Slack is checked against the documented API with a local stand-in; not yet run against a real workspace.

## Discord

Grant the bot View Channel, Send Messages, and Read Message History in one text
channel. Enable Message Content Intent in the bot settings for guild messages.
Run `ccc oncall channel add discord --vault token=discord-bot --set owner=USER_ID --set channel_id=CHANNEL_ID`.
Use Discord's Reply action on the alert. Separate Discord thread channels are
not created or scanned. Other members may read alerts but cannot answer them.
Discord is checked against the documented API with a local stand-in; not yet run against a real workspace.

## Matrix

Use a separate account joined to an unencrypted room, with permission to send
`m.room.message` events. No admin power level is needed.
Run `ccc oncall channel add matrix --vault token=matrix-bot --set owner=@owner:example.org --set channel_id=ROOM_ID --set api_base=https://matrix.example.org`.
Reply in the alert thread. Encrypted rooms are refused. Only the exact owner
user ID counts. Matrix has no universal bot flag; choose a human owner account.
A limited sync timeline stops intake and retains its cursor for review.

## Mattermost

Use a bot access token with channel membership and the `read_channel` and
`create_post` permissions. The owner must be a different, non-bot user.
Run `ccc oncall channel add mattermost --vault token=mattermost-bot --set owner=USER_ID --set channel_id=CHANNEL_ID --set api_base=https://chat.example.org`.
Reply in the alert thread. Other channel members cannot answer it.

## Delivery and limits

Only replies to roots sent by this channel configuration count. Edits, bot
messages where the protocol marks them, unrelated messages, and other users
are ignored. Changing the owner, destination, server, or Vault reference starts
a new cursor namespace. The first send establishes the receive boundary.

Each poll reads at most one page. Slack rotates across known alert threads;
large numbers of threads increase reply latency. Discord and Mattermost read
100 posts per page. Matrix uses a one-second HTTP sync timeout. Normal engine
polls are 30 seconds apart. Retry-After holds requests until the specified time;
there is no retry sleep inside a normal poll. Setup temporarily holds the engine
lock while waiting, for up to 90 seconds.

The engine claims inbound messages in SQLite before acknowledging the batch.
A crash before a claim replays the batch. A crash after a claim but before an
answer is applied uses the existing needs-review recovery, not automatic replay.
This is not exactly-once delivery across the provider and SQLite. Root mappings
are retained; retention and very large histories are not solved here.

## Reproduce

`python3 scripts/oncall-chat-proof.py --quick` runs local API fixtures.
Use `--slack`, `--discord`, `--matrix`, or `--mattermost` to select channels.
Without `--quick`, Matrix and Mattermost start Docker servers; Slack and Discord
use loopback HTTP stand-ins. Docker ports, HOME, CCC state and WatchTower state
are temporary. The proof starts an isolated foreground watcher on port zero,
disables auto-drain, and removes its processes, containers, network and files.
It needs Docker, Python with pytest, and an installed WatchTower. No model runs.
A forced host shutdown or SIGKILL cannot execute cleanup.

The harness enables only its isolated Engine gate; it does not verify the dashboard
preview gate or run a dashboard HTTP server. The real-server check exercises
a decision alert, wrong-user rejection, a
restarted interpreter before reply intake, a real isolated ticket answer, and a
fixed notice from fixture closure. It does not establish that an agent fixed
code or that a real Slack or Discord client displayed anything. It does not
exercise a process kill during an API response.

API references: [Slack replies](https://docs.slack.dev/reference/methods/conversations.replies/),
[Discord messages](https://docs.discord.com/developers/resources/message),
[Matrix client-server API](https://spec.matrix.org/v1.16/client-server-api/),
[Mattermost posts](https://docs.mattermost.com/api).

On this branch, the Synapse 1.127.1 and Mattermost 10.5.0 proofs each passed
in 202 and 208 seconds on Linux, respectively. Mattermost used a bot access token and PostgreSQL 16.
The restart check replaces the Python interpreter and restarts the isolated
watcher after the reply is posted, before intake. It does not restart the homeserver.
