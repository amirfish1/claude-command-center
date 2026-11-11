"""Headless setup and native service rendering, without touching host services."""
import pytest
from ccc_server.service import unit, quote
from tests.test_oncall_cli import box  # noqa: F401


def test_setup_no_tty_requires_explicit_preview_consent(box, capsys):
    assert box.run('setup') == 1
    assert '--yes' in capsys.readouterr().err
    assert not box.flag and not box.asked


def test_yes_suppresses_even_interactive_prompts(box, tmp_path):
    assert box.run('setup', '--yes', '--repo', str(tmp_path), '--queue', 'PROOF',
                   '--channel', 'command', '--set', 'send=cat', interactive=True) == 0
    assert not box.asked


def test_missing_secret_fails_without_prompt(box, tmp_path, capsys):
    assert box.run('setup', '--yes', '--repo', str(tmp_path), '--channel', 'telegram') == 1
    assert '--vault' in capsys.readouterr().err
    assert not box.asked


def test_unit_quotes_paths_and_supervises_group():
    text = unit('/opt/space dir', '/usr/bin/python3', '/home/user', '/usr/bin:/bin')
    assert '"/opt/space dir/scripts/oncall-supervisor.py"' in text
    assert 'KillMode=control-group' in text
    assert 'WantedBy=default.target' in text
    assert 'WorkingDirectory=/home/user\n' in text
    assert 'Restart=on-failure' in text
    assert '0.0.0.0' not in text
    assert quote('a%b$c"') == '"a%%b$c\\""'


@pytest.mark.parametrize('value', ['a\nb', 'a\rb', 'a\0b'])
def test_unit_refuses_line_injection(value):
    with pytest.raises(ValueError):
        unit(value, '/usr/bin/python3', '/home/user', '/bin')
