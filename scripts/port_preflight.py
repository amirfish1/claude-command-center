#!/usr/bin/env python3
"""Check a dashboard port before CCC starts anything on it.

    port_preflight.py PORT          -> prints free | ccc | busy
    port_preflight.py PORT --pick   -> prints PORT when it is free or already
                                       CCC, else the next free port above it

Used by install.sh and run.sh. A clean machine with another program on 8090
used to get a bind traceback after the worker had already started; this
check runs first so the user sees one clear line instead.
"""
import json
import socket
import sys
import urllib.request


def state(port: int) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Same option the dashboard server sets, so TIME_WAIT is not "busy".
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
        return "free"
    except OSError:
        pass
    finally:
        sock.close()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2) as resp:
            data = json.loads(resp.read(4096) or b"{}")
        if isinstance(data, dict) and data.get("version") and data.get("code_rev") is not None:
            return "ccc"
    except Exception:
        pass
    return "busy"


def main(argv) -> int:
    if len(argv) < 2 or not argv[1].isdigit():
        print("usage: port_preflight.py PORT [--pick]", file=sys.stderr)
        return 2
    port = int(argv[1])
    if "--pick" not in argv[2:]:
        print(state(port))
        return 0
    if state(port) in ("free", "ccc"):
        print(port)
        return 0
    for candidate in range(port + 1, min(port + 50, 65536)):
        if state(candidate) == "free":
            print(candidate)
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
