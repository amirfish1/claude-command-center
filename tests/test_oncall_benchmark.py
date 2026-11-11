"""Check benchmark oracles without models, containers or queue services."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('oncall_bench_cases', ROOT/'scripts/oncall-bench/cases.py')
cases = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cases
spec.loader.exec_module(cases)


def test_case_coverage():
    values = list(cases.CASES.values())
    assert len(values) >= 24
    assert sum(c.kind == 'owner' for c in values) >= 4
    assert sum(c.kind == 'minor' for c in values) >= 4
    assert sum(c.kind == 'hard' for c in values) >= 3
    assert len(cases.QUICK) == 4
    assert {'python3','node','sh'} <= {c.command[0] for c in values}


def test_seed_oracles(tmp_path):
    for c in cases.CASES.values():
        assert shutil.which(c.command[0]), f'Required runtime: {c.command[0]}'
        project = tmp_path/c.id
        project.mkdir()
        for name, content in c.files.items(): (project/name).write_text(content)
        visible = subprocess.run(c.command, cwd=project, capture_output=True, timeout=10)
        assert (visible.returncode == 0) == (c.kind in ('missing','wrong')), c.id
        suffix = {'python3':'.py','node':'.js','sh':'.sh'}[c.command[0]]
        hidden = project/('test_hidden'+suffix)
        hidden.write_text(c.hidden)
        result = subprocess.run([c.command[0],str(hidden)],cwd=project,capture_output=True,timeout=10)
        assert (result.returncode == 0) == (c.kind=='wrong'), c.id
