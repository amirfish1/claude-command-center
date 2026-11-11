"""Fast checks of the guide transcript format; no service or account needed."""
import importlib.util
from pathlib import Path
import sys
import pytest

spec = importlib.util.spec_from_file_location('check_guides', Path(__file__).resolve().parents[1] / 'scripts/check-guides.py')
guides = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guides
spec.loader.exec_module(guides)


def test_extract_and_trimmed_order():
    checks = guides.extract('```sh\nignored\n```\n```console checked slow\n$ example\nfirst\nlast\n```\n')
    assert len(checks) == 1
    assert checks[0].command == 'example' and checks[0].slow
    assert guides.matches('intro\nfirst\nomitted\nlast\n', checks[0].expected)
    assert not guides.matches('last\nfirst\n', checks[0].expected)
    assert not guides.matches('first changed\nlast\n', checks[0].expected)


@pytest.mark.parametrize('body', ['not a command\nout', '$ command', '$ one\n$ two\nout'])
def test_reject_ambiguous_blocks(body):
    with pytest.raises(ValueError):
        guides.extract('```console checked\n' + body + '\n```\n')


def test_reject_unclosed_block():
    with pytest.raises(ValueError):
        guides.extract('```console checked\n$ command\nout\n')
