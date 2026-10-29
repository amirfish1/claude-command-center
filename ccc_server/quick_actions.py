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
