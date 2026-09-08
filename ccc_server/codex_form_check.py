"""Short-lived, stdlib-only validator for an untrusted MCP form schema."""
import json
import sys

from ccc_server.codex_capabilities import validate_schema


def main():
    try:
        raw = sys.stdin.buffer.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            return 1
        request = json.loads(raw)
        validate_schema(request["value"], request["schema"])
        return 0
    except (ValueError, TypeError, KeyError, RecursionError):
        # Never echo input or provider schema content into process diagnostics.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
