"""Log file arguments belong to the caller's working directory."""
import pytest
from tests.test_oncall_cli import Box


@pytest.mark.parametrize('arguments', [('--path', 'app.log'), ('--set', 'path=app.log')])
def test_relative_file_is_resolved_before_sending_to_server(tmp_path, monkeypatch, arguments):
    box = Box(tmp_path / 'state', monkeypatch)
    box.flag = True
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'app.log').write_text('ERROR old\n')
    monkeypatch.chdir(project)
    assert box.run('source', 'add', 'logs', *arguments) == 0
    entry, source = next(iter(box.eng.sources().values()))
    assert entry['path'] == str(project / 'app.log')
    monkeypatch.chdir(tmp_path)
    with (project / 'app.log').open('a') as stream:
        stream.write('ERROR new\n')
    assert len(source.poll()) == 1
    assert source.poll() == []
