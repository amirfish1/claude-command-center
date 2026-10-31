"""Validate an action authored in the originating model's answer footer."""

import re


def _validated_quick_action(raw):
    if not isinstance(raw, dict):
        return None
    label, reply = raw.get("label"), raw.get("reply")
    if (not isinstance(label, str) or len(label) > 24
            or not re.fullmatch(r"[^\W\d_]+(?: [^\W\d_]+){0,2}", label)
            or not isinstance(reply, str) or not 1 <= len(reply.strip()) <= 400
            or any(ord(c) < 32 for c in reply)):
        return None
    return {"label": label, "reply": reply.strip()}


def _validated_quick_actions(raw, limit=4):
    """An object is one go-ahead chip; an array is one chip per option.

    The whole array is rejected if any entry is invalid, has a duplicate
    label, or there are more than ``limit`` options: a partial decision would
    silently drop a choice the user was offered.
    """
    if isinstance(raw, dict):
        action = _validated_quick_action(raw)
        return [action] if action else None
    if not isinstance(raw, list) or not 1 <= len(raw) <= limit:
        return None
    actions = [_validated_quick_action(item) for item in raw]
    if not all(actions) or len({a["label"].lower() for a in actions}) != len(actions):
        return None
    return actions
