# Discord community -> WatchTower queue + CCC notify

Turn a 4-channel Discord server into a live product intake loop connected directly to Claude Command Center (CCC) and WatchTower (`wt`).

Instead of keeping Discord open all day or letting bug reports get buried in chat scrollback, a lightweight stdlib Python script ([scripts/discord_community.py](../scripts/discord_community.py)) provisions your server and polls your channels once a minute via `launchd`:

- Posts in `#bugs-and-ideas` become numbered WatchTower tickets (`wt add -q CCC`) with a deduplicated key (`discord:<message_id>`), auto-tagged as `bug` or `feature`, and reacted to with 👀 so the reporter knows it landed.
- Questions in `#general-fleet` fire a live toast notification to your CCC dashboard (`POST /api/notify`).
- New releases and demo clips stream automatically into `#ship-log` via webhook so your server stays active 24/7.

## Architecture

```
Discord Server                        launchd (every 60s)                  CCC + WatchTower
┌─────────────────────┐   REST poll   ┌───────────────────────────┐        ┌──────────────────────┐
│ #bugs-and-ideas     │ ────────────▶ │ scripts/                  │ ─────▶ │ wt add -q CCC        │
│ #general-fleet      │               │   discord_community.py    │        │ POST /api/notify     │
│ #ship-log (webhook) │ ◀──────────── │   setup | relay | reply   │        └──────────────────────┘
└─────────────────────┘               └───────────────────────────┘
```

## 1. Create the Discord Application & Blank Server (2 minutes)

1. In Discord, click the **+** icon on the left sidebar -> **Create My Own** -> **For a club or community** -> name it **Claude Command Center**.
2. In Discord **User Settings -> Advanced**, turn on **Developer Mode**. Right-click your new server icon and click **Copy Server ID** (`DISCORD_GUILD_ID`).
3. Open the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application** -> name it **CCC Bot**.
4. Go to the **Bot** tab:
   - Click **Reset Token** and copy your `DISCORD_BOT_TOKEN`.
   - Scroll down to **Privileged Gateway Intents** and enable **Message Content Intent** (so the REST API returns message text).
5. Go to **OAuth2 -> URL Generator**:
   - Under **Scopes**, check `bot`.
   - Under **Bot Permissions**, check:
     - `Manage Channels`
     - `Manage Webhooks`
     - `Read Messages/View Channels`
     - `Send Messages`
     - `Manage Messages` (for pinning the welcome post)
     - `Add Reactions`
     - `Create Instant Invite`
   - Open the generated URL in your browser and authorize the bot into your server.

## 2. Auto-Provision the Channels, Welcome Pin, Webhook, and Docs

Run `setup` once:

```bash
python3 scripts/discord_community.py setup \
  --bot-token "<YOUR_BOT_TOKEN>" \
  --guild-id "<YOUR_GUILD_ID>" \
  --update-docs
```

What this does automatically:
- Creates the **CCC COMMUNITY** category and 4 channels (`#start-here`, `#general-fleet`, `#ship-log`, `#bugs-and-ideas`).
- Locks `#start-here` and `#ship-log` to read-only for `@everyone` so they stay clean.
- Posts and pins the welcome guide in `#start-here`.
- Generates a permanent, never-expiring invite link (`https://discord.gg/<code>`).
- Creates the `CCC Ship Log` webhook on `#ship-log`.
- Writes your config and credentials to `~/.claude/command-center/discord.json` (`chmod 600`, never inside the git repo).
- With `--update-docs`, stamps your new `https://discord.gg/<code>` URL directly into `README.md` and `docs/community.md`.

## 3. Start the Background Relay (`launchd`)

Test a single poll tick by hand:

```bash
python3 scripts/discord_community.py relay --once
```

Then install the background `launchd` agent so it polls every 60 seconds automatically:

```bash
python3 scripts/discord_community.py install-launchd --interval 60
```

## 4. Replying and Shipping from the Terminal (or from an Agent)

Reply directly to any Discord user's message from CCC or your terminal:

```bash
python3 scripts/discord_community.py reply bugs_and_ideas <MESSAGE_ID> "Fixed in main, shipping in the next cut!"
```

Post a release announcement or demo clip to `#ship-log`:

```bash
python3 scripts/discord_community.py ship "🚀 **CCC v5.38 is out!** Faster session switching + Discord relay."
```
