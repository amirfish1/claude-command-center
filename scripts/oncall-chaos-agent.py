"""Paid isolated automatic-worker fault stage for oncall-chaos-proof.py."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

REPO = Path(__file__).resolve().parents[1]


def load_proof():
    spec = importlib.util.spec_from_file_location('protocol_proof', REPO / 'scripts/oncall-proof.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def kill_tree(pid):
    # Snapshot descendants before killing the root; app-server children may have
    # separate process groups. Every selected PID must carry our exact HOME.
    selected = {pid}
    children = {}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            stat = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            children[int(proc.name)] = int(stat[1])
        except (OSError, ValueError):
            pass
    while True:
        more = {p for p, parent in children.items() if parent in selected}
        if more <= selected:
            break
        selected |= more
    for target in sorted(selected, reverse=True):
        try:
            env = Path(f'/proc/{target}/environ').read_bytes().split(b'\0')
            assert ('HOME=' + str(Path.home())).encode() in env
            os.kill(target, signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError):
            pass


def run(root, row):
    from ccc_server.oncall import engine, wt
    proof = load_proof()
    root = Path(root)
    for fault in ('worker', 'watcher'):
        queue = 'KILLWORKER' if fault == 'worker' else 'KILLWATCHER'
        project = root / fault
        project.mkdir()
        os.environ['PROOF_GIT'] = __import__('shutil').which('git')
        baseline = proof.seed_repo(project)
        gate = project / 'gate.py'
        gate.write_text('''from pathlib import Path
import time
p=Path('fix-started')
if not p.exists():
    p.write_text('started')
    deadline=time.monotonic()+180
    while not Path('release').exists() and time.monotonic()<deadline:
        time.sleep(.1)
''')
        with (project / 'AGENTS.md').open('a') as f:
            f.write('Before editing sum.py, run python3 gate.py. This local fault-test gate is required.\n'
                    'Do not commit gate.py, fix-started or release.\n')
        proof.run(os.environ['PROOF_GIT'], '-C', str(project), 'add', 'AGENTS.md')
        proof.run(os.environ['PROOF_GIT'], '-C', str(project), 'commit', '--only', 'AGENTS.md', '-m', 'test: require fault gate')
        adapter = wt.WT()
        watcher = proof.start_watcher(root, adapter.argv())
        eng = engine.Engine(root / (fault + '-oncall'), wt=adapter, flag_on=lambda: True)
        cfg = eng.config()
        cfg.update(enabled=True, queue=queue, repo=str(project), channels=[{
            'id': 'local', 'type': 'command', 'owner': 'owner@example.test',
            'send': f'{sys.executable} {root / "sink.py"}'}])
        eng.save_config(cfg)
        adapter.configure_queue(queue, str(project), 1)
        adapter._ok('config', '-q', queue, '--engine', 'codex', '--model', 'gpt-6.1-terra', '--effort', 'medium')
        eng.tick(force=True)
        started = time.monotonic()
        try:
            ref = eng.report('Fix add(2, 3), which returns -1 instead of 5. Follow AGENTS.md, including gate.py.')
            deadline = time.monotonic() + 240
            record = None
            while time.monotonic() < deadline:
                worker_file = root / '.watchtower/workers.json'
                if worker_file.exists():
                    records = json.loads(worker_file.read_text()).get('workers', [])
                    record = next((w for w in reversed(records) if w.get('queue') == queue), None)
                if (project / 'fix-started').exists() and record:
                    break
                if watcher.poll() is not None:
                    raise RuntimeError('watcher exited before fault injection')
                threading.Event().wait(.5)
            else:
                raise TimeoutError(f'{fault}: fixing worker did not reach the gate')
            row(f'{fault} fault start', 'PASS', f'{time.monotonic()-started:.2f}s; model={record.get("model")}; pid={record.get("pid")}')
            if fault == 'worker':
                kill_tree(int(record['pid']))
            else:
                proof.stop_watcher(watcher)
                watcher = proof.start_watcher(root, adapter.argv())
            (project / 'release').touch()
            deadline = time.monotonic() + 780
            item = {}
            while time.monotonic() < deadline:
                item = adapter.find(ref) or {}
                eng.tick()
                stalled = [a for a in eng.store.alerts(state='open')
                           if a['kind'] == 'stuck' and a['ref'] == queue and a['sent_at']]
                if stalled:
                    break
                if item.get('status') == 'closed' or item.get('needs_input'):
                    break
                threading.Event().wait(3)
            if item.get('status') == 'closed':
                proof.run(sys.executable, '-B', '-m', 'unittest', '-v', cwd=project)
                commit = proof.run(os.environ['PROOF_GIT'], '-C', str(project), 'rev-parse', 'HEAD')
                assert commit != baseline
                proof.run(os.environ['PROOF_GIT'], '-C', str(project), 'diff', '--exit-code', baseline, '--', 'test_sum.py')
                row(f'{fault} killed mid-fix', 'PASS', f'WatchTower recovered to closed; tests pass; {time.monotonic()-started:.2f}s')
            elif item.get('needs_input'):
                eng.tick(force=True)
                eng.tick(force=True)
                waiting = [a for a in eng.store.alerts(state='open') if a['ref'] == ref]
                assert len(waiting) == 1
                row(f'{fault} killed mid-fix', 'PASS', 'WatchTower parked; On-call has one owner decision alert')
            elif stalled:
                eng.tick(force=True)
                alert_id = stalled[0]['id']
                assert len(eng.store._all("SELECT id FROM deliveries WHERE alert_id=? AND ok=1", (alert_id,))) == 1
                row(f'{fault} killed mid-fix', 'BOUNDARY',
                    f'WatchTower still {item.get("status")}; On-call delivered one stuck-queue warning after '
                    f'{time.monotonic()-started:.2f}s; upstream WT-64 tracks recovery')
            else:
                row(f'{fault} killed mid-fix', 'FAIL', f'no recovery or owner warning after 780s; status={item.get("status")}; ref={ref}')
                raise RuntimeError(f'WatchTower did not recover {ref} or expose a stuck queue')
        finally:
            adapter._ok('config', '-q', queue, '--auto-drain', 'off')
            proof.stop_watcher(watcher)
            eng.store.close()
