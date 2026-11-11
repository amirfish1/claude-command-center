# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Consume a pinned mailbox server's Authentication-Results, not sender claims.

The receiving server must strip forged headers bearing its own authserv-id.
We accept only the first Authentication-Results field and strict domain
alignment. We do not perform DNS authentication or trust ARC assertions.
"""
import re
import email.utils
import shlex

_DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z", re.I)


def domain(value):
    value = str(value or '').strip().lower().rstrip('.')
    if '@' in value:
        value = value.rsplit('@', 1)[1]
    if (not _DOMAIN.fullmatch(value) or any(not label or len(label) > 63
            or label.startswith('-') or label.endswith('-') for label in value.split('.'))):
        return ''
    return value


def _parts(value):
    """Split outside comments and quoted strings; fail closed on malformed input."""
    if len(value) > 8192:
        return []
    out, buf, depth, quote, escaped = [], [], 0, False, False
    for ch in value:
        if escaped:
            if not depth:
                buf.extend(('\\', ch))
            escaped = False
            continue
        if ch == '\\' and (depth or quote):
            escaped = True
            continue
        if not quote and ch == '(':
            depth += 1
            if depth > 8:
                return []
            continue
        if depth:
            if ch == ')':
                depth -= 1
                if not depth:
                    buf.append(' ')
            continue
        if ch == '"':
            quote = not quote
        if ch == ';' and not quote:
            out.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if depth or quote or escaped:
        return []
    out.append(''.join(buf).strip())
    return out


def inspect(message, sender, pinned=''):
    """Return minimal evidence; never store the full authentication header."""
    fields = message.get_all('Authentication-Results', [])
    result = {'authserv_id': '', 'aligned': False, 'verified': False}
    if not fields or len(message.get_all('From', [])) != 1:
        return result
    addresses = email.utils.getaddresses([str(message.get('From', ''))])
    if len(addresses) != 1 or addresses[0][1].lower() != sender.lower():
        return result
    parts = _parts(str(fields[0]))
    if not parts:
        return result
    service = parts[0].split()
    if not service or len(service) > 2 or (len(service) == 2 and not service[1].isdigit()):
        return result
    authserv = domain(service[0])
    # authserv-id is a server identity, not an address.
    if not authserv or '@' in service[0]:
        return result
    result['authserv_id'] = authserv
    from_domain = domain(sender)
    if not from_domain or '@' not in sender:
        return result
    for part in parts[1:]:
        match = re.match(r'^(dmarc|dkim|spf)(?:/\d+)?\s*=\s*pass\b(.*)$', part, re.I | re.S)
        if not match:
            continue
        try:
            words = shlex.split(match[2])
        except ValueError:
            continue
        props = {}
        i = 0
        while i < len(words):
            word = words[i]
            if '=' not in word and i + 2 < len(words) and words[i + 1] == '=':
                word = word + '=' + words[i + 2]
                i += 2
            key, sep, value = word.partition('=')
            if sep and key.lower() in ('header.from', 'header.d', 'smtp.mailfrom'):
                props.setdefault(key.lower(), []).append(value)
            i += 1
        key = {'dmarc': 'header.from', 'dkim': 'header.d', 'spf': 'smtp.mailfrom'}[match[1].lower()]
        values = props.get(key, [])
        if len(values) == 1 and domain(values[0]) == from_domain:
            result['aligned'] = True
    result['verified'] = bool(result['aligned'] and authserv == domain(pinned))
    return result
