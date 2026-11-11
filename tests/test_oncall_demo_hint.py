from urllib.error import HTTPError
import pytest
from ccc_server.oncall import cli


@pytest.mark.parametrize('argv', [[], ['status']])
def test_preview_off_hints_at_demo(argv, capsys):
    def off(*args, **kwargs):
        raise HTTPError('http://127.0.0.1/api/oncall/status', 404, 'preview off', {}, None)
    assert cli.run(argv, base='http://127.0.0.1', get=off, post=None) == 1
    text = capsys.readouterr().err
    assert 'ccc oncall demo' in text
    assert len(text.splitlines()) == 1
