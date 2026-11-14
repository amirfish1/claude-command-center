"""Real local HTTP failures must not claim a healthy dashboard is absent."""
import http.server
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import urllib.error

import pytest


@pytest.fixture
def cli():
    loader = importlib.machinery.SourceFileLoader('ccc_sessions_failure', str(Path(__file__).resolve().parents[1] / 'ccc'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def api():
    class Handler(http.server.BaseHTTPRequestHandler):
        health = {'pid': 123, 'uptime_s': 400, 'sessions_readiness': {'state': 'warming', 'elapsed_s': 180}}
        census_delay = 0.15
        census_payload = {'ok': True, 'sessions': []}
        warm_census_path = None
        health_code = 200
        calls = []
        def log_message(self, *args):
            pass
        def do_GET(self):
            Handler.calls.append(self.path)
            if self.path.startswith('/api/sessions/census'):
                time.sleep(Handler.census_delay)
                if Handler.warm_census_path and self.path != Handler.warm_census_path:
                    data, code = {'ok': False, 'sessions': []}, 503
                else:
                    data, code = Handler.census_payload, 200
            else:
                data, code = Handler.health, Handler.health_code
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', Handler
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize('state,expected', [('warming', 'warming up for 180.0s'),
                                            ('busy', 'busy serving sessions for 180.0s'),
                                            ('ready', 'sessions are busy or slow')])
def test_real_timeout_uses_real_health_state(cli, api, monkeypatch, capsys, state, expected):
    base, handler = api
    handler.health['sessions_readiness']['state'] = state
    original = cli._get_json
    # Shorten only the client census deadline; the actual HTTP request and
    # healthy concurrent response run over real sockets.
    monkeypatch.setattr(cli, '_get_json', lambda base, route, timeout=10:
                        original(base, route, timeout=.03 if route.startswith('/api/sessions/census') else timeout))
    assert cli.cmd_sessions(SimpleNamespace(server=base, since=None, json=False)) == 1
    result = capsys.readouterr()
    assert not result.out
    assert expected in result.err
    assert 'server uptime 400.0s' in result.err
    assert 'timed out after' in result.err
    assert 'is CCC running?' not in result.err
    assert handler.calls == ['/api/sessions/census?since=86400', '/api/health']


def test_old_health_does_not_infer_warming_from_uptime(cli, api, capsys):
    base, handler = api
    handler.health = {'pid': 123, 'uptime_s': 5}
    cli._sessions_failure(base, TimeoutError('timed out'), 10)
    message = capsys.readouterr().err
    assert 'running' in message and 'busy or slow' in message and 'uptime 5.0s' in message
    assert 'warming' not in message


def test_non_200_health_can_report_warming(cli, api, capsys):
    base, handler = api
    handler.health_code = 503
    cli._sessions_failure(base, TimeoutError('timed out'), 10)
    assert 'warming up for 180.0s' in capsys.readouterr().err


def test_failed_probe_preserves_uncertainty(cli, api, capsys):
    base, handler = api
    handler.health = 'proxy error'
    cli._sessions_failure(base, TimeoutError('timed out'), 10)
    message = capsys.readouterr().err
    assert 'server state unavailable' in message
    assert 'is CCC running?' not in message
    cli._sessions_failure(base, urllib.error.URLError(ConnectionRefusedError('refused')), .1)
    assert 'not accepting connections' in capsys.readouterr().err


def test_success_does_not_probe_health_or_change_json(cli, api, capsys):
    base, handler = api
    handler.census_delay = 0
    assert cli.cmd_sessions(SimpleNamespace(server=base, since=None, json=True)) == 0
    assert json.loads(capsys.readouterr().out) == {'ok': True, 'sessions': []}
    assert handler.calls == ['/api/sessions/census?since=86400']


@pytest.mark.parametrize('since', [0, 7200, 18000, 86400, 172800])
def test_time_window_uses_warm_census_and_preserves_filter(cli, api, capsys, since):
    base, handler = api
    handler.census_delay = 0
    # Only the default census is kept warm by the server. Wider windows
    # still need their own result so recently ended sessions are included.
    handler.warm_census_path = f'/api/sessions/census?since={max(86400, since)}'
    handler.census_payload = {'ok': True, 'sessions': [
        {'session_id': 'boundary', 'last_event_age_s': since},
        {'session_id': 'older', 'last_event_age_s': since + 1},
        {'session_id': 'unknown', 'last_event_age_s': None},
    ]}
    assert cli.cmd_sessions(SimpleNamespace(server=base, since=since, json=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['sessions'] == [handler.census_payload['sessions'][0]]
    assert handler.calls == [handler.warm_census_path]


@pytest.mark.parametrize('since', [None, 7200])
def test_live_query_uses_warm_census_without_implicit_age_limit(cli, api, capsys, since):
    base, handler = api
    handler.census_delay = 0
    handler.warm_census_path = '/api/sessions/census?since=86400'
    handler.census_payload = {'ok': True, 'sessions': [
        {'session_id': 'recent-live', 'state': 'working', 'last_event_age_s': 60},
        {'session_id': 'old-live', 'state': 'idle', 'last_event_age_s': 172800},
        {'session_id': 'unknown-live', 'state': 'waiting', 'last_event_age_s': None},
        {'session_id': 'ended', 'state': 'ended', 'last_event_age_s': 30},
    ]}
    assert cli.cmd_sessions(SimpleNamespace(server=base, since=since, live=True, json=True)) == 0
    rows = json.loads(capsys.readouterr().out)['sessions']
    expected = handler.census_payload['sessions'][:3 if since is None else 1]
    assert rows == expected
    assert handler.calls == [handler.warm_census_path]
