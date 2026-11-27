"""The CCC-709 probe: hour-old sidebar tokens must still get a delta."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

scripts = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('old_token_check', scripts / 'check-sidebar-old-token.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


@pytest.fixture
def reader():
    """A stand-in list reader: ctx advances on demand; `forget` makes it
    answer old-context tokens with a full body, like the 8-entry history."""
    state = {'nonce': 'aaaaaaaa', 'ver': 1, 'ctx': 'c0', 'forget': False, 'seen': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            since = parse_qs(urlparse(self.path).query).get('since', [None])[0]
            state['seen'].append(since)
            token = f"{state['nonce']}.{state['ver']}.{state['ctx']}.0"
            if since == token:
                self.send_response(304)
                self.end_headers()
                return
            body = {'conversations': [{'session_id': 'a'}], 'delta_token': token}
            if since and not (state['forget'] and since.split('.')[2] != state['ctx']):
                body.update(delta=True, conversations=[])
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state['url'] = f'http://127.0.0.1:{server.server_address[1]}'
    yield state
    server.shutdown()


def _hourly(reader, path, hours, start=1_000_000.0):
    out = None
    for hour in range(hours):
        reader['ver'] += 1
        reader['ctx'] = f'c{hour + 1}'
        out = check.run(reader['url'], path, now=start + hour * 3600)
    return out


def test_pending_then_pass_when_old_tokens_get_deltas(reader, tmp_path):
    path = tmp_path / 'state.json'
    first = check.run(reader['url'], path, now=1_000_000.0 - 3600)
    assert first['status'] == 'pending' and not first['real_data']
    assert reader['seen'] == [None]  # seeded with one plain read
    out = _hourly(reader, path, 8)
    assert out['status'] == 'pass' and out['real_data']
    assert out['evidence']['samples'] >= 3
    # Both an about-an-hour-old and a 4-20 hour old token were replayed.
    ages = out['evidence']['ages_s']
    assert min(ages) <= 3600 and max(ages) >= 4 * 3600
    # Once seeded, runs replay saved tokens instead of reading full bodies.
    assert reader['seen'].count(None) == 1


def test_full_body_for_a_forgotten_context_fails(reader, tmp_path):
    path = tmp_path / 'state.json'
    check.run(reader['url'], path, now=1_000_000.0 - 3600)
    reader['forget'] = True
    out = _hourly(reader, path, 2)
    assert out['status'] == 'fail'
    assert out['evidence']['full_bodies'][0]['same_process'] is True


def test_other_process_full_body_and_unchanged_context_do_not_count(reader, tmp_path):
    path = tmp_path / 'state.json'
    check.run(reader['url'], path, now=1_000_000.0 - 3600)
    # Same context an hour later: 304, not informative.
    out = check.run(reader['url'], path, now=1_000_000.0)
    assert out['status'] == 'pending' and out['evidence']['samples'] == 0
    # A restart into a new projection rejects the old process's token.
    reader.update(nonce='bbbbbbbb', ctx='c9', forget=True)
    out = check.run(reader['url'], path, now=1_000_000.0 + 3600)
    assert out['status'] == 'pending' and out['evidence']['full_bodies'] == []


def test_unreachable_reader_stays_pending(tmp_path):
    out = check.run('http://127.0.0.1:9', tmp_path / 'state.json', now=1_000_000.0)
    assert out['status'] == 'pending' and out['evidence']['errors']
