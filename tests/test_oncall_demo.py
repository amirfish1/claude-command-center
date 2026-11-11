"""Run the no-account walkthrough through production On-call routing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from ccc_server.oncall.demo_phone import Phone

REPO = Path(__file__).resolve().parents[1]


def stored_tickets(root):
    if (root / 'queues.json').exists():
        return json.loads((root / 'queues.json').read_text())['items']
    import sqlite3
    with sqlite3.connect(root / 'queues.db') as conn:
        return [json.loads(row[0]) for row in conn.execute('SELECT item_json FROM items ORDER BY number')]


def demo(tmp_path, *args, input=None):
    env = dict(os.environ, HOME=str(tmp_path), CCC_STATE_DIR=str(tmp_path / 'real-state'),
               CCC_FF_ONCALL='0', NO_COLOR='1', TMPDIR=str(tmp_path))
    return subprocess.run([sys.executable, str(REPO / 'ccc'), 'oncall', 'demo', *args],
                          cwd=tmp_path, env=env, input=input, text=True,
                          capture_output=True, timeout=15)


def test_scripted_full_loop_and_perf(tmp_path):
    start = time.monotonic()
    proc = demo(tmp_path, '--json')
    elapsed = time.monotonic() - start
    assert proc.returncode == 0, proc.stderr + proc.stdout
    result = json.loads(proc.stdout)
    assert result['ok'] and result['test_before'] == 1 and result['test_after'] == 0
    assert result['reporter_state'] == 'sent'
    assert any('needs a decision' in msg['text'] for msg in result['messages'])
    assert any(msg['to'] == 'reporter@example.test' for msg in result['messages'])
    assert elapsed < 2, f'Full demo took {elapsed:.3f}s; startup budget is 2s'
    assert not (tmp_path / 'real-state').exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('answer,expected', [('zero', '0'), ('hide', 'None')])
def test_terminal_choice_and_no_send(tmp_path, answer, expected):
    proc = demo(tmp_path, '--terminal', '--keep', input=answer + '\ndecline\nno\n')
    assert proc.returncode == 0, proc.stderr + proc.stdout
    root = Path(proc.stdout.split('Kept workspace: ')[1].strip())
    try:
        assert f'else {expected}' in (root / 'project/cart.py').read_text()
        assert 'Not sent.' in proc.stdout
        if (root / 'tickets.json').exists():
            ticket = json.loads((root / 'tickets.json').read_text())
        else:
            ticket = stored_tickets(root)[0]
        assert ticket['status'] == 'closed'
        assert ticket['resolution']['commit']
        assert (root / 'project/.gitignore').read_text() == '__pycache__/\n'
    finally:
        import shutil
        shutil.rmtree(root)


def test_eof_is_not_approval(tmp_path):
    proc = demo(tmp_path, '--terminal', input='')
    assert proc.returncode == 1
    assert 'Input ended' in proc.stdout
    assert 'Fix committed' not in proc.stdout


def test_record(tmp_path):
    transcript = tmp_path / 'record.txt'
    proc = demo(tmp_path, '--json', '--record', str(transcript))
    assert proc.returncode == 0, proc.stdout
    text = transcript.read_text()
    assert 'needs a decision' in text and 'Reporter notice is waiting' in text
    assert 'scripted worker' in text and 'ccc oncall setup' in text


def test_phone_boundary_and_reply():
    lines = ['a real channel message']
    phone = Phone(lines.append)
    phone.start(lines)
    try:
        with urllib.request.urlopen(phone.base + '/') as response:
            assert b'Your reply' in response.read()
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(phone.base + '/messages')
        assert error.value.code == 403
        headers = {'X-Demo-Key': phone.key, 'Origin': phone.base}
        request = urllib.request.Request(phone.base + '/messages', headers=headers)
        with urllib.request.urlopen(request) as response:
            payload = json.load(response)
            assert payload['state'] == 'Reported' and payload['messages'] == []
        for origin, status in [('https://example.test', 403), (phone.base, 200)]:
            request = urllib.request.Request(phone.base + '/reply', data=b'{"text":"hide"}',
                                             headers={**headers, 'Origin': origin})
            try:
                with urllib.request.urlopen(request) as response:
                    assert response.status == status
            except urllib.error.HTTPError as exc:
                assert exc.code == status
        assert phone.replies.get_nowait() == 'hide'
    finally:
        phone.close()


@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_portable_loop_without_proc(tmp_path, monkeypatch, platform):
    import argparse
    import ccc_server.oncall.demo as module
    monkeypatch.setattr(module, 'installed', lambda: False)
    from ccc_server.oncall.demo import walkthrough
    monkeypatch.setattr(sys, 'platform', platform)
    original_read = Path.read_bytes

    def no_proc(path):
        assert not str(path).startswith('/proc/')
        return original_read(path)

    monkeypatch.setattr(Path, 'read_bytes', no_proc)
    lines = []
    result = walkthrough(tmp_path, argparse.Namespace(json=True, terminal=True, agent=False),
                         lines.append, lines)
    assert result['ok'] and result['reporter_state'] == 'sent'
    assert any('needs a decision' in line for line in lines)


def test_real_watchtower_policy(tmp_path):
    from ccc_server.oncall.demo_wt import installed
    if not installed():
        pytest.skip('WatchTower is not installed')
    proc = demo(tmp_path, '--json', '--keep')
    result = json.loads(proc.stdout)
    assert result['ok'], result
    root = Path(result['workspace'])
    try:
        assert result['backend'] == 'watchtower'
        items = stored_tickets(root)
        report = next(i for i in items if i['ref'] == result['ticket'])
        assert report['status'] == 'closed'
        assert any(h['event'] == 'answer' for h in report['history'])
        minor = next(i for i in items if i['ref'] == result['decision']['ref'])
        assert any(h['event'] == 'auto_decision' for h in minor['history'])
        assert result['decision']['undo'].startswith('git revert ')
        undo = result['decision']['undo'].split()[-1]
        subprocess.run(['git', '-C', str(root / 'project'), '-c', 'core.hooksPath=/dev/null',
                        'revert', '--no-commit', undo], check=True, capture_output=True,
                       env=dict(PATH=os.defpath, HOME=str(root), GIT_CONFIG_NOSYSTEM='1',
                                GIT_CONFIG_GLOBAL=os.devnull))
        assert not (root / 'project/.gitignore').exists()
        assert not (tmp_path / 'real-state').exists()
    finally:
        import shutil
        shutil.rmtree(root)


def test_same_second_fractional_block_is_delivered(tmp_path):
    from ccc_server.oncall import alerts
    from ccc_server.oncall.store import Store, default_config
    class Queue:
        def blocked(self, queue):
            return [dict(ref='DEMO-1', project='DEMO', needs_input=True,
                         blocked_at='2026-11-11T01:00:00.123456Z',
                         block_question='zero or hide?', status='in_progress')]
    store = Store(tmp_path)
    try:
        cfg = default_config()
        cfg['queue'] = 'DEMO'
        alerts.collect_decisions(cfg, store, Queue(), silent_before='2026-11-11T01:00:00Z')
        assert not store.alerts()[0]['sent_at']
    finally:
        store.close()


def test_fast_terminal_phone_box(tmp_path):
    proc = demo(tmp_path, '--terminal', '--fast', input='hide\ndecline\nno\n')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '| PHONE' in proc.stdout and 'Agent: Worker starts' in proc.stdout
    assert '\x1b[' not in proc.stdout


def test_phone_choices_preserve_message():
    from ccc_server.oncall.store import Outbound
    lines = []
    phone = Phone(lines.append)
    text = 'An empty cart crashes. Reply zero or hide.'
    phone.send(Outbound(text=text))
    assert phone.choices == ['zero', 'hide']
    assert phone.thread_messages[-1]['text'] == text
    phone.receive('zero')
    assert phone.choices == []
    assert phone.thread_messages[-1] == {'sender': 'You', 'text': 'zero'}


def test_watchtower_cli_interpreter_discovery(monkeypatch):
    import shutil
    from ccc_server.oncall import demo_wt
    if not shutil.which('wt'):
        pytest.skip('wt CLI is not installed')
    monkeypatch.setattr(demo_wt.importlib.util, 'find_spec', lambda name: None)
    assert demo_wt.interpreter()
