#!/usr/bin/env python3
"""Run paid, isolated On-call cases. Only model traffic leaves the machine."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import statistics
import signal
import threading
import subprocess
import sys
import time
from cases import CASES, QUICK, expected_messages


def capture_provenance():
    def git(directory, *args):
        return subprocess.check_output(
            ['git', '-C', str(directory), *args], text=True,
            stderr=subprocess.DEVNULL, timeout=10,
            env={**os.environ, 'GIT_OPTIONAL_LOCKS':'0'},
        ).strip()

    repo = Path(__file__).resolve().parents[2]
    repository_head = git(repo, 'rev-parse', 'HEAD')
    tracked_dirty = bool(git(repo, 'status', '--porcelain', '--untracked-files=no'))
    watchtower_head = None
    try:
        spec = importlib.util.find_spec('watchtower')
        if spec is not None and spec.origin:
            watchtower_head = git(Path(spec.origin).resolve().parent, 'rev-parse', 'HEAD')
    except (ImportError, ValueError, OSError, subprocess.SubprocessError):
        pass
    return {'repository_head':repository_head, 'repository_tracked_dirty':tracked_dirty,
            'watchtower_head':watchtower_head, 'python_version':platform.python_version()}


def summarize(rows):
    return {
        'passed':sum(r['pass'] for r in rows),
        'fixed':sum(r['pass'] and CASES[r['case']].kind!='wrong' for r in rows),
        'wrongly_fixed':sum(not r['pass'] and r.get('fix_s') is not None for r in rows),
        'stalled':sum(not r['pass'] and r.get('fix_s') is None for r in rows),
        'needless_owner_messages':sum(max(r.get('needless_owner_decisions',0),r.get('owner_messages',0)-expected_messages(CASES[r['case']])) for r in rows),
        'missed_owner_messages':sum(max(0,expected_messages(CASES[r['case']])-r.get('owner_messages',0)) for r in rows),
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--with-agent', action='store_true', help='Authorize paid Codex fixing agents')
    p.add_argument('--quick', action='store_true')
    p.add_argument('--case', choices=CASES, action='append')
    p.add_argument('--runs', type=int, help='Runs per case (default: 1 with --quick, otherwise 2)')
    p.add_argument('--jobs', type=int, default=2)
    p.add_argument('--timeout', type=int, default=480)
    p.add_argument('--output', type=Path, required=True, help='Private output directory for results and worker logs')
    a=p.parse_args()
    if a.runs is None: a.runs=1 if a.quick else 2
    if not a.with_agent: p.error('--with-agent is required')
    if not 1<=a.jobs<=4 or not 1<=a.runs<=10 or not 30<=a.timeout<=900: p.error('jobs 1..4, runs 1..10, timeout 30..900')
    if a.output.exists() and (
        not a.output.is_dir() or
        any(entry.name != 'table.log' or not entry.is_file() for entry in a.output.iterdir())
    ):
        p.error('output contains prior results or other files; choose a fresh output directory (only table.log is allowed)')
    provenance=capture_provenance()
    a.output.mkdir(parents=True,exist_ok=True)
    ids=a.case or (QUICK if a.quick else list(CASES))
    rows=[]
    children=set()
    children_lock=threading.Lock()
    stop=threading.Event()
    def interrupted(signum, frame):
        stop.set()
        with children_lock:
            for child in children:
                if child.poll() is None: child.terminate()
        raise RuntimeError(f'Interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    start=time.monotonic()
    def execute(n, case):
        cancelled={'case':case,'pass':False,'reason':'Cancelled before worker start',
                   'worker_start_s':None,'fix_s':None,'owner_messages':0,'run':n}
        if stop.is_set(): return cancelled
        target=(a.output/f'{n}-{case}.json').resolve()
        with target.with_suffix('.transport.log').open('w') as log:
            with children_lock:
                if stop.is_set(): return cancelled
                proc=subprocess.Popen([sys.executable,str(Path(__file__).with_name('worker.py')),
                    '--case',case,'--result',str(target),'--timeout',str(a.timeout)],stdout=log,stderr=log, start_new_session=True)
                children.add(proc)
            try:
                proc.wait(timeout=a.timeout+360)
            except subprocess.TimeoutExpired:
                proc.terminate()  # worker's finally removes containers and HOME-marked children
                proc.wait(timeout=90)
            finally:
                with children_lock: children.discard(proc)
        if target.exists(): row=json.loads(target.read_text())
        else: row={'case':case,'pass':False,'reason':f'Transport failed ({proc.returncode}); see transport log',
                   'worker_start_s':None,'fix_s':None,'owner_messages':0}
        row['run']=n
        return row
    # Runs are sequential; cases within each run have bounded concurrency.
    for n in range(1,a.runs+1):
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            futures=[pool.submit(execute,n,c) for c in ids]
            for f in as_completed(futures):
                row=f.result(); rows.append(row)
                print(f"{n} | {row['case']} | {'PASS' if row['pass'] else 'FAIL'} | {row['worker_start_s']} | {row['fix_s']} | {row['owner_messages']} | {row['reason']}",flush=True)
                (a.output/'results.json').write_text(json.dumps({'rows':rows,'complete':False},indent=2)+'\n')
    totals=[summarize([r for r in rows if r['run']==n]) for n in range(1,a.runs+1)]
    variance={key:statistics.pvariance([t[key] for t in totals]) for key in totals[0]}
    timing_variance = {}
    for case in ids:
        values = [r['fix_s'] for r in rows if r['case']==case and r.get('fix_s') is not None]
        timing_variance[case] = {'completed':len(values), 'mean_s':statistics.mean(values) if values else None,
            'population_variance_s2':statistics.pvariance(values) if len(values)>1 else None}
    output={'date':datetime.now(timezone.utc).isoformat(),'model':'gpt-6.1-terra','effort':'medium',
            'complete':True,'provenance':provenance,'duration_s':round(time.monotonic()-start,2),'rows':rows,'totals':totals,'variance':variance,'timing_variance':timing_variance}
    (a.output/'results.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps({'totals':totals,'variance':variance}),flush=True)
    return int(any(not r['pass'] for r in rows))


if __name__=='__main__': sys.exit(main())
