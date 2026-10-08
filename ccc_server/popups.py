# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Pop-up approvals: every promo pop-up stays off until it is approved here.

Each unsolicited pop-up (toast, banner, modal, floating card, OS
notification) has an id. A pop-up shows only when its id is in APPROVED.
Approve one by adding its id to APPROVED below AND to the same list in
static/popups.js (tests/test_popups.py keeps the two lists equal).

Things the user opens on purpose (Settings panels, /?onboarding=1, the
"Send test notification" button) are not pop-ups and are never gated.

Stdlib-only; no side effects at import.
"""

from __future__ import annotations

# Every gated pop-up, with what it is. Keep in sync with static/popups.js.
ALL = {
    "moment-zero": "Full-screen Moment Zero onboarding auto-opens for a new user",
    "savings-milestone": "Full-screen confetti card when savings cross $10/$100/$1k",
    "notify-permission": "Card asking to turn on browser notifications",
    "notify-task": "Toast/banner when a session finishes or needs you",
    "notify-digest": "6pm 'Your daily agent report' toast and macOS banner",
    "notify-milestone": "'$X of agent work' milestone toast and macOS banner",
    "notify-other": "Any other /api/notify toast (info, success, error)",
    "star-ask": "'Star CCC on GitHub?' card at success moments",
    "router-detected": "Floating 'Use your existing router' card",
    "limit-failover": "'Limit reached: continue on a free model?' cards",
    "fleet-limit": "One grouped notice for sessions stopped by an engine usage limit",
    "leftover-offer": "Card offering tasks before unused plan allowance resets",
    "leftover-notification": "Daily reminder to use plan allowance before reset",
}

# Approved pop-ups. Nothing else shows until Amir approves it.
APPROVED = frozenset({
    "moment-zero",  # approved 2026-10-06
})

_NOTIFY_KIND_IDS = {
    "task": "notify-task",
    "needs_input": "notify-task",
    "digest": "notify-digest",
    "milestone": "notify-milestone",
    "leftover": "leftover-notification",
}


def allowed(popup_id):
    return popup_id in APPROVED


def notify_kind_id(kind):
    return _NOTIFY_KIND_IDS.get(str(kind or ""), "notify-other")


def notify_allowed(kind):
    return allowed(notify_kind_id(kind))
