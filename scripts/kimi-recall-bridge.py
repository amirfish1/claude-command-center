#!/usr/bin/env python3
"""Export Kimi Code sessions to a Total Recall knowledge folder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ccc_server import kimi_recall


def _paths(args):
    output = Path(args.output_dir).expanduser()
    kimi_home = Path(args.kimi_home).expanduser() if args.kimi_home else None
    return output, kimi_home


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "connect", "install-launchd"):
        command = subparsers.add_parser(name)
        command.add_argument("--output-dir", default="~/.ccc/total-recall/kimi-code")
        command.add_argument("--kimi-home")
        command.add_argument("--interval-seconds", type=int, default=300)
        command.add_argument("--destination")
    args = parser.parse_args(argv)
    output_dir, kimi_home = _paths(args)
    if args.command == "install-launchd":
        if kimi_home is None:
            kimi_home = Path.home() / ".kimi-code"
        destination = kimi_recall.install_launchd(
            script_path=Path(__file__).resolve(), output_dir=output_dir, kimi_home=kimi_home,
            destination=args.destination, interval_seconds=args.interval_seconds,
        )
        loaded = kimi_recall.load_launchd(destination)
        print(json.dumps({"launchd_plist": str(destination), "loaded": loaded.returncode == 0,
                          "error": loaded.stderr.strip()}, sort_keys=True))
        return loaded.returncode
    if args.command == "connect":
        result, connected = kimi_recall.connect_kimi_knowledge(output_dir, kimi_home=kimi_home)
        payload = {"exported": result.exported, "skipped": result.skipped, "errors": result.errors}
        if connected is None:
            payload.update({"connected": False, "error": "Kimi export failed; Total Recall was not connected."})
            print(json.dumps(payload, sort_keys=True))
            return 1
        payload.update({"connected": connected.ok, "name": connected.name, "error": connected.error})
        print(json.dumps(payload, sort_keys=True))
        return 0 if connected.ok else 1
    result = kimi_recall.sync_kimi_knowledge(output_dir, kimi_home=kimi_home)
    payload = {"exported": result.exported, "skipped": result.skipped, "errors": result.errors}
    print(json.dumps(payload, sort_keys=True))
    return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
