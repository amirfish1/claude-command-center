import json
import os
from unittest.mock import patch

import pytest

from ccc_server.oncall.logs_source import LogsSource, MAX_FINDINGS, MAX_READ, FIELDS
from ccc_server.oncall.sources import _MemoryKV, _Problem


def source(tmp_path):
    path = tmp_path / 'app.log'
    path.write_text('ERROR old\n')
    kv = _MemoryKV()
    obj = LogsSource({'id': 'test', 'path': str(path)}, kv=kv)
    assert obj.check()[0]
    return obj, path, kv


def append(path, data):
    with path.open('ab') as handle:
        handle.write(data)


def test_validation_and_fields():
    for cfg in ({}, {'path': 'a', 'unit': 'b'}):
        with pytest.raises(ValueError):
            LogsSource({'id': 'test', **cfg})
    assert {f['name'] for f in FIELDS} >= {'path', 'unit', 'container'}


def test_baseline_partial_scrub_and_dedup(tmp_path):
    obj, path, kv = source(tmp_path)
    assert obj.poll() == []
    append(path, b'ERROR job 123 password=hunter')
    assert obj.poll() == []
    assert 'hunter' not in json.dumps(kv._data)
    append(path, b'2\nERROR job 456 password=other\n')
    findings = obj.poll()
    assert len(findings) == 1
    assert 'Occurrences in this batch: 2' in findings[0].text
    assert 'hunter' not in json.dumps(kv._data) + findings[0].text
    append(path, b'ERROR job 999 password=third\n')
    assert obj.poll() == []
    assert LogsSource({'id': 'test', 'path': str(path)}, kv=kv).poll() == []


def test_traceback_across_polls(tmp_path):
    obj, path, kv = source(tmp_path)
    append(path, b'Traceback (most recent call last):\n  File "app.py", line 12\n')
    assert obj.poll() == []
    append(path, b'ValueError: wrong 345\n')
    findings = obj.poll()
    assert len(findings) == 1
    assert 'ValueError' in findings[0].text


def test_rotation_copytruncate_and_utf8(tmp_path):
    obj, path, kv = source(tmp_path)
    append(path, b'ERROR original\n')
    assert len(obj.poll()) == 1
    path.rename(tmp_path / 'app.log.1')
    path.write_bytes(b'ERROR rotated \xff\n')
    assert len(obj.poll()) == 1
    path.write_bytes(b'ERROR short\n')
    assert len(obj.poll()) == 1


def test_burst_drains_and_file_read_is_bounded(tmp_path):
    obj, path, kv = source(tmp_path)
    append(path, b''.join(('ERROR unique%s\n' % chr(65 + i)).encode() for i in range(20)))
    results = []
    for _ in range(9):
        page = obj.poll()
        assert len(page) <= MAX_FINDINGS
        results += page
    assert len(results) == 20
    append(path, b'x' * (MAX_READ * 2))
    before = obj._get('position')
    assert obj.poll() == []
    assert 'exceeds' in obj.last_error
    assert obj._get('position') == before


def test_journal_cursor_and_bounded_invocations():
    kv = _MemoryKV()
    obj = LogsSource({'id': 'journal', 'unit': 'example.service'}, kv=kv)
    outputs = [b'{"__CURSOR":"a", "MESSAGE":"ERROR old"}\n',
               b'{"__CURSOR":"b", "MESSAGE":"ERROR new"}\n', b'']
    with patch('ccc_server.oncall.logs_source._command', side_effect=outputs) as command:
        assert obj.check()[0]
        assert len(obj.poll()) == 1
        assert obj.poll() == []
    assert command.call_count == 3
    assert '--after-cursor' in command.call_args_list[1].args[0]
    assert obj._get('cursor') == 'b'


def test_docker_same_timestamp_duplicates():
    stamp = '2026-01-01T00:00:01.000000001Z'
    obj = LogsSource({'id': 'docker', 'container': 'example'}, kv=_MemoryKV())
    line = (stamp + ' ERROR one\n').encode()
    with patch('ccc_server.oncall.logs_source._command', side_effect=[line, line, line + line, line + line]) as command:
        assert obj.check()[0]
        assert len(obj.poll()) == 1
        assert obj.poll() == []
    assert command.call_count == 4
    assert '--since' in command.call_args_list[1].args[0]


def test_command_failure_does_not_advance():
    obj = LogsSource({'id': 'journal', 'unit': 'example'}, kv=_MemoryKV())
    with patch('ccc_server.oncall.logs_source._command', side_effect=[b'{"__CURSOR":"a"}\n', _Problem('too large')]):
        assert obj.check()[0]
        assert obj.poll() == []
        assert obj._get('cursor') == 'a'
        assert obj.last_error == 'too large'


def test_rotation_drains_unread_old_inode_first(tmp_path):
    obj, path, kv = source(tmp_path)
    append(path, b'ERROR before rename\n')
    path.rename(tmp_path / 'app.log.1')
    path.write_bytes(b'ERROR after rename\n')
    assert 'before rename' in obj.poll()[0].text
    assert 'after rename' in obj.poll()[0].text


def test_traceback_signature_uses_frames_and_exception(tmp_path):
    obj, path, _ = source(tmp_path)
    def trace(location, error):
        return (f'Traceback (most recent call last):\n  File "{location}", line 123, in run\n{error}: failed 42\n').encode()
    append(path, trace('first.py', 'ValueError') + trace('second.py', 'TypeError'))
    first = obj.poll()
    assert len(first) == 2
    assert first[0].fingerprint != first[1].fingerprint
    append(path, trace('first.py', 'ValueError'))
    assert obj.poll() == []


def test_traceback_scrubs_partial_private_key_and_caps_bytes(tmp_path):
    obj, path, kv = source(tmp_path)
    append(path, ('Traceback (most recent call last):\n  -----BEGIN PRIVATE\n  ' + 'secretmaterial' * 1000 + '\n').encode())
    assert not obj.poll()
    state = json.dumps(kv._data)
    assert 'secretmaterial' not in state
    assert 'BEGIN PRIVATE' not in state
    append(path, ('ValueError: ' + 'é' * 10000 + '\n').encode())
    result = obj.poll()
    assert not result  # oversized individual lines fail safely


def test_bounded_command_pages_complete_records():
    import sys
    from ccc_server.oncall.logs_source import _command
    program = 'import sys; sys.stdout.write("record\\n" * 100000)'
    page = _command([sys.executable, '-c', program])
    assert page == b'record\n' * 64


def test_docker_burst_replays_same_timestamp_without_stalling():
    import sys
    import ccc_server.oncall.logs_source as module
    obj = LogsSource({'id': 'docker-burst', 'container': 'example'}, kv=_MemoryKV())
    stamp = '2026-01-01T00:00:01.000000001Z'
    obj._set('baselined', True)
    obj._set('docker', {'since': stamp, 'seen': {}})
    # More than MAX_OUTPUT bytes at the same timestamp; each record has a
    # distinct signature and no page is allowed to discard older records.
    program = ("import sys; stamp=sys.argv[1]; "
               "sys.stdout.write(''.join(stamp+' ERROR '+str(i)+' '+chr(65+i//100)+'x'*3500+'\\n' for i in range(300)))")
    real = module._command
    def fake(argv, **kw):
        return real([sys.executable, '-c', program, stamp], **kw)
    with patch.object(module, '_command', side_effect=fake):
        for _ in range(8):
            obj.poll()
        position = obj._get('docker')
    assert sum(position['seen'].values()) == 300
    assert not obj.last_error


def test_journal_burst_drains_in_order():
    import sys
    import ccc_server.oncall.logs_source as module
    obj = LogsSource({'id': 'journal-burst', 'unit': 'example'}, kv=_MemoryKV())
    obj._set('baselined', True)
    obj._set('cursor', '0')
    program = ("import sys,json; start=int(sys.argv[1]); "
               "sys.stdout.write(''.join(json.dumps({'__CURSOR':str(i),'MESSAGE':'ERROR item '+str(i)+' x'*1800})+'\\n' for i in range(start+1,301)))")
    real = module._command
    def fake(argv, **kw):
        cursor = argv[argv.index('--after-cursor') + 1]
        return real([sys.executable, '-c', program, cursor], **kw)
    with patch.object(module, '_command', side_effect=fake):
        for _ in range(7):
            obj.poll()
    assert obj._get('cursor') == '300'
    assert not obj.last_error


def test_cleanup_kills_process_group():
    import sys
    import ccc_server.oncall.logs_source as module
    real_killpg = os.killpg
    with patch.object(module.os, 'killpg', wraps=real_killpg) as kill:
        assert module._command([sys.executable, '-c', 'print("record")']) == b'record\n'
    assert kill.call_count == 1


def test_docker_baseline_with_many_historical_records():
    obj = LogsSource({'id': 'docker-baseline', 'container': 'example'}, kv=_MemoryKV())
    stamp = '2026-01-01T00:00:02.000000000Z'
    latest = (stamp + ' healthy last\n').encode()
    older = [f'2026-01-01T00:00:01.000000000Z old {i}\n'.encode() for i in range(200)]
    history = older + [latest]
    def fake(argv, **kwargs):
        if '--tail' in argv:
            return b''.join(history[-int(argv[argv.index('--tail') + 1]):])
        since = argv[argv.index('--since') + 1].encode()
        return b''.join(line for line in history if line.split(b' ', 1)[0] >= since)
    with patch('ccc_server.oncall.logs_source._command', side_effect=fake) as command:
        assert obj.check()[0]
    assert command.call_args_list[0].args[0][-3:-1] == ['--tail', '1']
    assert '--since' in command.call_args_list[1].args[0]
    assert obj._get('docker')['since'] == stamp


def test_docker_baseline_ends_at_last_snapshot():
    import ccc_server.oncall.logs_source as module
    obj = LogsSource({'id': 'docker-race', 'container': 'example'}, kv=_MemoryKV())
    stamp = '2026-01-01T00:00:02.000000000Z'
    marker = (stamp + ' healthy last\n').encode()
    new = (stamp + ' ERROR arrived during baseline\n').encode()
    def fake(argv, **kwargs):
        if '--tail' in argv:
            return marker
        records = (marker + new).splitlines(keepends=True)
        skip = kwargs.get('skip')
        return b''.join(line for line in records if not skip or not skip(line.rstrip(b'\n')))
    with patch.object(module, '_command', side_effect=fake):
        assert obj.check()[0]
        findings = obj.poll()
    assert findings == []  # both records existed before setup returned


def test_docker_baseline_rejects_over_1000_at_same_timestamp():
    obj = LogsSource({'id': 'docker-overflow', 'container': 'example'}, kv=_MemoryKV())
    stamp = '2026-01-01T00:00:02.000000000Z'
    marker = (stamp + ' marker\n').encode()
    many = (stamp + ' other\n').encode() * 1000 + marker
    with patch('ccc_server.oncall.logs_source._command', side_effect=[marker, many]):
        ok, reason = obj.check()
    assert not ok
    assert '1000' in reason
    assert not obj._get('baselined')


def test_fifo_is_refused_without_blocking(tmp_path):
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    obj = LogsSource({'id': 'fifo', 'path': str(fifo)})
    ok, reason = obj.check()
    assert not ok and 'regular file' in reason


def test_identical_existing_docker_errors_are_all_baselined():
    obj = LogsSource({'id': 'duplicate-baseline', 'container': 'example'}, kv=_MemoryKV())
    line = b'2026-01-01T00:00:02.000000000Z ERROR old\n'
    def command(argv, **kwargs):
        rows = [line] if '--tail' in argv else [line] * 10
        skip = kwargs.get('skip')
        return b''.join(row for row in rows if not skip or not skip(row.rstrip(b'\n')))
    with patch('ccc_server.oncall.logs_source._command', side_effect=command):
        assert obj.check()[0]
        assert obj.poll() == []
