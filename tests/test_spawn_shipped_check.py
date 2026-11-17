"""Queue drain jobs use claim-time recall; concrete goals keep spawn warnings."""
import io
import json
from unittest.mock import Mock

import pytest

import server
from ccc_server import brain_hook, shipped_check


@pytest.fixture
def spawn_request(monkeypatch):
    monkeypatch.setattr(server, '_load_spawn_defaults', lambda: {})
    monkeypatch.setattr(server, '_model_policy_error', lambda _: None)
    monkeypatch.setattr(server, 'byok_spawn_env', lambda *args: {})
    monkeypatch.setattr(server, '_restamp_archive_serve_cache_after_mutation', lambda: None)
    monkeypatch.setattr(server, '_tag_spawn_task_meta_in_registry', lambda **kwargs: None)
    monkeypatch.setattr(server, '_apply_spawn_auto_resume_opt_in', lambda *args: None)
    monkeypatch.setattr(server, '_tag_spawned_via_in_registry', lambda **kwargs: None)
    monkeypatch.setattr(brain_hook, 'session_start_prefix', lambda _: '')
    spawn = Mock(return_value={'ok': True, 'session_id': 'test-child'})
    warning = Mock(return_value={
        'shipped': True, 'confidence': .9, 'repo': 'test-project',
        'commit': 'abc123def456', 'subject': 'A matching fix', 'tickets': [],
    })
    activity = Mock()
    monkeypatch.setattr(server, 'spawn_session', spawn)
    monkeypatch.setattr(server, '_log_activity', activity)
    monkeypatch.setattr(shipped_check, 'check_shipped_for_spawn', warning)

    def submit(prompt, name=''):
        request = object.__new__(server.CommandCenterHandler)
        request.path = '/api/sessions/spawn'
        request.command = 'POST'
        body = json.dumps({'prompt': prompt, 'name': name, 'engine': 'claude'}).encode()
        request.headers = {'Content-Length': str(len(body))}
        request.rfile = io.BytesIO(body)
        request._check_proxy_secret = lambda: True
        request._check_same_origin = lambda: True
        request._phone_pin_blocked = lambda _: False
        request._note_typed_spawn = lambda *args: None
        request.send_json = lambda data, status=200: setattr(request, 'result', (status, data))
        request.do_POST()
        assert request.result[0] == 200, request.result
        assert request.result[1]['ok']
        return spawn, warning, activity, request.result[1]

    return submit


@pytest.mark.parametrize('prompt,name', [
    ('Drain the TEST WatchTower queue and keep it empty.\nWorker instructions.', ''),
    ('Drain the TEST-TOOLS WatchTower queue and keep it empty.', 'Custom worker name'),
    ('  drain the test_tools watchtower queue.\nWorker instructions.', 'TEST worker'),
])
def test_queue_worker_skips_lookup_and_keeps_prompt(spawn_request, prompt, name):
    spawn, warning, activity, response = spawn_request(prompt, name)
    warning.assert_not_called()
    assert spawn.call_args.args == (prompt.strip(),)
    assert not any(call.args[:2] == ('spawn', 'SHIPPED_WARNING') for call in activity.call_args_list)
    assert not response.get('shipped_warning')


@pytest.mark.parametrize('prompt,name,goal', [
    ('Repair the archive refresh path.\nMore detail.', '', 'Repair the archive refresh path.'),
    ('Inspect the known regression.', 'A concrete goal', 'A concrete goal'),
    ('Repair a ticket reported through WatchTower.', 'TEST worker', 'TEST worker'),
    ('Drain the staging backlog; inspect the WatchTower queue afterward.', '',
     'Drain the staging backlog; inspect the WatchTower queue afterward.'),
    ('You are an independent verifier for a WatchTower ticket.', '',
     'You are an independent verifier for a WatchTower ticket.'),
])
def test_concrete_goal_still_checks_and_warns(spawn_request, prompt, name, goal):
    spawn, warning, activity, _ = spawn_request(prompt, name)
    warning.assert_called_once_with(goal)
    assert spawn.call_args.args[0].startswith('Heads-up: this may already be shipped:')
    assert spawn.call_args.args[0].endswith(prompt)
    assert any(call.args[:2] == ('spawn', 'SHIPPED_WARNING') for call in activity.call_args_list)
