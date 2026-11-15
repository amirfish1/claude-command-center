"""The removed outcome surface refuses callers without changing ordinary spawn."""
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import server
from ccc_server import brain_hook, shipped_check


@pytest.fixture
def cli():
    loader = importlib.machinery.SourceFileLoader('ccc_outcomes_removed', str(Path(__file__).resolve().parents[1] / 'ccc'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def handler(route, method='GET', payload=None):
    request = object.__new__(server.CommandCenterHandler)
    request.path = route
    request.command = method
    body = json.dumps(payload or {}).encode()
    request.headers = {'Content-Length': str(len(body))}
    request.rfile = io.BytesIO(body)
    request._check_proxy_secret = lambda: True
    request._check_same_origin = lambda: True
    request._phone_pin_blocked = lambda path: False
    request._note_typed_spawn = lambda *args: None
    request.send_json = lambda data, status=200: setattr(request, 'result', (status, data))
    return request


@pytest.mark.parametrize('method,suffix', [
    ('GET', ''), ('GET', '/'), ('GET', '/settings'), ('GET', '/OUT-1'),
    ('POST', ''), ('POST', '/settings'), ('POST', '/create'),
    ('POST', '/update'), ('POST', '/link'), ('POST', '/event'),
    ('GET', '/intake'), ('POST', '/intake'), ('POST', '/associate'),
])
def test_removed_routes_return_not_found(method, suffix):
    request = handler('/api/outcomes' + suffix, method, {'enabled': True})
    if method == 'GET':
        request._do_GET()
    else:
        request.do_POST()
    assert request.result == (404, {'error': 'Not found'})


@pytest.mark.parametrize('value', ['OUT-1', '', None])
def test_removed_spawn_field_refuses_before_forwarding(monkeypatch, value):
    forward = Mock(side_effect=AssertionError('must not forward removed field'))
    monkeypatch.setattr(server, '_federation_spawn_on_node', forward)
    request = handler('/api/sessions/spawn', 'POST', {
        'prompt': 'hello', 'outcome_id': value, 'node': 'another-node',
    })
    request.do_POST()
    assert request.result[0] == 400
    assert request.result[1]['code'] == 'unsupported_spawn_field'
    forward.assert_not_called()


def test_cli_rejects_removed_flag_without_request(cli, monkeypatch, capsys):
    post = Mock(side_effect=AssertionError('must not contact server'))
    monkeypatch.setattr(cli, '_post_json', post)
    with pytest.raises(SystemExit) as exc:
        cli.main(['spawn', '--outcome-id', 'OUT-1', 'hello'])
    assert exc.value.code == 2
    assert 'unrecognized arguments' in capsys.readouterr().err
    post.assert_not_called()


def test_cli_ordinary_spawn_preserves_report_to_and_prompt(cli, monkeypatch):
    monkeypatch.setattr(cli, '_resolve_server', lambda _: 'http://127.0.0.1:1')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    post = Mock(return_value=(200, {'ok': True, 'session_id': 'child', 'engine': 'claude'}))
    monkeypatch.setattr(cli, '_post_json', post)
    assert cli.main(['spawn', '--report-to', 'parent', 'hello']) == 0
    args = post.call_args.args
    assert args[1] == '/api/sessions/spawn'
    assert args[2]['prompt'] == 'hello'
    assert args[2]['report_to'] == 'parent'
    assert 'outcome_id' not in args[2]


def test_ordinary_spawn_keeps_parent_without_outcome_context(monkeypatch):
    monkeypatch.setattr(server, '_load_spawn_defaults', lambda: {})
    monkeypatch.setattr(server, '_model_policy_error', lambda _: None)
    monkeypatch.setattr(server, 'byok_spawn_env', lambda *args: {})
    monkeypatch.setattr(server, '_log_activity', lambda *args, **kwargs: None)
    monkeypatch.setattr(server, '_restamp_archive_serve_cache_after_mutation', lambda: None)
    monkeypatch.setattr(server, '_tag_spawn_task_meta_in_registry', lambda **kwargs: None)
    monkeypatch.setattr(server, '_apply_spawn_auto_resume_opt_in', lambda *args: None)
    monkeypatch.setattr(server, '_tag_spawned_via_in_registry', lambda **kwargs: None)
    monkeypatch.setattr(shipped_check, 'check_shipped_for_spawn', lambda _: None)
    monkeypatch.setattr(brain_hook, 'session_start_prefix', lambda _: '')
    spawn = Mock(return_value={'ok': True, 'session_id': 'child'})
    monkeypatch.setattr(server, 'spawn_session', spawn)
    request = handler('/api/sessions/spawn', 'POST', {
        'prompt': 'hello', 'engine': 'claude', 'parent_session_id': '11111111-1111-4111-8111-111111111111',
    })
    request.do_POST()
    assert request.result[0] == 200, request.result
    assert request.result[1]['ok']
    assert spawn.call_args.args == ('hello',)
    assert spawn.call_args.kwargs['parent_session_id'] == '11111111-1111-4111-8111-111111111111'
    assert not any(key.startswith('outcome') for key in request.result[1])
