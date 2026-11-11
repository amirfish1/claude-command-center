"""Offline film evidence and renderer boundaries, using only temporary state."""

import base64
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from ccc_server.oncall import film
from ccc_server.oncall.store import Receipt, Store, save_config


# A complete one-pixel PNG; the capture substitute writes real image bytes.
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='
)


def transcript_row(when, *blocks):
    return {'timestamp': when, 'message': {'content': list(blocks)}}


def tool(name, ident, **args):
    return {'type': 'tool_use', 'name': name, 'id': ident, 'input': args}


def result(ident, text, **extra):
    return {'type': 'tool_result', 'tool_use_id': ident, 'content': text, **extra}


@pytest.fixture
def retained(tmp_path, monkeypatch):
    root = tmp_path / 'oncall'
    repo = tmp_path / 'repo'
    repo.mkdir()
    store = Store(root)
    save_config(root, {'repo': str(repo), 'queue': 'TEST'})
    monkeypatch.setenv('CCC_ONCALL_STATE_DIR', str(root))
    queues = tmp_path / 'queues.json'
    monkeypatch.setenv('WATCHTOWER_STORE', str(queues))
    transcript = tmp_path / 'session.jsonl'
    rows = [
        transcript_row(110, tool('Read', 'read', file_path=str(repo / 'widget.py'))),
        transcript_row(120, tool('Edit', 'edit', file_path=str(repo / 'widget.py'))),
        transcript_row(130, tool('Bash', 'test', command='python3 -m pytest tests/test_widget.py')),
        transcript_row(140, result('test', '================ 2 passed in 0.02s ================')),
    ]
    transcript.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
    ticket = {'ref': 'TEST-1', 'repo_path': str(repo), 'claimed_at': 100,
              'closed_at': 200, 'resolution': {'transcript_path': str(transcript)}}
    queues.write_text(json.dumps({'items': [ticket]}))
    report, _ = store.report_upsert(
        'test', 'widget', title='Widget broken', text='The widget fails to open.',
        queue='TEST', reporter_name='Example Person',
        reporter_address='person@example.test', now=80,
    )
    store.report_update(report['id'], ticket_ref='TEST-1', state='filed')
    alert, _ = store.alert_upsert(
        'fixed:TEST-1', kind='fixed', ref='TEST-1', title='Widget opens',
        body='Corrected the widget.', meta={'outcome': 'fixed'}, now=200,
    )
    store.delivery_add(alert['id'], 'test', 'owner@example.test', Receipt(True), now=201)
    sent = store.customer_message_add(
        ref='TEST-1', kind='fixed', channel='test', address='person@example.test',
        name='Example Person', text='Your widget is fixed.', state='sent', now=201,
    )
    store.customer_message_update(sent['id'], sent_at=202)
    try:
        yield SimpleNamespace(root=root, repo=repo, store=store, queues=queues,
                              transcript=transcript, rows=rows, ticket=ticket,
                              report=report, alert=alert, sent=sent)
    finally:
        store.close()


def test_collect_scene_order_and_retained_evidence(retained):
    data = film.collect('TEST-1', root=retained.root)
    assert data['ref'] == 'TEST-1'
    assert [scene['kind'] for scene in data['scenes']] == [
        'report', 'work', 'change', 'check', 'fixed', 'customer', 'duration',
    ]
    scenes = {scene['kind']: scene for scene in data['scenes']}
    assert scenes['report']['text'] == 'The widget fails to open.'
    assert scenes['work']['text'] == 'Read: widget.py\nEdit: widget.py'
    assert scenes['change']['text'] == 'No local commit diff retained.'
    assert scenes['check']['text'] == '2 passed in 0.02s'
    assert scenes['fixed']['caption'] == 'Fixed notice'
    assert 'Corrected the widget.' in scenes['fixed']['text']
    assert scenes['duration']['text'] == '2 min 0 sec'
    assert all(3 <= scene['seconds'] <= 12 for scene in data['scenes'])
    assert data['limits']
    assert film.collect(str(retained.report['id']), root=retained.root) == data


def test_collect_customer_messages_require_sent_state_and_timestamp(retained):
    for state, sent_at in [('pending', None), ('held', None), ('failed', 203),
                           ('uncertain', 203), ('sent', None)]:
        message = retained.store.customer_message_add(
            ref='TEST-1', kind='fixed', channel='test', address='person@example.test',
            text=f'Excluded {state} {sent_at}', state=state, now=201,
        )
        retained.store.customer_message_update(message['id'], sent_at=sent_at)
    other = retained.store.customer_message_add(
        ref='TEST-2', kind='fixed', channel='test', address='other@example.test',
        text='Another ticket message', state='sent', now=201,
    )
    retained.store.customer_message_update(other['id'], sent_at=202)
    scenes = film.collect('TEST-1', root=retained.root)['scenes']
    assert [s['text'] for s in scenes if s['kind'] == 'customer'] == ['Your widget is fixed.']


def test_collect_redacts_names_addresses_secrets_links_and_external_paths(retained):
    sensitive = (
        f'Example Person person@example.test +1 (202) 555-0123 '
        f'sk-test-XXXX ghp_TESTXXXX Bearer TESTXXXX password=TESTPASSWORD '
        f'api_key=TESTAPI https://example.test/private '
        f'/outside/private.txt ~/private.txt C:\\outside\\private.txt '
        f'{retained.repo}/widget.py'
    )
    retained.store.report_update(retained.report['id'], text=sensitive)
    retained.store.customer_message_update(retained.sent['id'], text=sensitive)
    data = film.collect('TEST-1', root=retained.root)
    for scene in (s for s in data['scenes'] if s['kind'] in ('report', 'customer')):
        text = scene['text']
        for value in ('Example Person', 'person@example.test', '555-0123',
                      'sk-test-XXXX', 'ghp_TESTXXXX', 'TESTXXXX', 'TESTPASSWORD',
                      'TESTAPI', 'https://', '/outside/', '~/private', 'C:\\outside',
                      str(retained.repo)):
            assert value not in text
        assert 'a customer' in text
        assert '[secret removed]' in text
        assert '[link removed]' in text
        assert '[path removed]' in text
        assert 'widget.py' in text


def test_transcript_excludes_other_ticket_time_windows_and_untyped_text(retained):
    rows = [
        transcript_row(99, tool('Edit', 'before', file_path=str(retained.repo / 'other-before.py'))),
        transcript_row(99, tool('Bash', 'before-test', command='pytest')),
        transcript_row(100, result('before-test', '99 passed in 1s')),
        *retained.rows,
        transcript_row(150, {'type': 'text', 'text': 'Private conversation: 88 passed'}),
        transcript_row(150, tool('Read', 'external', file_path=str(retained.root / 'private.txt'))),
        transcript_row(150, tool('Bash', 'failed', command='pytest')),
        transcript_row(151, result('failed', '3 passed, 1 failed')),
        transcript_row(152, result('failed', '77 passed', is_error=True)),
        transcript_row(201, tool('Write', 'after', file_path=str(retained.repo / 'other-after.py'))),
        transcript_row(201, result('test', '66 passed in 1s')),
    ]
    other = dict(retained.ticket, ref='TEST-2', claimed_at=201, closed_at=300)
    retained.queues.write_text(json.dumps({'items': [other, retained.ticket]}))
    retained.transcript.write_text('not json\n' + '\n'.join(json.dumps(r) for r in rows))
    scenes = {s['kind']: s['text'] for s in film.collect('TEST-1', root=retained.root)['scenes']}
    assert scenes['work'] == 'Read: widget.py\nEdit: widget.py'
    assert scenes['check'] == '2 passed in 0.02s'
    assert 'Private conversation' not in json.dumps(scenes)


def test_collect_offline_without_executables_or_sockets(retained, monkeypatch):
    def forbidden_socket(*args, **kwargs):
        pytest.fail('collect attempted to open a socket')

    assert not retained.ticket['resolution'].get('commit')
    monkeypatch.setenv('PATH', '')
    monkeypatch.setattr(socket, 'socket', forbidden_socket)
    data = film.collect('TEST-1', root=retained.root)
    assert data['ref'] == 'TEST-1'
    assert data['scenes'][1]['text'] == 'Read: widget.py\nEdit: widget.py'
    assert data['scenes'][3]['text'] == '2 passed in 0.02s'


def test_main_preview_off_uses_only_temporary_state(tmp_path, monkeypatch, capsys):
    from ccc_server import paths

    monkeypatch.setattr(paths, 'COMMAND_CENTER_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('CCC_ONCALL_STATE_DIR', str(tmp_path / 'missing-state'))
    monkeypatch.setenv('CCC_FF_ONCALL', '0')
    (tmp_path / 'feature-flags.json').write_text('{"oncall": false}')
    output = tmp_path / 'preview.mp4'
    assert film.preview_enabled() is False
    assert film.main(['TEST-1', '--output', str(output)]) == 1
    captured = capsys.readouterr()
    assert 'On-call preview is off' in captured.err
    assert captured.out == ''
    assert sorted(p.name for p in tmp_path.iterdir()) == ['feature-flags.json']


def capture_png(argv, **kwargs):
    assert argv[0] == 'node'
    assert Path(argv[1]).name == 'film_capture.cjs'
    assert Path(argv[2]).is_file()
    assert kwargs == {'check': True, 'capture_output': True, 'timeout': 120}
    (Path(argv[3]) / 'frame-000.png').write_bytes(PNG)
    return subprocess.CompletedProcess(argv, 0)


def test_render_missing_ffmpeg_preserves_player_frames_poster_and_manifest(retained, tmp_path, monkeypatch):
    data = film.collect('TEST-1', root=retained.root)
    calls = []

    def capture(argv, **kwargs):
        calls.append(argv)
        return capture_png(argv, **kwargs)

    def missing(program):
        assert program == 'ffmpeg'
        return None

    monkeypatch.setattr(film.subprocess, 'run', capture)
    monkeypatch.setattr(film.shutil, 'which', missing)
    output = tmp_path / 'missing-encoder.mp4'
    rendered = film.render(data, output, dark=True)
    assert len(calls) == 1
    assert rendered['mp4'] is None
    assert not output.exists()
    assert 'ffmpeg is missing' in rendered['notice']
    assert Path(rendered['poster']).read_bytes() == PNG
    assert (Path(rendered['frames']) / 'frame-000.png').read_bytes() == PNG
    page = Path(rendered['html']).read_text()
    assert 'data-theme="dark"' in page
    assert 'The report' in page and 'Your widget is fixed.' in page
    manifest = json.loads(output.with_suffix('.json').read_text())
    assert manifest == dict(rendered, scenes=data['scenes'])


def test_render_subprocess_allowlist_is_only_node_and_ffmpeg(retained, tmp_path, monkeypatch):
    data = film.collect('TEST-1', root=retained.root)
    calls = []
    output = tmp_path / 'encoded.mp4'

    def allowed(argv, **kwargs):
        # Reject any model CLI, shell, git, or other executable immediately.
        assert argv[0] in ('node', '/fake/bin/ffmpeg')
        calls.append(argv)
        if argv[0] == 'node':
            return capture_png(argv, **kwargs)
        assert kwargs == {'check': True, 'capture_output': True, 'timeout': 180}
        assert argv[-1] == str(output)
        assert '-nostdin' in argv and '-n' in argv and '-an' in argv
        listing = Path(argv[argv.index('-i') + 1])
        lines = listing.read_text().splitlines()
        assert len(lines) == 2 * len(data['scenes']) + 1
        for n, scene in enumerate(data['scenes']):
            assert lines[2 * n:2 * n + 2] == [
                f"file 'frame-{n:03}.png'", f"duration {scene['seconds']:.3f}",
            ]
        assert lines[-1] == f"file 'frame-{len(data['scenes']) - 1:03}.png'"
        output.write_bytes(b'fake encoded output')
        return subprocess.CompletedProcess(argv, 0)

    def encoder(program):
        assert program == 'ffmpeg'
        return '/fake/bin/ffmpeg'

    monkeypatch.setattr(film.subprocess, 'run', allowed)
    monkeypatch.setattr(film.shutil, 'which', encoder)
    rendered = film.render(data, output)
    assert [argv[0] for argv in calls] == ['node', '/fake/bin/ffmpeg']
    assert rendered['mp4'] == str(output)
    assert output.read_bytes() == b'fake encoded output'
    assert json.loads(output.with_suffix('.json').read_text())['mp4'] == str(output)


def test_command_no_models_no_network(retained, tmp_path, monkeypatch, capsys):
    """Exercise the entrypoint, allowing only the separately tested capture adapter."""
    from ccc_server import paths
    monkeypatch.setattr(paths, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setenv('CCC_FF_ONCALL', '1')
    monkeypatch.setenv('PATH', '')
    def no_network(*args, **kwargs):
        raise AssertionError('Network access is forbidden')
    monkeypatch.setattr(socket, 'socket', no_network)
    monkeypatch.setattr(film.subprocess, 'run', capture_png)
    output = tmp_path / 'offline.mp4'
    assert film.main(['TEST-1', '--output', str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['mp4'] is None
    assert Path(result['poster']).is_file()


@pytest.mark.skipif(__import__('os').environ.get('CCC_FILM_BROWSER_TEST') != '1',
                    reason='opt-in real offline Chromium capture')
def test_real_command_without_models_and_with_network_blocked(retained, tmp_path):
    """Linux integration proof; the network interposer is inherited by children."""
    import os
    import shutil
    import sys
    if sys.platform != 'linux' or not shutil.which('gcc'):
        pytest.skip('requires Linux and gcc for the network interposer')
    source = tmp_path / 'deny.c'
    source.write_text('''
#define _GNU_SOURCE
#include <sys/socket.h>
#include <dlfcn.h>
#include <errno.h>
int connect(int fd,const struct sockaddr *a,socklen_t n){
 if(a && (a->sa_family==AF_INET || a->sa_family==AF_INET6)){errno=ENETUNREACH;return -1;}
 return ((int(*)(int,const struct sockaddr*,socklen_t))dlsym(RTLD_NEXT,"connect"))(fd,a,n);
}
ssize_t sendto(int fd,const void*b,size_t n,int flags,const struct sockaddr*a,socklen_t l){
 if(a && (a->sa_family==AF_INET || a->sa_family==AF_INET6)){errno=ENETUNREACH;return -1;}
 return ((ssize_t(*)(int,const void*,size_t,int,const struct sockaddr*,socklen_t))dlsym(RTLD_NEXT,"sendto"))(fd,b,n,flags,a,l);
}
''')
    shim = tmp_path / 'deny.so'
    subprocess.run(['gcc', '-shared', '-fPIC', str(source), '-ldl', '-o', str(shim)], check=True)
    binaries = tmp_path / 'bin'; binaries.mkdir()
    for name in ('node', 'git', 'ffmpeg'):
        executable = shutil.which(name)
        if not executable:
            pytest.skip(f'{name} is not installed')
        (binaries / name).symlink_to(executable)
    env = dict(os.environ, PATH=str(binaries), LD_PRELOAD=str(shim), CCC_FF_ONCALL='1')
    # Fail if the network blocker does not actually reject an IPv4 connection.
    probe = subprocess.run([sys.executable, '-c',
                            'import socket; socket.create_connection(("127.0.0.1", 9))'],
                           env=env, capture_output=True)
    assert probe.returncode != 0 and b'Network is unreachable' in probe.stderr
    output = tmp_path / 'real.mp4'
    run = subprocess.run([sys.executable, str(film.ROOT / 'ccc'), 'oncall', 'film',
                          'TEST-1', '--output', str(output)], env=env,
                         capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)['mp4'] == str(output)
    assert output.stat().st_size > 1000
    assert output.with_suffix('.png').stat().st_size > 1000
