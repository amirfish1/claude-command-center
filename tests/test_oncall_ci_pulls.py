import json
from tests.test_oncall_ci import source, run


def test_allowed_same_repo_pull_and_green_keep_default_branch_separate():
    src, rows, calls = source()
    src.pull_requests = True
    src.pr_allowlist = {'dependency-bot'}
    original = src.request
    pull = {'number': 4, 'state': 'open', 'user': {'login': 'dependency-bot'},
            'head': {'ref': 'deps/update', 'sha': 'red', 'repo': {'full_name': 'example/demo'}}}
    src.request = lambda url, headers: (200, json.dumps([pull]).encode(), {}) if '/pulls?' in url else original(url, headers)
    src.poll()
    rows[:] = [run(1, branch='deps/update', sha='red')]
    finding = src.poll()[0]
    assert 'Push only to origin refs/heads/deps/update' in finding.text
    assert 'Never push to the default branch main' in finding.text
    rows[:] = [run(2, 'success', sha='other', branch='main')]
    src.poll()
    assert not src._get('confirmed')
    pull['head']['sha'] = 'green'
    rows[:] = [run(3, 'success', sha='green', branch='deps/update')]
    src.poll()
    assert finding.fingerprint in src._get('confirmed')


def test_unknown_author_fork_closed_or_wrong_sha_never_files():
    for reason in ('author', 'fork', 'closed', 'sha'):
        src, rows, _ = source()
        src.pull_requests = True
        src.pr_allowlist = {'dependency-bot'}
        original = src.request
        pull = {'number': 4, 'state': 'open', 'user': {'login': 'dependency-bot'},
                'head': {'ref': 'deps/update', 'sha': 'red', 'repo': {'full_name': 'example/demo'}}}
        if reason == 'author': pull['user']['login'] = 'unknown'
        if reason == 'fork': pull['head']['repo']['full_name'] = 'unknown/demo'
        if reason == 'closed': pull['state'] = 'closed'
        if reason == 'sha': pull['head']['sha'] = 'other'
        src.request = lambda url, headers: (200, json.dumps([pull]).encode(), {}) if '/pulls?' in url else original(url, headers)
        src.poll()
        rows[:] = [run(1, branch='deps/update', sha='red')]
        assert src.poll() == [], reason
