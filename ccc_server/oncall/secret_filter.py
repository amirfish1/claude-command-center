# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Mask likely secrets before material leaves On-call's local store."""

import re

MASK = '[secret redacted]'
_PATTERNS = tuple(re.compile(p) for p in (
    r'\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}\b',
    r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b',
    r'\bgithub_pat_[A-Za-z0-9_]{20,}\b',
    r'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b',
    r'\bxox[baprs]-[A-Za-z0-9-]{20,}\b',
    r'\bAKIA[A-Z0-9]{16}\b',
    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
))


def mask(*values):
    """Return masked strings and a hit flag. Fail closed if the Vault cannot be read."""
    from ccc_server import vault
    return _with_secrets(vault.vault_get_value(entry['name'])
                         for entry in vault.vault_list_entries())(*values)


def masker():
    """Read secrets once for one operation; never retain them across requests."""
    from ccc_server import vault
    return _with_secrets(vault.vault_get_values().values())


def _with_secrets(values):
    secrets = []
    for value in values:
        if value is None:
            raise ValueError('Cannot check outgoing content against the Vault; sending is paused.')
        if value:
            secrets.append(value)
    secrets.sort(key=len, reverse=True)
    return lambda *values: _mask(values, secrets)


def _mask(values, secrets):
    results, hit = [], False
    for value in values:
        text = str(value or '')
        for secret in secrets:
            if secret in text:
                text = text.replace(secret, MASK)
                hit = True
        for pattern in _PATTERNS:
            text, count = pattern.subn(MASK, text)
            hit |= bool(count)
        results.append(text)
    return (*results, hit)
