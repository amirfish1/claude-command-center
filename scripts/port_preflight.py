#!/usr/bin/env python3
"""Check a dashboard port before CCC starts anything on it.

    port_preflight.py PORT [--host H]         -> prints free | ccc | busy
    port_preflight.py PORT [--host H] --pick  -> prints PORT when it is free,
                                                 else the next free port

"free" means nothing listens on the port on loopback, on any IPv4 address,
or on --host. --pick never reuses a port another CCC holds: that may serve
a different repo, and a same-repo duplicate is refused by the server itself.

Used by install.sh and run.sh. A clean machine with another program on 8090
used to get a bind traceback after the worker had already started; this
check runs first so the user sees one clear line instead.
"""
import json
import socket
import sys
import urllib.request


def _bindable(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Same option the dashboard server sets, so TIME_WAIT is not "busy".
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def state(port: int, host: str = "") -> str:
    # Loopback and the wildcard both, because macOS lets a wildcard bind
    # succeed beside a loopback listener; plus the configured bind host.
    hosts = ["127.0.0.1", "0.0.0.0"] + ([host] if host and host not in ("localhost", "127.0.0.1", "0.0.0.0") else [])
    if all(_bindable(h, port) for h in hosts):
        return "free"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2) as resp:
            data = json.loads(resp.read(4096) or b"{}")
        if isinstance(data, dict) and data.get("version") and data.get("code_rev") is not None:
            return "ccc"
    except Exception:
        pass
    return "busy"


def main(argv) -> int:
    args = argv[1:]
    if not args or not args[0].isdigit():
        print("usage: port_preflight.py PORT [--host H] [--pick]", file=sys.stderr)
        return 2
    port = int(args[0])
    host = args[args.index("--host") + 1] if "--host" in args[:-1] else ""
    if "--pick" not in args:
        print(state(port, host))
        return 0
    for candidate in range(port, min(port + 50, 65536)):
        if state(candidate, host) == "free":
            print(candidate)
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
