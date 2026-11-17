"""Read the shared frontend for static source-contract assertions.

Runtime behavior uses the actual controller factories in the Node/browser
fixtures. Static assertions can ignore live adapter qualification when checking
an existing algorithm after it moves from the dashboard to a shared controller.
"""
from functools import lru_cache
import re


@lru_cache(maxsize=1)
def conversation_source():
    from tests.dashboard_source import dashboard_source
    return dashboard_source()


def conversation_function(name):
    """Find a declaration without depending on its former neighbor or file."""
    source = conversation_source()
    declaration = re.search(r"(?m)^([ \t]*)(?:async )?function " + re.escape(name) + r"\(", source)
    if not declaration:
        raise ValueError(f"Missing shared function: {name}")
    line_end = source.index("\n", declaration.start())
    if source[line_end - 1] == "}":
        return source[declaration.start():line_end]
    closing = re.search(r"(?m)^" + declaration[1] + r"\}[ \t]*$", source[line_end:])
    if not closing:
        raise ValueError(f"Unclosed shared function: {name}")
    return source[declaration.start():line_end + closing.end()]

def conversation_handler(fragment):
    """Return the shared binding containing a delegated action selector."""
    source = conversation_source()
    at = source.index(fragment)
    declarations = list(re.finditer(r"(?m)^  (?:async )?function (\w+)\s*\(", source[:at]))
    if not declarations:
        raise ValueError("Missing handler containing: " + fragment)
    return conversation_function(declarations[-1][1])

