Keep successfully steered messages delivered across late queue replies and pane refreshes. Refresh delivery history when a queued Steer targets a message that already left the queue, clearing stale cards without sending the message again. When Codex explicitly rejects an outdated active turn, refresh its turn identity before retrying Steer once.

Preserve queued-message identity through secondary-account workers so Steer and Cancel find the same message the browser displays.
