"""Connection checks must not acknowledge unpolled monitoring evidence."""
import json

from ccc_server.oncall.logs_source import LogsSource
from ccc_server.oncall.alertmanager_source import AlertmanagerSource
from tests.test_oncall_alertmanager_source import group


def test_log_recheck_preserves_new_errors(tmp_path):
    log = tmp_path / 'sample.log'
    log.write_text('ERROR old\n')
    source = LogsSource({'id': 'logs', 'path': str(log)})
    assert source.check()[0]
    log.write_text('ERROR old\nERROR new\n')
    position = source._get('position')
    assert source.check()[0]
    assert source._get('position') == position
    assert len(source.poll()) == 1
    assert source.poll() == []


def test_alertmanager_recheck_preserves_new_groups():
    groups = [group('old')]
    source = AlertmanagerSource({'id': 'alerts', 'url': 'http://localhost:9093'},
        http=lambda *a, **kw: (200, json.dumps(groups).encode()))
    assert source.check()[0]
    groups.append(group('new'))
    assert source.check()[0]
    assert len(source.poll()) == 1
    assert source.poll() == []
