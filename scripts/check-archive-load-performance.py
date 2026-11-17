#!/usr/bin/env python3
"""Retain startup and steady archive paint budgets on deployed real telemetry."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import perf_gate


def check(events, repo, commit):
    deployed = perf_gate._Deployed(repo, commit)
    cohorts = {'startup': [], 'steady': []}
    rows = perf_gate._read_rows(events, datetime.now(timezone.utc) - timedelta(hours=24),
                                'archive_load')
    for row in rows:
        if row.get('kind') != 'archive_load' or not deployed(row):
            continue
        reason = perf_gate._steady_reason(row)
        # Same calm-host/warmup rules as the filing. Startup is retained as
        # its own cohort, with the cold budget even for persisted warm caches.
        if reason not in ('', 'boot') or row.get('up_s') is None:
            continue
        cohort = 'startup' if reason == 'boot' else 'steady'
        budget = 5000 if cohort == 'startup' or not row.get('warm') else 1000
        cohorts[cohort].append((float(row.get('ms') or 0), budget))
    gates = []
    for cohort, values in cohorts.items():
        ratio = perf_gate._percentile([ms / budget for ms, budget in values], 95) if values else None
        status = 'pending' if len(values) < 5 else 'pass' if ratio <= 1 else 'fail'
        gates.append({'cohort': cohort, 'status': status, 'samples': len(values),
                      'minimum': 5, 'p95_budget_ratio': ratio,
                      'p95_ms': perf_gate._percentile([ms for ms, _ in values], 95) if values else None})
    states = {gate['status'] for gate in gates}
    status = 'fail' if 'fail' in states else 'pending' if 'pending' in states else 'pass'
    return {'status': status, 'real_data': any(cohorts.values()),
            'evidence': {'commit': commit, 'events': str(events), 'window_hours': 24,
                         'startup_budget_ms': 5000, 'warm_budget_ms': 1000,
                         'cold_budget_ms': 5000, 'gates': gates}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--repo', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--events', default=str(perf_gate.DEFAULT_EVENTS))
    args = parser.parse_args()
    print(json.dumps(check(args.events, args.repo, args.commit)))


if __name__ == '__main__':
    main()
