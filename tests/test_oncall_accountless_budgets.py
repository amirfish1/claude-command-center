"""Per-poll process and request budgets for sources without an account."""
import json

from ccc_server.oncall.docker_source import DockerSource
from ccc_server.oncall.alertmanager_source import AlertmanagerSource
from ccc_server.oncall.logs_source import LogsSource
from tests.test_oncall_alertmanager_source import group


def test_docker_process_budget_per_poll():
    row = {'State': {'Status': 'running', 'Health': {'Status': 'healthy'}}, 'RestartCount': 0}
    calls = []
    def command(argv):
        calls.append(argv[1])
        return json.dumps([row]).encode() if argv[1] == 'inspect' else b'ERROR failed\n'
    source = DockerSource({'id': 'docker', 'container': 'sample'})
    source.command = command
    assert source.check()[0] and calls == ['inspect']
    calls.clear()
    assert source.poll() == [] and calls == ['inspect']
    calls.clear()
    row['State'] = {'Status': 'restarting', 'Restarting': True}
    assert len(source.poll()) == 1 and calls == ['inspect', 'logs']
    calls.clear()
    assert source.poll() == [] and calls == ['inspect']


def test_alertmanager_single_request_during_burst_and_failure():
    rows, calls = [], []
    reply = [200]
    def http(*args, **kwargs):
        calls.append(1)
        return reply[0], json.dumps(rows).encode()
    source = AlertmanagerSource({'id': 'am', 'url': 'http://localhost:9093'}, http=http)
    assert source.check()[0]
    rows.extend(group('new-' + str(i)) for i in range(10))
    found = []
    for _ in range(4):
        calls.clear()
        batch = source.poll()
        assert len(calls) == 1 and len(batch) <= 3
        found.extend(batch)
    assert len(found) == 10
    prior = source._get('groups')
    reply[0] = 503
    calls.clear()
    assert source.poll() == [] and len(calls) == 1
    assert source._get('groups') == prior and not source._get('recovered')


def test_file_500_old_errors_are_ignored_and_burst_is_bounded(tmp_path):
    logfile = tmp_path / 'app.log'
    logfile.write_text(''.join('ERROR old ' + chr(65 + i % 26) + '\n' for i in range(500)))
    source = LogsSource({'id': 'file', 'path': str(logfile)})
    assert source.check()[0] and source.poll() == []
    with logfile.open('a') as stream:
        stream.write(''.join('ERROR new ' + chr(65 + i) + '\n' for i in range(20)))
    found = []
    for _ in range(8):
        batch = source.poll()
        assert len(batch) <= 3
        found.extend(batch)
    assert len(found) == len({f.fingerprint for f in found}) == 20
    assert source.poll() == []
