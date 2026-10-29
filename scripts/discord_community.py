#!/usr/bin/env python3
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""CCC Discord Community Provisioner & WatchTower Relay (stdlib-only).

Commands:
  setup             Provision the 4-channel CCC Discord layout (#start-here,
                    #general-fleet, #ship-log, #bugs-and-ideas), pin the
                    welcome message, create the #ship-log webhook, generate a
                    permanent invite URL, and optionally stamp README.md and
                    docs/community.md.
  relay             Poll #bugs-and-ideas and #general-fleet for new community
                    messages, file WatchTower tickets (wt add -q CCC), and
                    notify the local CCC dashboard (/api/notify).
  reply             Reply directly to a Discord message from CCC or terminal.
  ship              Post a release or update card to #ship-log via webhook.
  install-launchd   Install a macOS LaunchAgent that runs `relay --once` every
                    60 seconds in the background.

Configuration lives in ~/.claude/command-center/discord.json (mode 0600) or
environment variables DISCORD_BOT_TOKEN and DISCORD_GUILD_ID.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DISCORD_API_BASE = "https://discord.com/api/v10"
STATE_DIR = Path.home() / ".claude" / "command-center"
CONFIG_PATH = STATE_DIR / "discord.json"
STATE_PATH = STATE_DIR / "discord-relay-state.json"
LAUNCHD_LABEL = "ai.amirfish.ccc.discord-relay"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CCC_URL = os.environ.get("CCC_URL", "http://127.0.0.1:8090")
DEFAULT_WT_QUEUE = os.environ.get("CCC_DISCORD_QUEUE", "CCC-DISCORD")

# Discord channel types
GUILD_TEXT = 0
GUILD_CATEGORY = 4

# Permission bit: SEND_MESSAGES (1 << 11 = 2048), VIEW_CHANNEL (1 << 10 = 1024)
PERM_VIEW_CHANNEL = 1 << 10
PERM_SEND_MESSAGES = 1 << 11

CHANNEL_SPECS = [
    {
        "key": "start_here",
        "name": "start-here",
        "topic": "Welcome to Claude Command Center (CCC). Install in 1 command, links, and how this server works.",
        "read_only": True,
    },
    {
        "key": "general_fleet",
        "name": "general-fleet",
        "topic": "Show off your agent fleet, share workflows, and ask questions.",
        "read_only": False,
    },
    {
        "key": "ship_log",
        "name": "ship-log",
        "topic": "Automated feed of CCC releases, commits, and new feature clips.",
        "read_only": True,
    },
    {
        "key": "bugs_and_ideas",
        "name": "bugs-and-ideas",
        "topic": "Report bugs or pitch features. Posts here route directly into the CCC WatchTower queue.",
        "read_only": False,
    },
]

WELCOME_MESSAGE = """**Welcome to the CCC (Claude Command Center) Community**

CCC puts every coding agent session (Claude Code, Codex, Cursor, Antigravity, Kilo Code, Kimi Code, OpenCode, Devin) on one local board and tells you which one needs you.

**Quick Install (macOS / Linux):**
```bash
curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash
```
**Homebrew:**
```bash
brew install amirfish1/tap/ccc && ccc
```

**Where to go in this server:**
- **#general-fleet** : Share your multi-agent setups, ask workflow questions, and hang out with other builders.
- **#bugs-and-ideas** : Drop bug reports, stack traces, or feature ideas. Messages here are bridged directly into our local WatchTower queue (`CCC`) so nothing gets lost.
- **#ship-log** : Live stream of new CCC releases and demo clips.

**Links:**
- GitHub Repo: <https://github.com/amirfish1/claude-command-center>
- Live Read-Only Demo: <https://ccc.amirfish.ai/demo/>
- Documentation: <https://github.com/amirfish1/claude-command-center/blob/main/docs/features.md>
"""


def _read_json(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default)
    except (OSError, ValueError):
        return dict(default)


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _vault_token(entry_name: str = "discord-relay-token") -> str:
    ccc_bin = shutil.which("ccc") or str(Path.home() / ".local" / "bin" / "ccc")
    try:
        res = subprocess.run(
            [ccc_bin, "vault", "get", entry_name, "--reveal"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except OSError:
        pass
    return ""


def load_config() -> dict:
    cfg = _read_json(CONFIG_PATH, {})
    if os.environ.get("DISCORD_BOT_TOKEN"):
        cfg["bot_token"] = os.environ["DISCORD_BOT_TOKEN"].strip()
    elif not cfg.get("bot_token"):
        vault_val = _vault_token(cfg.get("vault_entry", "discord-relay-token"))
        if vault_val:
            cfg["bot_token"] = vault_val
            cfg["_from_vault"] = True
    if os.environ.get("DISCORD_GUILD_ID"):
        cfg["guild_id"] = os.environ["DISCORD_GUILD_ID"].strip()
    if os.environ.get("DISCORD_SHIP_WEBHOOK_URL"):
        cfg["ship_log_webhook_url"] = os.environ["DISCORD_SHIP_WEBHOOK_URL"].strip()
    return cfg



def discord_request(
    method: str,
    path: str,
    token: str,
    payload: dict | list | None = None,
) -> dict | list | None:
    url = path if path.startswith("http") else f"{DISCORD_API_BASE}{path}"
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "CCC-Discord-Bridge/1.0 (+https://github.com/amirfish1/claude-command-center)",
    }
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Discord API {method} {path} failed ({exc.code}): {err_body}") from exc


def update_repo_docs(invite_url: str, guild_id: str = "") -> list[str]:
    """Stamp the Discord invite URL into README.md and docs/community.md."""
    updated = []
    community_md = REPO_ROOT / "docs" / "community.md"
    if community_md.exists():
        text = community_md.read_text(encoding="utf-8")
        new_text = re.sub(
            r"https://discord\.gg/[A-Za-z0-9_-]+",
            invite_url,
            text,
        )
        if new_text != text:
            community_md.write_text(new_text, encoding="utf-8")
            updated.append(str(community_md))

    readme_md = REPO_ROOT / "README.md"
    if readme_md.exists():
        text = readme_md.read_text(encoding="utf-8")
        new_text = re.sub(
            r"https://discord\.gg/[A-Za-z0-9_-]+",
            invite_url,
            text,
        )
        if guild_id:
            new_text = re.sub(
                r"img\.shields\.io/discord/\d+",
                f"img.shields.io/discord/{guild_id}",
                new_text,
            )
        if new_text != text:
            readme_md.write_text(new_text, encoding="utf-8")
            updated.append(str(readme_md))
    return updated


def cmd_setup(args: argparse.Namespace) -> int:
    cfg = load_config()
    token = (args.bot_token or cfg.get("bot_token") or "").strip()
    guild_id = (args.guild_id or cfg.get("guild_id") or "").strip()

    if not token or not guild_id:
        print(
            "Missing Discord credentials.\n"
            "Pass --bot-token and --guild-id, or set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID.\n"
            "Quick 2-minute setup:\n"
            "  1. In Discord, click '+' on the left sidebar -> Create My Own -> 'Claude Command Center'.\n"
            "  2. Enable Developer Mode in Discord Settings -> Advanced, then right-click your server icon -> Copy Server ID.\n"
            "  3. Open https://discord.com/developers/applications -> New Application -> 'CCC Bot'.\n"
            "  4. Go to Bot -> Reset Token (copy it) and enable 'Message Content Intent'.\n"
            "  5. Go to OAuth2 -> URL Generator -> check 'bot', then check permissions:\n"
            "     Manage Channels, Manage Webhooks, Read Messages/View Channels, Send Messages,\n"
            "     Manage Messages, Add Reactions, Create Instant Invite.\n"
            "  6. Open the generated URL to invite the bot to your server, then run:\n"
            "     python3 scripts/discord_community.py setup --bot-token <TOKEN> --guild-id <GUILD_ID> --update-docs",
            file=sys.stderr,
        )
        return 1

    bot_user = discord_request("GET", "/users/@me", token) or {}
    bot_id = str(bot_user.get("id", ""))
    print(f"Authenticated as bot: {bot_user.get('username')} ({bot_id})")

    existing_channels = discord_request("GET", f"/guilds/{guild_id}/channels", token) or []
    by_name = {
        (c.get("name", "").lower(), c.get("type")): c
        for c in existing_channels
        if isinstance(c, dict)
    }

    # 1. Ensure category "CCC COMMUNITY"
    category_name = "CCC COMMUNITY"
    cat = by_name.get((category_name.lower(), GUILD_CATEGORY))
    if not cat:
        cat = discord_request(
            "POST",
            f"/guilds/{guild_id}/channels",
            token,
            {"name": category_name, "type": GUILD_CATEGORY},
        )
        print(f"Created category: {category_name} ({cat['id']})")
    else:
        print(f"Found existing category: {category_name} ({cat['id']})")
    category_id = cat["id"]

    # 2. Ensure the 4 channels
    channels_cfg = cfg.get("channels", {})
    for spec in CHANNEL_SPECS:
        ch = by_name.get((spec["name"], GUILD_TEXT))
        overwrites = []
        if spec["read_only"]:
            # @everyone role id in Discord is equal to guild_id
            overwrites.append(
                {
                    "id": str(guild_id),
                    "type": 0,  # role
                    "allow": str(PERM_VIEW_CHANNEL),
                    "deny": str(PERM_SEND_MESSAGES),
                }
            )
            if bot_id:
                overwrites.append(
                    {
                        "id": bot_id,
                        "type": 1,  # member
                        "allow": str(PERM_VIEW_CHANNEL | PERM_SEND_MESSAGES),
                        "deny": "0",
                    }
                )
        if not ch:
            payload = {
                "name": spec["name"],
                "type": GUILD_TEXT,
                "parent_id": category_id,
                "topic": spec["topic"],
            }
            if overwrites:
                payload["permission_overwrites"] = overwrites
            ch = discord_request("POST", f"/guilds/{guild_id}/channels", token, payload)
            print(f"Created #{spec['name']} ({ch['id']})")
        else:
            print(f"Found #{spec['name']} ({ch['id']})")
        channels_cfg[spec["key"]] = ch["id"]

    # 3. Post welcome message in #start-here if not already posted
    start_ch_id = channels_cfg["start_here"]
    recent_msgs = discord_request("GET", f"/channels/{start_ch_id}/messages?limit=10", token) or []
    has_welcome = any(
        "Welcome to the CCC" in (m.get("content") or "")
        for m in recent_msgs
        if isinstance(m, dict)
    )
    if not has_welcome:
        msg = discord_request(
            "POST",
            f"/channels/{start_ch_id}/messages",
            token,
            {"content": WELCOME_MESSAGE},
        )
        if msg and msg.get("id"):
            try:
                discord_request("PUT", f"/channels/{start_ch_id}/pins/{msg['id']}", token)
            except RuntimeError:
                pass
        print("Posted and pinned welcome message in #start-here")

    # 4. Ensure permanent invite URL on #start-here
    invite_url = cfg.get("invite_url", "")
    if not invite_url or args.refresh_invite:
        inv = discord_request(
            "POST",
            f"/channels/{start_ch_id}/invites",
            token,
            {"max_age": 0, "max_uses": 0, "unique": False},
        )
        if inv and inv.get("code"):
            invite_url = f"https://discord.gg/{inv['code']}"
            print(f"Created permanent invite: {invite_url}")

    # 5. Ensure webhook on #ship-log for automated release & clip posts
    ship_ch_id = channels_cfg["ship_log"]
    webhook_url = cfg.get("ship_log_webhook_url", "")
    if not webhook_url:
        hooks = discord_request("GET", f"/channels/{ship_ch_id}/webhooks", token) or []
        hook = next((h for h in hooks if h.get("name") == "CCC Ship Log"), None)
        if not hook:
            hook = discord_request(
                "POST",
                f"/channels/{ship_ch_id}/webhooks",
                token,
                {"name": "CCC Ship Log"},
            )
        if hook and hook.get("id") and hook.get("token"):
            webhook_url = f"{DISCORD_API_BASE}/webhooks/{hook['id']}/{hook['token']}"
            print("Configured #ship-log webhook")

    from_vault = bool(cfg.pop("_from_vault", False)) and not args.bot_token
    cfg.update(
        {
            "vault_entry": cfg.get("vault_entry", "discord-relay-token"),
            "bot_user_id": bot_id,
            "guild_id": guild_id,
            "category_id": category_id,
            "channels": channels_cfg,
            "invite_url": invite_url,
            "ship_log_webhook_url": webhook_url,
            "wt_queue": cfg.get("wt_queue", DEFAULT_WT_QUEUE),
            "ccc_url": cfg.get("ccc_url", DEFAULT_CCC_URL),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if from_vault:
        cfg.pop("bot_token", None)
    else:
        cfg["bot_token"] = token
    _write_json_atomic(CONFIG_PATH, cfg)
    print(f"Saved config to {CONFIG_PATH} (mode 0600)")

    if args.update_docs and invite_url:
        changed = update_repo_docs(invite_url, guild_id=guild_id)
        if changed:
            print("Updated repo docs with invite URL:")
            for p in changed:
                print(f"  - {p}")

    return 0


def _find_wt_bin() -> str | None:
    for candidate in (
        shutil.which("wt"),
        str(Path.home() / ".local" / "bin" / "wt"),
    ):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _file_wt_ticket(queue: str, title: str, text: str, jump_url: str, msg_id: str) -> str | None:
    wt_bin = _find_wt_bin()
    if not wt_bin:
        return None
    cmd = [
        wt_bin,
        "add",
        "-q",
        queue,
        "--title",
        title[:180],
        "--text",
        text,
        "--url",
        jump_url,
        "--dedupe-key",
        f"discord:{msg_id}",
    ]
    lower = f"{title} {text}".lower()
    if any(k in lower for k in ("bug", "error", "crash", "broken", "stack", "traceback", "fail")):
        cmd.extend(["--type", "bug"])
    else:
        cmd.extend(["--type", "feature"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except OSError:
        pass
    return None


def _notify_ccc(ccc_url: str, kind: str, title: str, body: str, url: str = "") -> bool:
    payload = {
        "kind": kind,
        "title": title[:200],
        "body": body[:1000],
        "source": "discord-relay",
    }
    if url:
        payload["url"] = url
    req = urllib.request.Request(
        f"{ccc_url.rstrip('/')}/api/notify",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except OSError:
        return False


def _extract_ticket_ref(wt_out: str | None) -> str:
    if not wt_out:
        return ""
    m = re.search(r"\b([A-Z0-9]+(?:-[A-Z0-9]+)*-\d+)\b", wt_out)
    return m.group(1) if m else ""


def _answer_wt_ticket(ref: str, answer_text: str) -> bool:
    wt_bin = _find_wt_bin()
    if not wt_bin:
        return False
    try:
        proc = subprocess.run(
            [wt_bin, "answer", ref, answer_text],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0
    except OSError:
        return False


def _sync_wt_resolutions(cfg: dict, state: dict) -> int:
    wt_bin = _find_wt_bin()
    if not wt_bin:
        return 0
    token = cfg.get("bot_token", "")
    guild_id = str(cfg.get("guild_id", ""))
    wt_queue = cfg.get("wt_queue", DEFAULT_WT_QUEUE)
    tickets: dict = state.setdefault("tickets", {})
    question_msgs: dict = state.setdefault("question_messages", {})

    # Discover any Discord-originated tickets from recent queue history
    try:
        proc = subprocess.run(
            [wt_bin, "ls", "-q", wt_queue, "--status", "all", "--limit", "0", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            rows = json.loads(proc.stdout)
            if isinstance(rows, list):
                for row in rows[-50:]:
                    ref = str(row.get("ref") or "")
                    url = str(row.get("url") or "")
                    m = re.match(
                        r"https://discord\.com/channels/(\d+)/(\d+)/(\d+)",
                        url,
                    )
                    if ref and m and m.group(1) == guild_id and ref not in tickets:
                        tickets[ref] = {
                            "channel_id": m.group(2),
                            "message_id": m.group(3),
                            "replied_closed": False,
                            "replied_blocked_at": "",
                        }
    except (OSError, ValueError):
        pass

    replied_count = 0
    for ref, info in list(tickets.items()):
        if not isinstance(info, dict) or info.get("replied_closed"):
            continue
        ch_id = str(info.get("channel_id") or "")
        msg_id = str(info.get("message_id") or "")
        if not ch_id or not msg_id:
            continue

        try:
            proc = subprocess.run(
                [wt_bin, "find", ref, "--json"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            detail = json.loads(proc.stdout)
        except (OSError, ValueError):
            continue

        status = str(detail.get("status") or "")
        if status == "closed":
            res = detail.get("resolution")
            summary = ""
            commit = ""
            if isinstance(res, dict):
                summary = str(res.get("summary") or "").strip()
                commit = str(res.get("commit") or "").strip()[:7]
            elif isinstance(res, str):
                summary = res.strip()

            header = f"\u2705 **Resolved in `{ref}`**"
            if commit:
                header += f" (`{commit}`)"
            reply_body = f"{header}\n\n{summary}" if summary else header
            try:
                discord_request(
                    "POST",
                    f"/channels/{ch_id}/messages",
                    token,
                    {
                        "content": reply_body[:1950],
                        "message_reference": {"message_id": msg_id},
                    },
                )
                check_emoji = urllib.parse.quote("\u2705")
                discord_request(
                    "PUT",
                    f"/channels/{ch_id}/messages/{msg_id}/reactions/{check_emoji}/@me",
                    token,
                )
                info["replied_closed"] = True
                info["closed_at"] = detail.get("closed_at") or datetime.now(timezone.utc).isoformat()
                replied_count += 1
            except RuntimeError:
                pass

        elif status in ("awaiting_answer", "blocked"):
            question = str(detail.get("block_question") or "").strip()
            updated_at = str(detail.get("updated_at") or "")
            if question and info.get("replied_blocked_at") != updated_at:
                reply_body = (
                    f"\u2753 **Question from worker on `{ref}`:**\n\n"
                    f"{question}\n\n"
                    f"*(Reply directly to this message in Discord to answer and resume `{ref}`)*"
                )
                try:
                    sent = discord_request(
                        "POST",
                        f"/channels/{ch_id}/messages",
                        token,
                        {
                            "content": reply_body[:1950],
                            "message_reference": {"message_id": msg_id},
                        },
                    )
                    if isinstance(sent, dict) and sent.get("id"):
                        question_msgs[str(sent["id"])] = ref
                    info["replied_blocked_at"] = updated_at
                    replied_count += 1
                except RuntimeError:
                    pass

    return replied_count


def poll_once(cfg: dict) -> int:
    token = cfg.get("bot_token", "")
    guild_id = cfg.get("guild_id", "")
    channels = cfg.get("channels", {})
    bot_user_id = str(cfg.get("bot_user_id", ""))
    wt_queue = cfg.get("wt_queue", DEFAULT_WT_QUEUE)
    ccc_url = cfg.get("ccc_url", DEFAULT_CCC_URL)

    if not token or not guild_id or not channels:
        raise RuntimeError(f"Discord config incomplete in {CONFIG_PATH}. Run `setup` first.")

    state = _read_json(STATE_PATH, {"last_message_id": {}, "processed_ids": [], "tickets": {}, "question_messages": {}})
    last_ids: dict = state.setdefault("last_message_id", {})
    processed: list = state.setdefault("processed_ids", [])
    tickets: dict = state.setdefault("tickets", {})
    question_msgs: dict = state.setdefault("question_messages", {})
    processed_set = set(processed)

    watched = [
        ("bugs_and_ideas", channels.get("bugs_and_ideas"), True),
        ("general_fleet", channels.get("general_fleet"), False),
    ]

    handled_count = 0
    for key, ch_id, file_to_wt in watched:
        if not ch_id:
            continue
        after_id = last_ids.get(ch_id)
        query = f"?limit=25&after={after_id}" if after_id else "?limit=10"
        msgs = discord_request("GET", f"/channels/{ch_id}/messages{query}", token) or []
        if not isinstance(msgs, list) or not msgs:
            continue

        # Discord returns newest first; sort oldest -> newest by snowflake ID
        msgs_sorted = sorted(msgs, key=lambda m: int(m.get("id", "0")))

        # On very first run with no after_id, seed cursor to latest message without backfilling old history
        if not after_id and not state.get("backfill_initial"):
            last_ids[ch_id] = str(msgs_sorted[-1]["id"])
            continue

        for m in msgs_sorted:
            msg_id = str(m.get("id", ""))
            if not msg_id:
                continue
            last_ids[ch_id] = msg_id
            if msg_id in processed_set:
                continue

            author = m.get("author") or {}
            if author.get("bot") or str(author.get("id", "")) == bot_user_id:
                continue

            content = (m.get("content") or "").strip()
            attachments = m.get("attachments") or []
            if not content and not attachments:
                continue

            username = author.get("global_name") or author.get("username") or "user"
            jump_url = f"https://discord.com/channels/{guild_id}/{ch_id}/{msg_id}"
            att_lines = [a.get("url", "") for a in attachments if a.get("url")]
            full_text = content
            if att_lines:
                full_text = (full_text + "\n\nAttachments:\n" + "\n".join(att_lines)).strip()

            # Check if this message is a reply to a blocked WatchTower question
            ref_obj = m.get("message_reference") or {}
            ref_msg_id = str(ref_obj.get("message_id") or "")
            if ref_msg_id and ref_msg_id in question_msgs:
                target_ref = question_msgs[ref_msg_id]
                if _answer_wt_ticket(target_ref, full_text):
                    try:
                        thumb = urllib.parse.quote("\U0001f44d")
                        discord_request(
                            "PUT",
                            f"/channels/{ch_id}/messages/{msg_id}/reactions/{thumb}/@me",
                            token,
                        )
                    except RuntimeError:
                        pass
                    processed.append(msg_id)
                    processed_set.add(msg_id)
                    handled_count += 1
                    continue

            first_line = (content.splitlines()[0] if content else "Attachment post")[:120]

            if file_to_wt:
                ticket_title = f"[Discord #{key.replace('_', '-')}] {username}: {first_line}"
                ticket_body = (
                    f"From Discord user @{username} in #{key.replace('_', '-')}:\n"
                    f"Jump URL: {jump_url}\n\n"
                    f"{full_text}\n\n"
                    f"To reply directly on Discord:\n"
                    f"python3 scripts/discord_community.py reply {ch_id} {msg_id} \"<your reply>\""
                )
                wt_out = _file_wt_ticket(wt_queue, ticket_title, ticket_body, jump_url, msg_id)
                ref = _extract_ticket_ref(wt_out)
                if ref:
                    tickets[ref] = {
                        "channel_id": ch_id,
                        "message_id": msg_id,
                        "replied_closed": False,
                        "replied_blocked_at": "",
                    }
                _notify_ccc(
                    ccc_url,
                    kind="needs_input",
                    title=f"Discord #{key.replace('_', '-')}: @{username}",
                    body=f"{first_line}\n{wt_out or jump_url}",
                    url=jump_url,
                )
                # React with eyes emoji so the user sees it was queued
                try:
                    emoji = urllib.parse.quote("\U0001f440")
                    discord_request(
                        "PUT",
                        f"/channels/{ch_id}/messages/{msg_id}/reactions/{emoji}/@me",
                        token,
                    )
                except RuntimeError:
                    pass
            else:
                _notify_ccc(
                    ccc_url,
                    kind="info",
                    title=f"Discord #general-fleet: @{username}",
                    body=first_line,
                    url=jump_url,
                )

            processed.append(msg_id)
            processed_set.add(msg_id)
            handled_count += 1

    # Sync any closed or blocked WatchTower tickets back to their Discord threads
    handled_count += _sync_wt_resolutions(cfg, state)

    # Bound processed list
    if len(processed) > 500:
        state["processed_ids"] = processed[-500:]
    _write_json_atomic(STATE_PATH, state)
    return handled_count


def cmd_relay(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.once:
        count = poll_once(cfg)
        print(f"Processed {count} new Discord messages.")
        return 0

    interval = max(15, int(args.interval))
    print(f"Starting CCC Discord relay loop (every {interval}s)...")
    while True:
        try:
            count = poll_once(cfg)
            if count:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Processed {count} messages")
        except Exception as exc:
            print(f"Relay poll error: {exc}", file=sys.stderr)
        time.sleep(interval)


def cmd_reply(args: argparse.Namespace) -> int:
    cfg = load_config()
    token = cfg.get("bot_token", "")
    if not token:
        print("Missing bot_token in config.", file=sys.stderr)
        return 1

    channels = cfg.get("channels", {})
    ch_id = channels.get(args.channel, args.channel)
    payload = {
        "content": args.message,
        "message_reference": {"message_id": str(args.message_id)},
    }
    res = discord_request("POST", f"/channels/{ch_id}/messages", token, payload)
    print(f"Sent reply ({res.get('id') if isinstance(res, dict) else 'ok'})")
    return 0


def cmd_ship(args: argparse.Namespace) -> int:
    cfg = load_config()
    webhook_url = (args.webhook_url or cfg.get("ship_log_webhook_url") or "").strip()
    if not webhook_url:
        print("Missing ship_log_webhook_url. Run `setup` or pass --webhook-url.", file=sys.stderr)
        return 1

    payload = {
        "username": "CCC Ship Log",
        "content": args.message,
    }
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "CCC-Discord-Bridge/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"Posted to #ship-log (HTTP {resp.status})")
    return 0


def cmd_install_launchd(args: argparse.Namespace) -> int:
    script_path = Path(__file__).resolve()
    python_bin = sys.executable or "/usr/bin/python3"
    log_path = STATE_DIR / "discord-relay.log"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    local_bin = str(Path.home() / ".local" / "bin")
    path_env = f"{local_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>{script_path}</string>
        <string>relay</string>
        <string>--once</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{path_env}</string>
        <key>HOME</key>
        <string>{Path.home()}</string>
    </dict>
    <key>StartInterval</key>
    <integer>{max(30, int(args.interval))}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""
    LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHD_PLIST.write_text(plist_content, encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)], check=False, capture_output=True)
    subprocess.run(["launchctl", "load", str(LAUNCHD_PLIST)], check=True)
    print(f"Installed and started LaunchAgent: {LAUNCHD_PLIST}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CCC Discord Community Provisioner & WatchTower Relay"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Provision channels, welcome pin, invite, and webhook")
    p_setup.add_argument("--bot-token", default="", help="Discord Bot Token")
    p_setup.add_argument("--guild-id", default="", help="Discord Server (Guild) ID")
    p_setup.add_argument("--refresh-invite", action="store_true", help="Create a fresh invite URL")
    p_setup.add_argument(
        "--update-docs",
        action="store_true",
        help="Stamp the generated invite link into README.md and docs/community.md",
    )
    p_setup.set_defaults(func=cmd_setup)

    p_relay = sub.add_parser("relay", help="Poll Discord channels and bridge to WatchTower + CCC")
    p_relay.add_argument("--once", action="store_true", help="Run a single poll tick and exit")
    p_relay.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")
    p_relay.set_defaults(func=cmd_relay)

    p_reply = sub.add_parser("reply", help="Reply to a Discord message")
    p_reply.add_argument("channel", help="Channel ID or key (bugs_and_ideas, general_fleet)")
    p_reply.add_argument("message_id", help="Target Discord message ID")
    p_reply.add_argument("message", help="Reply text")
    p_reply.set_defaults(func=cmd_reply)

    p_ship = sub.add_parser("ship", help="Post an update to #ship-log via webhook")
    p_ship.add_argument("message", help="Markdown text to post to #ship-log")
    p_ship.add_argument("--webhook-url", default="", help="Override webhook URL")
    p_ship.set_defaults(func=cmd_ship)

    p_launchd = sub.add_parser("install-launchd", help="Install background macOS LaunchAgent")
    p_launchd.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")
    p_launchd.set_defaults(func=cmd_install_launchd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
