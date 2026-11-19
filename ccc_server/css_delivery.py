"""Shorten explanatory CSS comments without changing CSS tokens or rules."""

import re


_TOKENS = re.compile(
    rb'''"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|'''
    rb'''/\*[\s\S]*?\*/|\\[\s\S]|[()]''')


def compact_comments(source):
    """Retain empty comment boundaries, strings and function bodies verbatim.

    Removing a comment outright can join tokens. Replacing it with whitespace
    can introduce a descendant selector. An empty comment preserves both.
    Function bodies stay unchanged, including unquoted data URLs whose comment
    lookalikes are literal URL content. License comments remain distributable.
    """
    depth = 0

    def replace(match):
        nonlocal depth
        token = match[0]
        if token == b"(":
            depth += 1
        elif token == b")":
            depth = max(0, depth - 1)
        elif (not depth and token.startswith(b"/*")
              and not token.startswith(b"/*!")
              and b"@license" not in token.lower()
              and b"@preserve" not in token.lower()):
            return b"/**/"
        return token

    return _TOKENS.sub(replace, source)
