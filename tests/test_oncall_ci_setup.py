import json
import pytest
from ccc_server.oncall.ci_source import CISource
from tests.test_oncall_ci import source, run


def test_no_login_message():
    src = CISource({'id': 'ci', 'repo': 'example/demo'})
    src._credential = lambda: ''
    assert src.check() == (False, 'No CI login. Run gh auth login or supply --vault token=ENTRY.')


@pytest.mark.parametrize('reply,message', [
    ({'workflows': []}, 'No CI workflows'),
    ({'workflows': [{'state': 'disabled_manually'}]}, 'disabled'),
])
def test_workflow_setup_errors(reply, message):
    src = CISource({'id': 'ci', 'repo': 'example/demo'})
    src._credential = lambda: 'test-only'
    src.request = lambda url, headers: (200, json.dumps(reply if '/workflows?' in url else {'default_branch': 'main'}).encode(), {})
    assert message in src.check()[1]


def test_unreadable_private_repo_message():
    src = CISource({'id': 'ci', 'repo': 'example/demo'})
    src._credential = lambda: 'test-only'
    src.request = lambda *a: (404, b'', {})
    assert 'cannot read this repository' in src.check()[1]


def test_recheck_waits_for_new_attempt_not_another_poll():
    src, rows, calls = source()
    src.recheck = True
    src.poll()
    rows[:] = [run(1, sha='same')]
    assert src.poll() == []
    assert src.poll() == []
    rows[:] = [dict(run(1, sha='same'), run_attempt=2)]
    assert len(src.poll()) == 1


def test_recheck_green_cancels_candidate():
    src, rows, calls = source()
    src.recheck = True
    src.poll()
    rows[:] = [run(1, sha='same')]
    assert src.poll() == []
    rows[:] = [run(2, 'success', sha='same')]
    src.poll()
    rows[:] = [run(3, sha='same')]
    assert src.poll() == []


def test_setup_offers_one_ci_question_and_does_not_repeat(tmp_path, monkeypatch):
    from tests.test_oncall_cli import Box
    from ccc_server.oncall import ci_source
    box = Box(tmp_path / 'state', monkeypatch)
    box.flag = True
    monkeypatch.setattr(ci_source, 'setup_candidate', lambda folder: {'repo': 'example/demo'})
    monkeypatch.setattr(CISource, '_check', lambda self: 'Connected')
    args = ('setup', '--repo', str(tmp_path), '--channel', 'command', '--set', 'send=cat')
    assert box.run(*args, interactive=True, answers=['y']) == 0
    assert len(box.asked) == 1 and 'CI' in box.asked[0]
    assert box.eng.config()['sources'][0]['type'] == 'ci'
    box.asked.clear()
    assert box.run('setup', '--repo', str(tmp_path), interactive=True) == 0
    assert box.asked == []


def test_actions_policy_disabled_message():
    src = CISource({'id': 'ci', 'repo': 'example/demo'})
    src._credential = lambda: 'test-only'
    src.request = lambda url, headers: (200, json.dumps({'enabled': False} if url.endswith('/permissions') else {'default_branch': 'main'}).encode(), {})
    assert src.check()[1] == 'Actions is disabled for this repository.'
