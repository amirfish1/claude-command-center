#!/usr/bin/env python3
"""Kill intake after real isolated WatchTower filing, then replay and dedupe."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def exercise(root, crash=False):
    from ccc_server.oncall import customers, store, wt
    from ccc_server.oncall.sources import Finding
    from ccc_server.oncall.ci_source import CISource
    database = store.Store(root / 'oncall')
    tracker = wt.WT(argv=[shutil.which('wt')], dispatch=False)
    source = CISource({'id': 'ci', 'repo': 'example/demo'}, kv=database)
    finding = Finding('ci:crash-proof', 'CI failed: fixture', 'Local crash boundary fixture.')
    source._set('outbox', [finding.__dict__]) if crash else None
    config = {'queue': 'PROOF'}
    if crash:
        tracker._ok('config', '-q', 'PROOF', '--auto-drain', 'off')
        update = database.report_update
        def crash_after_filing(*args, **kwargs):
            if kwargs.get('state') == 'filed':
                os._exit(73)  # wt add has returned, report reference is not saved
            return update(*args, **kwargs)
        database.report_update = crash_after_filing
        customers.file_finding(config, database, tracker, {'id': 'ci'}, finding)
        raise AssertionError('Crash point was not reached')
    # No network is needed: the outbox must replay before a source API request.
    source._credential = lambda: ''
    replay = source.poll()
    assert len(replay) == 1 and replay[0].fingerprint == finding.fingerprint
    customers.retry_unfiled(config, database, tracker)
    ref = customers.file_finding(config, database, tracker, {'id': 'ci'}, replay[0])
    assert ref
    source.acknowledge(finding.fingerprint)
    tickets = tracker.ls('PROOF', 'active')
    assert len(tickets) == 1 and tickets[0]['ref'] == ref
    assert len(database.reports()) == 1 and database.reports()[0]['ticket_ref'] == ref
    assert source._get('outbox') == []
    print('PASS real WatchTower crash after add, restart, one ticket, recorded reference, acknowledged finding')


def main():
    if not shutil.which('wt'):
        raise RuntimeError('WatchTower is required')
    with tempfile.TemporaryDirectory(prefix='ccc-ci-crash-') as temp:
        root = Path(temp)
        env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL') if key in os.environ}
        env.update(HOME=temp, XDG_CONFIG_HOME=temp, XDG_DATA_HOME=temp,
                   CCC_STATE_DIR=str(root / 'state'), WATCHTOWER_STORE=str(root / 'queues.json'),
                   WT_NO_AUTOSTART='1', WT_NO_SELF_UPDATE='1', WATCHTOWER_DELEGATE_URL='off')
        first = subprocess.run([sys.executable, __file__, '--child', temp, 'crash'], env=env, timeout=60)
        assert first.returncode == 73, first.returncode
        subprocess.run([sys.executable, __file__, '--child', temp, 'recover'], env=env, timeout=60, check=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        exercise(Path(sys.argv[2]), sys.argv[3] == 'crash')
    else:
        main()
