"""Guard the real test-container options without starting external services."""
import ast
from pathlib import Path
import re

import pytest


@pytest.mark.parametrize('script, readiness_seconds', [
    ('scripts/oncall-bench/worker.py', 90),
    ('scripts/oncall-proof.py', 60),
])
def test_greenmail_startup_timeout_fits_readiness_budget(script, readiness_seconds):
    tree = ast.parse((Path(__file__).resolve().parents[1] / script).read_text())
    containers = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == 'container'
        and node.args and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.startswith('greenmail/standalone:')
    ]
    assert len(containers) == 1
    extra = next(kw.value for kw in containers[0].keywords if kw.arg == 'extra')
    options = [node.value for node in extra.elts
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and node.value.startswith('GREENMAIL_OPTS=')]
    assert len(options) == 1
    timeouts = [option.split('=', 1)[1] for option in options[0].split()
                if option.startswith('-Dgreenmail.startup.timeout=')]
    assert len(timeouts) == 1
    assert re.fullmatch(r'[0-9]+', timeouts[0])

    # Read the actual readiness deadline too; keep its existing budget fixed.
    readiness_loops = [node for node in ast.walk(tree)
                       if isinstance(node, ast.For)
                       and ast.unparse(node.iter) == '(http, smtp, imap)']
    assert len(readiness_loops) == 1
    budgets = [
        node.value.right.value for node in ast.walk(readiness_loops[0])
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'deadline'
                for target in node.targets)
        and isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Add)
        and isinstance(node.value.left, ast.Call)
        and ast.unparse(node.value.left) == 'time.monotonic()'
        and isinstance(node.value.right, ast.Constant)
    ]
    assert budgets == [readiness_seconds]
    assert readiness_seconds >= 60
    assert 30000 <= int(timeouts[0]) < readiness_seconds * 1000
