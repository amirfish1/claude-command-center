#!/usr/bin/env python3
"""Report the sidebar performance gates for permanent checks.

list_read gates the list reads a user waits for (boot, preview, window
switch, forced refresh) at 1 s. Background refreshes of the window already
on screen report as list_poll and have their own 3 s gate (owner Q1416,
CCC-691): they were 90% of list_read samples, mostly phone round trips.
That leaves ~16 waited-for reads a day, so list_read is measured over 7 days
with the same budget and minimum; at 24 h it could never reach 50 samples
and stayed pending even while it failed.

archive_load is also measured over 7 days (CCC-729). People boot the
dashboard about 5 times a day, mostly on a phone, so at 24 h its p95 was
simply the slowest boot: one boot whose phone network took 1.3 s to send
the request failed the gate. The budget (2 s) and minimum (5) are unchanged.

Network-bound boots are attributed, not counted (CCC-733). A phone boot
read can wait on the phone before it is sent (iOS waking its network or
VPN after a resume) and in transit; neither is CCC's code. The 2716 ms
miss that kept archive_load failing was held 1291 ms on the phone before
its request was sent (HTTP/2, no new connection), while a conversation open
in the same boot lost ~1.45 s outside the server too. Over a week only 3 of
985 boots were held >= 0.7 s, all iPhone boots during a degraded link. So a
list_read, and an archive_load through its boot's first list_read (joined
by boot_id), counts as network-bound when the phone held it >= NET_MS
before sending (see phone_hold_ms). Those samples are reported per gate;
if they exceed NET_SHARE of a gate's samples (min 3) the gate fails as a
degraded link. Reads without resource timing are counted (conservative).

Uses perf_gate.py's deployment proof and its warmup and host-load filters.
Missing post-deployment samples stay pending; they never become a successful
check.

Unlike perf_gate --steady-state, samples from 5 minutes to 30 minutes after a
dashboard boot count here (CCC-600). `ccc restart` merges requests into
at most one restart per 30 minutes (RESTART_DEBOUNCE_S), and a busy host
always has one pending. That left the 30-minute boot exclusion with ~6
steady minutes per day, so the gate stayed pending at zero samples while
users loaded the sidebar on warming servers. The server's own warmup flag
(first 5 minutes, CCC_PERF_WARMUP_S) still excludes the cold replay itself,
and scripted-browser probes (automated=true) are not user samples.
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
    ('list_poll', 3000, 50),
)
WINDOW_HOURS = 24
# Kinds measured over a longer window (same budget and minimum).
KIND_WINDOW_HOURS = {'list_read': 7 * 24, 'archive_load': 7 * 24}
# Network attribution (CCC-733) for the reads a user waits for.
NET_KINDS = ('list_read', 'archive_load')
NET_MS = 1000
NET_SHARE = 0.25


def phone_hold_ms(list_read_row):
    """How long the phone held a list read before sending it, or None.

    Only counted on HTTP/2 or HTTP/3, where the read shares an open
    connection: the server cannot delay the send there. On HTTP/1.1 a stall
    can be the browser's connection limit, filled by CCC's own slow
    requests, so it stays CCC's. Time after sending (transit, the server's
    accept queue) is never attributed: a slow server must not read as a
    slow phone.
    """
    detail = list_read_row.get('detail') if isinstance(list_read_row, dict) else None
    rt = detail.get('rt') if isinstance(detail, dict) else None
    if not isinstance(rt, dict) or not str(rt.get('proto') or '').startswith(('h2', 'h3')):
        return None
    stall = rt.get('stall_ms')
    return max(0, stall) if isinstance(stall, (int, float)) else None


def _excluded(row):
    """Warmup, fully hidden-tab or saturated-host samples; post-warmup
    boot samples count."""
    return perf_gate._steady_reason(row) in ('warmup', 'automated', 'hidden', 'load')


def _window_hours(kind):
    return KIND_WINDOW_HOURS.get(kind, WINDOW_HOURS)


def check(events, repo, commit):
    now = datetime.now(timezone.utc)
    deployed = perf_gate._Deployed(repo, commit)
    values_by_kind = {kind: [] for kind, _, _ in GATES}
    by_window = {}
    for kind, _, _ in GATES:
        by_window.setdefault(_window_hours(kind), []).append(kind)
    net_bound = {kind: 0 for kind in NET_KINDS}
    for hours, kinds in sorted(by_window.items()):
        wanted = {kind: [] for kind in kinds}
        rows = perf_gate._read_rows_for_kinds(events, now - timedelta(hours=hours), wanted)
        first_list = {}
        if 'archive_load' in wanted:
            for row in sorted((r for r in rows if r.get('kind') == 'list_read' and r.get('boot_id')),
                              key=lambda r: r['_ts']):
                first_list.setdefault(row['boot_id'], row)
        for row in rows:
            kind = row.get('kind')
            if kind not in wanted or _excluded(row):
                continue
            if kind in ('list_read', 'list_poll') and str((row.get('detail') or {}).get('status')) == '304':
                continue
            if not deployed(row):
                continue
            if kind in NET_KINDS:
                source = row if kind == 'list_read' else first_list.get(row.get('boot_id'))
                held = phone_hold_ms(source) if source else None
                if held is not None and held >= NET_MS:
                    net_bound[kind] += 1
                    continue
            values_by_kind[kind].append(float(row.get('ms') or 0))

    gates = []
    for kind, budget, minimum in GATES:
        values = values_by_kind[kind]
        p95 = perf_gate._percentile(values, 95) if values else None
        status = ('pending' if len(values) < minimum
                  else 'pass' if p95 <= budget else 'fail')
        gate = {'kind': kind, 'status': status, 'samples': len(values),
                'minimum': minimum, 'p95_ms': p95, 'budget_ms': budget,
                'window_hours': _window_hours(kind)}
        if kind in NET_KINDS:
            bound = net_bound[kind]
            gate['network_bound'] = bound
            if bound >= 3 and bound > NET_SHARE * (bound + len(values)):
                gate['status'] = 'fail'
                gate['reason'] = (f'{bound}/{bound + len(values)} reads network-bound '
                                  f'(held >= {NET_MS} ms on the client before sending): degraded client link')
        gates.append(gate)
    statuses = {gate['status'] for gate in gates}
    status = 'fail' if 'fail' in statuses else 'pending' if 'pending' in statuses else 'pass'
    return {'status': status, 'real_data': any(g['samples'] for g in gates),
            'evidence': {'commit': commit, 'window_hours': WINDOW_HOURS,
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
