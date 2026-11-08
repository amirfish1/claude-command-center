"""WatchTower auto-decisions for the Decisions view (CCC-371).

WatchTower records ``auto_decision`` / ``auto_decision_declined`` /
``auto_decision_reversed`` / ``contradiction`` events in a ticket's history.
This module only reads them from the ticket list the server already holds and,
on request, runs ``wt undo <ref>``; WatchTower does the revert and verifies it.
"""
import re
import subprocess

REF = re.compile(r'^[A-Z][A-Z0-9_]*-\d+$')


def _decided(item, event, reversal):
    return {
        'ref': item.get('ref', ''), 'title': item.get('title', ''),
        'decision_id': event.get('decision_id', ''), 'at': event.get('at', ''),
        'text': event.get('text', ''), 'class': event.get('class', ''),
        'commit': event.get('commit', ''),
        'reversed': bool(reversal), 'reversed_at': (reversal or {}).get('at', ''),
        'revertable': (not reversal and event.get('class') == 'code-change-revertable'
                       and bool(event.get('commit'))),
    }


def auto_decision_rows(items, limit=200):
    """Newest first. Reversals and contradictions are flagged so a view or
    digest can list them before ordinary decisions."""
    decided, flags = [], []
    for item in items or []:
        hist = item.get('history') or []
        for h in hist:
            kind = h.get('event')
            if kind == 'auto_decision':
                rev = next((r for r in hist if r.get('event') == 'auto_decision_reversed'
                            and r.get('decision_id') == h.get('decision_id')), None)
                decided.append(_decided(item, h, rev))
            elif kind == 'contradiction':
                flags.append({'ref': item.get('ref', ''), 'title': item.get('title', ''),
                              'at': h.get('at', ''), 'text': h.get('text', '')})
    decided.sort(key=lambda r: r['at'], reverse=True)
    flags.sort(key=lambda r: r['at'], reverse=True)
    # Exception-first: reversed decisions, then everything else.
    decided.sort(key=lambda r: not r['reversed'])
    return {'ok': True, 'decisions': decided[:limit], 'contradictions': flags[:limit]}


def revert(ref, worker='ccc-dashboard', runner=subprocess.run):
    """Run ``wt undo <ref>``. Returns (payload, http_status)."""
    if not isinstance(ref, str) or not REF.match(ref):
        return {'ok': False, 'error': 'invalid_ref'}, 400
    try:
        done = runner(['wt', 'undo', ref, '--worker', worker], capture_output=True,
                      text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'ok': False, 'error': f'wt undo failed to run: {exc}'}, 502
    out = (done.stdout or '').strip()
    if done.returncode != 0:
        return {'ok': False, 'error': (done.stderr or out or 'wt undo failed').strip()}, 409
    return {'ok': True, 'ref': ref, 'message': out}, 200
