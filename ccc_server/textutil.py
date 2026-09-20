# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Leaf module for text utilities, slugification, and string sanitization.

Stdlib-only. No imports from server or ccc_server.core.
"""

from __future__ import annotations

import re

_LONE_SURROGATE_RE = re.compile("[{}-{}]".format(chr(0xD800), chr(0xDFFF)))


def _strip_lone_surrogates(s):
    """Remove unpaired UTF-16 surrogate code points (U+D800..U+DFFF).

    JS strings are UTF-16; the browser's clipboard / pasted-image / selection
    APIs can leave a lone high or low surrogate in the payload sent to
    /api/annotations (or any other text field). Python stores those as-is
    in `str`, but `json.dumps` happily serialises them, and the Anthropic
    API rejects the resulting request with
        "API Error: 400 The request body is not valid JSON:
         no low surrogate in string: line 1 column N (char N)"
    Strip the unpaired code points at the boundary so downstream injection
    paths can never feed broken UTF-16 into the API. Paired surrogates
    (i.e. real astral-plane chars like emoji) are already collapsed into
    a single Python code point above U+FFFF and do not match this regex.
    """
    if not s:
        return s
    return _LONE_SURROGATE_RE.sub("", s)


def _strip_spawn_payload_surrogates(payload):
    """Drop unpaired UTF-16 surrogates from every string in a spawn payload.

    Browser callers build spawn names/prompts by manipulating UTF-16 JS
    strings; an emoji cut mid-pair arrives as a lone surrogate that
    ``json.loads`` keeps, and the next strict UTF-8 encode downstream
    (control-plane socket write, engine stdin, sqlite store) then raises
    ``UnicodeEncodeError: surrogates not allowed`` — killing the spawn before
    any registry row exists (OPS-935). Strip them at the HTTP boundary so the
    effective spawn request is always encodable.
    """
    def _clean(value):
        if isinstance(value, str):
            return _strip_lone_surrogates(value)
        if isinstance(value, dict):
            return {key: _clean(val) for key, val in value.items()}
        if isinstance(value, list):
            return [_clean(item) for item in value]
        return value
    return _clean(payload)


def _slugify(text, max_len=40):
    """Turn a prompt into a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


# Mirrors watchtower.config._validate_queue_label so a bad label is refused
# before any setter runs (this module cannot import watchtower).
_QUEUE_LABEL_RESERVED = {
    "watchtower:in-progress", "watchtower:no-auto-drain", "watchtower:play",
}


def _validate_queue_label(label):
    if "," in label or "\n" in label or "\r" in label:
        raise ValueError("queue_label cannot contain commas or newlines")
    if len(label) > 50:
        raise ValueError("queue_label must be 50 characters or fewer")
    if label.lower() in _QUEUE_LABEL_RESERVED:
        raise ValueError("queue_label is reserved for a WatchTower control label")


def _validate_auto_compact_k(value, default=250):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(50, min(1000, v))
