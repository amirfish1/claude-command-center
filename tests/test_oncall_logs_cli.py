"""The short log flags use the real CLI, API, and file baseline."""
from tests.test_oncall_cli import Box


def test_logs_file_short_flag_baselines_at_add(tmp_path, monkeypatch):
    box = Box(tmp_path / 'state', monkeypatch)
    box.flag = True
    logfile = tmp_path / 'app.log'
    logfile.write_text('ERROR old\n')
    assert box.run('source', 'add', 'logs', '--path', str(logfile)) == 0
    entry, source = next(iter(box.eng.sources().values()))
    assert source.poll() == []
    with logfile.open('a') as stream:
        stream.write('ERROR new\n')
    assert len(source.poll()) == 1
    assert source.poll() == []
    assert not box.asked


def test_logs_exclusive_target_error_is_specific(tmp_path, monkeypatch, capsys):
    box = Box(tmp_path, monkeypatch)
    box.flag = True
    assert box.run('source', 'add', 'logs', '--unit', 'example.service', '--container', 'sample') == 1
    assert 'exactly one' in capsys.readouterr().err
