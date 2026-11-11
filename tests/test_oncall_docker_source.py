import json
import sys
import pytest
from ccc_server.oncall.docker_source import DockerSource, _command, _Problem, MAX_BYTES


def test_episodes_logs_and_restart_threshold():
    source = DockerSource({'id': 'd', 'container': 'web', 'restart_threshold': 2})
    row = {'State': {'Status': 'running', 'Health': {'Status': 'healthy'}}, 'RestartCount': 0}
    calls = []
    def command(argv):
        calls.append(argv)
        if argv[1] == 'logs':
            return b'password=sk-ant-test-XXXX\nerror'
        return json.dumps([row]).encode()
    source.command = command
    assert source.poll() == []
    row['RestartCount'] = 2
    first = source.poll()
    assert len(first) == 1 and 'sk-ant-test-XXXX' not in first[0].text
    row['State']['Health']['Status'] = 'unhealthy'
    assert source.poll() == []
    assert sum(c[1] == 'logs' for c in calls) == 1
    assert source.poll() == []
    row['State']['Health']['Status'] = 'healthy'
    assert source.poll() == []
    row['State'] = {'Status': 'exited', 'ExitCode': 1}
    second = source.poll()
    assert len(second) == 1 and second[0].fingerprint != first[0].fingerprint
    assert all(isinstance(c, list) and c[0] == 'docker' for c in calls)


def test_bounded_subprocess():
    assert _command([sys.executable, '-c', 'print("ok")']).strip() == b'ok'
    with pytest.raises(_Problem, match='limit'):
        _command([sys.executable, '-c', f'print("x" * {MAX_BYTES + 1})'])


def test_name_validation():
    with pytest.raises(ValueError):
        DockerSource({'id': 'd', 'container': '--bad'})


def test_timeout_kills_descendant_group(monkeypatch):
    import time
    import ccc_server.oncall.docker_source as docker
    monkeypatch.setattr(docker, 'TIMEOUT', 0.25)
    start = time.monotonic()
    with pytest.raises(_Problem, match='timed out'):
        docker._command([sys.executable, '-c',
            'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); time.sleep(30)'])
    assert time.monotonic() - start < 3


def test_setup_captures_restart_counter():
    source = DockerSource({'id': 'setup', 'container': 'web'})
    row = {'State': {'Status': 'running'}, 'RestartCount': 20}
    source.command = lambda argv: json.dumps([row]).encode() if argv[1] == 'inspect' else b'error'
    assert source.check()[0]
    row['RestartCount'] = 23
    assert len(source.poll()) == 1
