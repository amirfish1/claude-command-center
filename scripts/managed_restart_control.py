#!/usr/bin/env python3
"""Launch one explicitly root-accepted transition from a JSON stdin packet.

Does not provision anchors, accept proofs, retry writes or manage a service.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ccc_server.execution_restart import launch, MAX_PACKET


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_PACKET + 1)
        if not raw or len(raw) > MAX_PACKET:
            raise ValueError('managed request exceeds packet bound')
        result = launch(json.loads(raw))
    except Exception as exc:
        print(json.dumps({'ok': False, 'outcome': 'unknown', 'fallback': 'queue',
                          'error': 'Reconcile before retry: ' + str(exc)[:200]}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
