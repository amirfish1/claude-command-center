---
name: group-chat
description: Coordinate parallel sessions for discussion, task execution, and git commits.
allowed-tools: Read, Edit, Write, Bash
---

Coordinate with parallel sessions via a dedicated file per discussion, located in the `group-chats/` directory at the workspace root. Use this to ask questions, propose work division, or safely execute tasks.

## 1. Setup & Discovery
- **Find the File:** To ensure independent sessions find the same file, check `$ARGUMENTS` for a specific topic or file path. If none is provided, list the `group-chats/` directory and use the most recently modified active chat file. If you are initiating a new discussion, create a new file (e.g., `group-chats/chat_<YYYY-MM-DD>_<topic>.md`).
- **Identity:** Generate or retrieve your tag (e.g., hash of `$CLAUDE_SESSION_ID`, stored in `~/.claude/group-chat/sessions/<hash>.tag`).

## 2. Interact (Append Only)
Read the chosen chat file to see the current state. **Append** your post. NEVER edit existing lines. 

**Format:** `## <timestamp> — <your-tag> <emoji>`
**Body:** <Concise message>

**Action Types:**
- 💬 **Discuss:** Ask questions, share context, or reply. No execution needed.
- 📝 **Propose:** Outline a numbered plan assigning specific execution steps to specific tags. **Never assign tasks to tags that have left.**
- ✅ **Ack:** Agree to a proposal. Execution requires ALL assigned tags to ack.
- ❌ **Counter/Abort:** Reject a proposal or halt execution.
- ▶ **Start:** Announce you are starting your assigned execution step.
- 🏁 **Done:** Announce your step or the overall task is complete.
- 👋 **Leave:** Announce you are dropping off (as an observer or done). You can no longer be assigned tasks.

## 3. Execution Rules
1. **Wait for Consensus:** NEVER start executing a proposed plan until all assigned tags have posted an `✅ Ack`.
2. **Execute Only Your Steps:** Only perform the tasks assigned to your tag in the proposal.
3. **No Ghost Assignments:** If you have no work, post `👋 Leave` before exiting. Proposers must NEVER assign tasks (e.g., final pushes) to a tag that has already posted `👋 Leave`.
4. **Git Commits (If applicable):** Use atomic explicit paths (`git commit --only <paths> -m "msg"`). NEVER commit the chat file.
