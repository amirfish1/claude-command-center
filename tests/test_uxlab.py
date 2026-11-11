"""Focused, model-free UX lab orchestration tests. No server imports."""
import base64
import copy
import json
from pathlib import Path
import subprocess

import pytest

from uxlab.cli import main
from uxlab.core import (brief, compare, finding_key, make_plan, merge_findings,
                        new_round_path, run_round, validate_url)

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1cAAAAASUVORK5CYII=')


def finding(**overrides):
    value = dict(page='http://localhost:8000/details?variant=1#top', control='Save favorite',
                 symptom='Button does nothing', severity='blocked', steps=['Open details', 'Tap Save favorite'],
                 expected='Favorite confirmation', actual='No response', viewport='390×844', screenshot='shot-1.png')
    value.update(overrides)
    return value


class FakeEngine:
    """Engine contract exercises orchestration without agent/model subprocesses."""
    def __init__(self, fail=(), verdict='absent', emit=True):
        self.calls = []
        self.fail, self.verdict, self.emit = fail, verdict, emit

    def run(self, plan, person, folder, earlier):
        self.calls.append((copy.deepcopy(plan), copy.deepcopy(person), copy.deepcopy(earlier)))
        (folder / 'shot-1.png').write_bytes(PNG)
        if person['id'] in self.fail:
            (folder / 'result.json').write_text(json.dumps({'findings': [finding()] if self.emit else [], 'replays': []}))
            raise RuntimeError('fake timeout')
        return dict(id=person['id'], status='finished', success=True, worked='<script>alert(1)</script>',
                    findings=[finding(), finding()] if self.emit else [],
                    replays=[dict(key=f['key'], verdict=self.verdict, steps=f['steps'],
                                  expected='Old behavior checked', actual='Replayed old steps', screenshot='shot-1.png') for f in earlier])


@pytest.mark.parametrize('url', ['https://example.org/', 'file:///tmp/x', 'http://localhost.evil/',
                                  'http://user:pass@localhost/', 'http://127.0.0.2/', 'javascript:alert(1)'])
def test_reject_urls(url):
    with pytest.raises(ValueError):
        make_plan(url, [])


def test_exact_hosts_devices_goals():
    plan = make_plan('http://[::1]:8000/', ['one', 'two'], people=4)
    assert [p['device'] for p in plan['people']] == ['desktop', 'phone', 'desktop', 'phone']
    assert [p['goal'] for p in plan['people']] == ['one', 'two', 'one', 'two']
    validate_url('https://example.org:8443/', ['example.org'])
    for host in ('*.example.org', 'https://example.org', 'example.org:8000', 'a/b'):
        with pytest.raises(ValueError):
            make_plan('http://localhost/', [], hosts=[host])
    with pytest.raises(ValueError):
        make_plan('http://localhost/', ['one', 'two'], people=1)
    with pytest.raises(ValueError):
        make_plan('http://localhost/', [], people=0)


def test_deterministic_dedup_keeps_evidence_counts_people():
    a = finding()
    b = finding(page='http://LOCALHOST:8000/details?variant=2', control='SAVE   favorite!', symptom='button does nothing!')
    assert finding_key(a) == finding_key(b)
    result = merge_findings([{'id': 'a', 'findings': [a, b]}, {'id': 'b', 'findings': [b]}])
    assert len(result) == 1
    assert result[0]['confirmations'] == 2
    assert len(result[0]['evidence']) == 3
    assert finding_key(a) != finding_key(finding(page='http://localhost:8001/details'))
    assert finding_key(a) != finding_key(finding(control='Other'))


def test_absence_requires_all_explicit_successful_replays():
    old = merge_findings([{'id': 'a', 'findings': [finding()]}, {'id': 'b', 'findings': [finding()]}])
    key = old[0]['key']
    results = [{'id': t, 'status': 'finished', 'replays': [{'key': key, 'verdict': 'absent'}]} for t in ('a', 'b')]
    assert compare(old, results)[0]['status'] == 'absent on explicit replay'
    results[1]['status'] = 'failed'
    assert compare(old, results)[0]['status'] == 'not checked'
    results[0]['replays'][0]['verdict'] = 'present'
    assert compare(old, results)[0]['status'] == 'present'
    assert compare(old, [])[0]['status'] == 'not checked'


def test_fake_workflow_report_and_partial_failures(tmp_path):
    engine = FakeEngine(fail=['tester-2'])
    plan = make_plan('http://localhost:8000/', ['Try it'], people=2)
    folder = tmp_path / 'first'
    out = run_round(plan, folder, engine=engine)
    assert [r['status'] for r in out['results']] == ['finished', 'failed']
    assert out['findings'][0]['confirmations'] == 2
    assert len(out['findings'][0]['evidence']) == 3
    report = (folder / 'report.html').read_text()
    assert '<script>' not in report
    assert '&lt;script&gt;' in report
    assert 'data:image/png;base64,' in report
    assert '390×844' in report and 'No response' in report
    assert not (folder / 'filing.json').exists()
    with pytest.raises(FileExistsError):
        run_round(plan, folder, engine=engine)
    assert len(engine.calls) == 2


def test_rerun_plan_and_unchecked_baseline_survive(tmp_path):
    plan = make_plan('http://localhost/', ['Try'], people=2, device='phone', risky=True)
    first = run_round(plan, tmp_path / 'one', engine=FakeEngine())
    rerun = FakeEngine(emit=False)
    child = new_round_path(tmp_path / 'one')
    second = run_round(plan, child, engine=rerun, previous=first['findings'])
    assert second['comparison'][0]['status'] == 'absent on explicit replay'
    assert all(call[0] == plan for call in rerun.calls)
    assert all(call[2][0]['steps'] == first['findings'][0]['steps'] for call in rerun.calls)
    assert json.loads((child / 'plan.json').read_text()) == plan
    assert json.loads((child / 'replay-findings.json').read_text())
    failed = run_round(plan, child / 'failed', engine=FakeEngine(fail=['tester-1'], emit=False), previous=first['findings'])
    assert failed['comparison'][0]['status'] == 'not checked'
    assert child != new_round_path(tmp_path / 'one')


@pytest.mark.parametrize('extra', [['--no-browser-sandbox'], ['--allow-risky'], ['--people', '4'], ['--phone'], ['--allow-host', 'example.org'],
                                  ['--goal', 'other'], ['--timeout', '100'], ['--steps', '20'], ['--model', 'other'],
                                  ['--output', 'elsewhere'], ['http://localhost/']])
def test_again_rejects_any_plan_override(tmp_path, extra):
    with pytest.raises(SystemExit) as exc:
        main(['run', '--again', str(tmp_path)] + extra)
    assert exc.value.code == 2


def test_brief_includes_old_steps_not_seed_manifest():
    plan = make_plan('http://localhost/', ['Explore'], people=1)
    prompt = brief(plan, plan['people'][0], [finding()])
    assert 'Tap Save favorite' in prompt
    assert 'seeds.json' not in prompt


def test_node_safety_and_protocol_contract():
    completed = subprocess.run(['nice', '-n', '15', 'node', '--test', 'tests/uxlab-browser.test.js'],
                               text=True, capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_codex_minimal_configuration(tmp_path):
    from uxlab.core import codex_config
    text = codex_config(tmp_path / 'spec.json')
    assert 'model_reasoning_effort = "medium"' in text
    assert 'web_search = "disabled"' in text
    assert 'project_doc_max_bytes = 0' in text
    for feature in ('shell_tool', 'apply_patch_freeform', 'apps', 'multi_agent', 'unified_exec', 'js_repl'):
        assert feature + ' = false' in text
    assert text.count('[mcp_servers.') == 1
    assert '[mcp_servers.uxlab]' in text
    assert 'default_tools_approval_mode = "approve"' in text
    assert 'PUPPETEER_CACHE_DIR' in text


def test_cli_again_uses_exact_plan_without_model_or_queue(tmp_path, monkeypatch):
    import uxlab.cli as cli
    plan = make_plan('http://localhost/', ['Original goal'], people=2, risky=True, device='phone')
    first = run_round(plan, tmp_path / 'first', engine=FakeEngine())
    seen = []
    def fake_round(saved, folder, previous, queue):
        seen.append((saved, folder, previous, queue))
        return {'results': [{'status': 'finished'}]}
    monkeypatch.setattr(cli, 'run_round', fake_round)
    assert cli.main(['run', '--again', str(tmp_path / 'first')]) == 0
    assert seen[0][0] == plan
    assert seen[0][1].parent == tmp_path / 'first'
    assert seen[0][2][0]['key'] == first['findings'][0]['key']
    assert seen[0][3] is None


def test_queue_failure_keeps_report_and_filing_log(tmp_path):
    # Replace our submission boundary, never run or emulate the external wt CLI.
    attempted = []
    def unavailable(command, **kwargs):
        attempted.append(command)
        raise FileNotFoundError('queue tool unavailable')
    outcome = run_round(make_plan('http://localhost/', ['Try'], people=1), tmp_path / 'run',
                        engine=FakeEngine(), queue='UX-TEST', queue_submit=unavailable)
    folder = Path(outcome['folder'])
    assert (folder / 'report.html').is_file()
    filed = json.loads((folder / 'filing.json').read_text())
    assert not filed[0]['ok'] and 'unavailable' in filed[0]['error']
    assert attempted[0][:4] == ['wt', 'add', '-q', 'UX-TEST']
    assert '--no-dispatch' in attempted[0]
    assert '--dedupe-key' in attempted[0]


def test_new_evidence_overrides_absent_replay():
    old = merge_findings([{'id': 'tester-1', 'findings': [finding()]}])
    result = {'id': 'tester-1', 'status': 'finished', 'findings': [finding()],
              'replays': [{'key': old[0]['key'], 'verdict': 'absent'}]}
    assert compare(old, [result])[0]['status'] == 'present'


def test_saved_plan_cannot_escape_artifact_folder(tmp_path):
    plan = make_plan('http://localhost/', ['Try'], people=1)
    plan['people'][0]['id'] = '../escape'
    with pytest.raises(ValueError, match='invalid tester'):
        run_round(plan, tmp_path / 'trial', engine=FakeEngine())
    assert not (tmp_path / 'escape').exists()


def test_report_headline_counts_goal_failure_not_just_session_failure(tmp_path):
    class Unsuccessful(FakeEngine):
        def run(self, *args):
            result = super().run(*args)
            result['success'] = False
            return result
    run_round(make_plan('http://localhost/', ['Try'], people=1), tmp_path / 'report', engine=Unsuccessful())
    assert '1 tester tried 1 goal. 1 could not finish.' in (tmp_path / 'report/report.html').read_text()
