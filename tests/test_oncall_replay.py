"""Offline replay checks with real scratch git and a local HTTP fixture."""
import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading

import pytest
from ccc_server.oncall import replay as r


@pytest.fixture
def api(tmp_path):
    routes = {}
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            body = json.dumps(routes.get(self.path.split('?')[0], [])).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield r.GitHub(tmp_path / 'cache', f'http://127.0.0.1:{server.server_port}'), routes, calls
    server.shutdown()
    server.server_close()
    thread.join()


def test_selection_and_cache(api):
    client, routes, calls = api
    routes['/repos/example/library'] = {'private': False}
    routes['/repos/example/library/issues'] = [
        {'number': 1, 'title': 'Bug', 'body': 'Broken', 'closed_at': '2026-11-10T00:00:00Z', 'html_url': 'https://github.com/example/library/issues/1'},
        {'number': 2, 'title': 'Old', 'closed_at': '2020-01-01T00:00:00Z'},
        {'number': 3, 'closed_at': '2026-11-10T00:00:00Z', 'pull_request': {}},
    ]
    routes['/repos/example/library/issues/1/events'] = [{'event': 'closed', 'commit_id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}]
    routes['/repos/example/library/commits/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/pulls'] = [{'number': 4, 'merged_at': '2026-11-10T00:00:00Z', 'merge_commit_sha': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'base': {'repo': {'full_name': 'example/library'}}}]
    routes['/repos/example/library/pulls/4/files'] = [{'filename': 'tests/test_bug.py', 'status': 'added'}]
    now = dt.datetime(2026, 11, 11, tzinfo=dt.timezone.utc)
    selected = r.select_cases(client, 'example/library', 30, 20, now)
    assert [c['number'] for c in selected] == [1]
    before = len(calls)
    assert r.select_cases(client, 'example/library', 30, 20, now) == selected
    assert len(calls) == before
    routes['/repos/example/library/issues/1/events'] = [{'event': 'cross-referenced', 'commit_id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}]
    for file in client.cache.iterdir():
        file.unlink()
    assert r.select_cases(client, 'example/library', 30, 20, now) == []


def git(cwd, *args):
    return subprocess.check_output(['git', '-C', str(cwd), *args], stderr=subprocess.STDOUT).decode().strip()


def test_history_hiding_and_test_transfer(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-q')
    git(source, 'config', 'user.email', 'test@example.invalid')
    git(source, 'config', 'user.name', 'Test')
    (source / 'bug.py').write_text('value = 0\n')
    git(source, 'add', 'bug.py')
    git(source, 'commit', '-qm', 'old')
    parent = git(source, 'rev-parse', 'HEAD')
    (source / 'bug.py').write_text('value = 1\n')
    (source / 'test_bug.py').write_text('assert value == 1\n')
    git(source, 'add', 'bug.py', 'test_bug.py')
    git(source, 'commit', '-qm', 'secret fix')
    merge = git(source, 'rev-parse', 'HEAD')
    env = r.isolated_env(tmp_path / 'home')
    target = tmp_path / 'target'
    r.snapshot(source, parent, target, env)
    assert git(target, 'remote') == ''
    assert git(target, 'rev-list', '--count', 'HEAD') == '1'
    assert 'secret fix' not in git(target, 'log', '--all', '--oneline')
    assert subprocess.run(['git', '-C', str(target), 'cat-file', '-e', merge], capture_output=True).returncode != 0
    assert not (target / 'test_bug.py').exists()
    r.install_tests(source, merge, target, ['test_bug.py'], env)
    assert (target / 'test_bug.py').read_text() == 'assert value == 1\n'
    assert (target / 'bug.py').read_text() == 'value = 0\n'


@pytest.mark.parametrize('changed,agent,before,reference,final,expected', [
    (['bug.py'], 0, 1, 0, 0, 'fixed'),
    (['bug.py'], 0, 1, 0, 1, 'wrong fix'),
    ([], 0, 1, 0, 1, 'no attempt or asked a question'),
    (['bug.py'], 124, 1, 0, 1, 'wrong fix'),
    (['bug.py'], 0, 0, 0, 0, 'could not set up'),
    (['bug.py'], 0, 1, 1, 0, 'could not set up'),
    (['bug.py'], 0, 1, 0, 2, 'wrong fix'),
])
def test_outcomes(changed, agent, before, reference, final, expected):
    assert r.judge(changed, agent, before, reference, final) == expected


def test_env_only_copies_auth(tmp_path, monkeypatch):
    monkeypatch.setenv('SECRET_TEST_VALUE', 'not-for-child')
    auth = tmp_path / 'auth.json'
    auth.write_text('{}')
    env = r.isolated_env(tmp_path / 'home', auth)
    assert 'SECRET_TEST_VALUE' not in env
    assert (Path(env['CODEX_HOME']) / 'auth.json').read_text() == '{}'
    assert not (Path(env['CODEX_HOME']) / 'config.toml').exists()


def test_timeout(tmp_path):
    code, text = r.run(['python3', '-c', 'import time; time.sleep(5)'], tmp_path, timeout=.05)
    assert code == 124


def test_question_overrides_partial_fix():
    assert r.judge(["bug.py"], 0, 1, 0, 0, "question") == "no attempt or asked a question"


def test_graphql_closer_fallback(api, monkeypatch):
    client, routes, calls = api
    routes['/repos/example/library'] = {'private': False}
    routes['/repos/example/library/issues'] = [{'number': 1, 'title': 'Bug', 'closed_at': '2026-11-10T00:00:00Z', 'html_url': 'https://github.com/example/library/issues/1'}]
    routes['/repos/example/library/issues/1/events'] = [{'event': 'closed', 'commit_id': None}]
    pr = {'number': 4, 'merged_at': True, 'merge_commit_sha': 'a' * 40, 'base': {'repo': {'full_name': 'example/library'}}}
    monkeypatch.setattr(client, 'closing_pr', lambda repo, number: pr)
    routes['/repos/example/library/pulls/4/files'] = [{'filename': 'test_bug.py', 'status': 'modified'}]
    assert len(r.select_cases(client, 'example/library', 30, 1, dt.datetime(2026, 11, 11, tzinfo=dt.timezone.utc))) == 1


def test_preview_off_before_io(tmp_path, monkeypatch):
    monkeypatch.setenv('CCC_FF_ONCALL', '0')
    with pytest.raises(SystemExit):
        r.main(['example/library', '--output', str(tmp_path / 'results')])
    assert not (tmp_path / 'results').exists()


def test_real_test_processes_distinguish_before_wrong_and_fixed(tmp_path):
    (tmp_path / 'value.py').write_text('value = 0\n')
    (tmp_path / 'test_value.py').write_text('from value import value\nassert value == 1\n')
    command = ['python3', '-B', 'test_value.py']
    before, _ = r.run(command, tmp_path)
    assert before == 1
    (tmp_path / 'value.py').write_text('value = 2\n')
    wrong, _ = r.run(command, tmp_path)
    assert r.judge(['value.py'], 0, before, 0, wrong) == 'wrong fix'
    (tmp_path / 'value.py').write_text('value = 1\n')
    fixed, _ = r.run(command, tmp_path)
    assert r.judge(['value.py'], 0, before, 0, fixed) == 'fixed'
