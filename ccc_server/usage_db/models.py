"""Model-id normalization: pricing keys and (only when grounded) family/label."""

from __future__ import annotations

import re
from typing import Optional, Tuple


def pricing_key(model_id: Optional[str]) -> Optional[str]:
    """Canonical key used to join ``price_rates`` (never used for display).

    Strips a ``vendor/`` prefix (``kimi-code/k3`` -> ``k3``), a trailing
    ``[1m]``-style context suffix and a ``-YYYYMMDD`` snapshot date, and turns
    dots into dashes so ``kimi-k2.7-code`` and ``kimi-k2-7-code`` agree.
    """
    if not model_id:
        return None
    m = model_id.strip().lower()
    if "/" in m:
        m = m.rsplit("/", 1)[-1]
    m = re.sub(r"\s*[\[(].*?[\])]\s*$", "", m).strip()
    m = m.replace(".", "-")
    m = re.sub(r"-\d{8}$", "", m)
    return m or None


_CLAUDE = re.compile(r"^claude-(opus|sonnet|haiku|fable)-(\d+(?:-\d+)?)")
_CLAUDE_OLD = re.compile(r"^claude-(\d+(?:-\d+)?)-(opus|sonnet|haiku)")
_GPT = re.compile(r"^gpt-(\d+(?:-\d+)?)(?:-([a-z0-9]+))?")
_KIMI = re.compile(r"^(?:kimi-)?k(\d+(?:-\d+)?)")


def family_and_label(model_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (family, label) when the id matches a known naming scheme.

    Anything else returns (None, None): an unmapped model is reported as unknown
    rather than guessed.
    """
    key = pricing_key(model_id)
    if not key:
        return None, None
    m = _CLAUDE.match(key)
    if m:
        return m.group(1).title(), f"{m.group(1).title()} {m.group(2).replace('-', '.')}"
    m = _CLAUDE_OLD.match(key)
    if m:
        return m.group(2).title(), f"{m.group(2).title()} {m.group(1).replace('-', '.')}"
    m = _GPT.match(key)
    if m:
        ver = m.group(1).replace("-", ".")
        tail = f" {m.group(2)}" if m.group(2) else ""
        return "GPT", f"GPT-{ver}{tail}"
    m = _KIMI.match(key)
    if m:
        return "Kimi", f"Kimi K{m.group(1).replace('-', '.')}"
    if key.startswith("kimi-for-coding"):
        return "Kimi", "Kimi for Coding"
    return None, None
