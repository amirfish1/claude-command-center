# When CCC pings group-chat participants

This is the exact set of events that cause CCC to inject `/group-chat` into a
participant session ("ping" = wake that session up to read the chat and reply).
Everything below is in `server.py`. A "ping" is always one call to
`_inject_text_into_session(sid, text)` where `text` is built by
`_group_chat_inject_text` — i.e. the `/group-chat` slash-command injection.

## TL;DR

A participant is pinged in exactly five situations:

1. **You create a chat** with them in it → immediate ping.
2. **You add them** to an existing chat → immediate ping.
3. **You press the Nudge button** on their row → immediate ping (just them).
4. **The chat file changes** (someone posts) → the background watcher pings the
   right participants on its next tick (within ~30s), throttled to once per 60s
   per chat.
5. **Nothing else.** No posts, no changes, paused, idle, done, or archived → no
   pings.

## The constants that govern timing

```
_COORD_POLL_INTERVAL  = 30        # watcher thread wakes every 30s
_COORD_NUDGE_INTERVAL = 60        # min seconds between auto-nudges for one chat
_COORD_DEATH_TIMEOUT  = 45 * 60   # 45 min with no file change → chat is dropped
```

So an auto-ping lands **0–30s** after a post (next watcher tick), and a given
chat can auto-ping **at most once per 60s** no matter how fast posts arrive.

---

## 1. Immediate pings (synchronous, no waiting on the watcher)

### Create — `_coordinate_sessions` (`POST /api/group-chat/create`)
When a chat is created, every session in `session_ids` is injected immediately,
in a loop, before the call returns. Then the chat is registered with the
background watcher (`_register_coordination`).

### Add participant — `_group_chat_add_participant` (`POST /api/group-chat/add`)
The added session is injected immediately. This is idempotent for *membership*
but **still delivers the check-in** even when re-adding an existing participant
(CCC-114): the join link doubles as a "go read the chat now" nudge.

### Manual nudge — `_group_chat_nudge(..., target_sid=...)` (`POST /api/group-chat/nudge`)
When the UI passes a `target_sid` (the per-participant **Nudge** button), all the
auto-targeting logic below is **bypassed**. Exactly that one participant is
pinged, regardless of who spoke last.

---

## 2. The automatic ping — the background watcher

This is the main path and the one people mean by "when does it ping."

`_coordination_watcher()` is a daemon thread. Every `_COORD_POLL_INTERVAL` (30s)
it walks every active coordination and, per chat:

1. Refreshes the wake-status header (`_group_chat_update_header_if_changed`).
2. Stats the `.md` file.
3. Under `_coord_lock`, decides one of three outcomes:
   - **Drop** the chat (stop pinging forever) if it has been idle longer than
     `_COORD_DEATH_TIMEOUT` (45 min) **or** the tail signals done
     (`_is_coord_done`: contains `we're done` / `✅ done`). Writes `closed_at`.
   - **Fire a nudge** if the file's mtime **changed** since the last tick **and**
     more than `_COORD_NUDGE_INTERVAL` (60s) has passed since the last nudge.
   - **Do nothing** otherwise.

So the trigger for an automatic ping is: **the chat file changed (someone
posted) and the 60s debounce has elapsed.** Posting through
`POST /api/group-chat/post` changes the mtime; so does a hand-edit. Either way
the next tick within 30s picks it up.

The check-and-claim of the nudge slot is done while holding `_coord_lock`, so two
ticks (or a tick racing a manual caller) can't both fire for the same post.

### Who gets pinged on an automatic nudge — `_group_chat_nudge` targeting

When the watcher fires `_group_chat_nudge(path)` with no `target_sid`, the
function reads the chat tail and decides *which* participants to ping based on
who authored last:

| Last author | Who gets pinged |
|---|---|
| **An agent** | Everyone **except** that agent (no self-nudge). |
| **Human**, message **@mentions** names or 8-hex short-ids | **Only** the addressed participants. |
| **Human**, no mentions, a prior agent exists | **Only** the agent who wrote immediately before the human (their reply is almost always for that agent). |
| **Human**, no mentions, no prior agent (fresh thread) | **Everyone.** |
| **An agent**, message @mentions specific participants | **Only** the addressed ones (writer always excluded). |

Guards that suppress the ping even when the above would select targets:

- **No recent author** — if the trailing window has only system `pinged …`
  lines and no real post, it returns `skipped: "no recent author"` and fires
  nothing. This is what stops a quiet chat from pinging itself in a loop.
- **Already reminded** — `last_reminder_key` (post count + last heading) is
  stored in the sidecar. If the same post was already reminded, it returns
  `skipped: "already reminded"`. So a ping fires **once per new post**, not
  every tick.

After a successful ping it writes a system `pinged <labels>` line and bumps the
cached baseline mtime so that admin write isn't mistaken for new activity on the
next tick.

---

## 3. When CCC will NOT ping (stop conditions)

- **Paused / disabled** — `_group_chat_is_paused(path)` true → the chat is kept
  out of the watcher entirely and `_group_chat_nudge` refuses (`error: paused`).
- **Idle > 45 min** — dropped from the watcher; `closed_at` is set.
- **Done marker** in the tail (`we're done` / `✅ done`) → dropped.
- **Archived** — never re-registered.
- **No file change** — no post, no ping. The watcher only acts on mtime changes.

---

## 4. After a server restart — `_start_coordination_watcher`

On boot CCC re-registers active chats from `~/.claude/group-chats` **except**:

- `archived: true` — explicitly retired, never resurrected.
- `closed_at` set — already idled-out or done; **not** resumed on boot. (This is
  what prevents "the chat started orchestrating again just because I restarted
  the server.")

A genuinely-active chat (`closed_at: None`) resumes normally. And any **new human
post clears `closed_at` and re-registers** the chat, so a closed chat comes back
to life the moment someone actually says something.

---

## One-line mental model

> CCC pings a participant the instant you put them in a chat or nudge them, and
> otherwise only when the chat file *changes* — at most once per 60s per chat,
> only to the participants the last message is actually addressed to, and never
> when the chat is paused, idle 45 min, done, or archived.
