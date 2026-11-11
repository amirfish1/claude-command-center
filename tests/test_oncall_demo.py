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
    proc = demo(tmp_path, '--terminal', '--keep', input=answer + '\nno\n')
    assert proc.returncode == 0, proc.stderr + proc.stdout
    root = Path(proc.stdout.split('Kept workspace: ')[1].strip())
    try:
        assert f'else {expected}' in (root / 'project/cart.py').read_text()
        assert 'Not sent.' in proc.stdout
        ticket = json.loads((root / 'tickets.json').read_text())
        assert ticket['status'] == 'closed' and ticket['answer'] == answer
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
            assert json.load(response) == lines
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
