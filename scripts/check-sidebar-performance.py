#!/usr/bin/env python3
"""Report the three original sidebar performance gates for permanent checks.

Uses the same telemetry filters and deployment proof as perf_gate.py. Missing
post-deployment samples stay pending; they never become a successful check.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import perf_gate


GATES = (
    ('list_read', 1000, 50),
    ('live_build', 1000, 100),
    ('archive_load', 2000, 5),
)


def check(events, repo, commit):
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    deployed = perf_gate._Deployed(repo, commit)
    gates = []
    for kind, budget, minimum in GATES:
        rows = perf_gate._read_rows(events, since, kind)
        rows = [row for row in rows if row.get('kind') == kind
                and (kind != 'list_read'
                     or str((row.get('detail') or {}).get('status')) != '304')
                and deployed(row) and not perf_gate._steady_reason(row)]
        values = [float(row.get('ms') or 0) for row in rows]
        p95 = perf_gate._percentile(values, 95) if values else None
        status = ('pending' if len(rows) < minimum
                  else 'pass' if p95 <= budget else 'fail')
        gates.append({'kind': kind, 'status': status, 'samples': len(rows),
                      'minimum': minimum, 'p95_ms': p95, 'budget_ms': budget})
    statuses = {gate['status'] for gate in gates}
    status = 'fail' if 'fail' in statuses else 'pending' if 'pending' in statuses else 'pass'
    return {'status': status, 'real_data': any(g['samples'] for g in gates),
            'evidence': {'commit': commit, 'window_hours': 24,
                         'events': str(events), 'gates': gates}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--repo', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--events', default=str(perf_gate.DEFAULT_EVENTS))
    args = parser.parse_args()
    print(json.dumps(check(args.events, args.repo, args.commit)))


if __name__ == '__main__':
    main()
