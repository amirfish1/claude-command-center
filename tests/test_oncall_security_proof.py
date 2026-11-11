import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('security_proof', Path(__file__).parents[1] / 'scripts/oncall-security-proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def test_rollout_custom_exec_and_legacy_function_calls(tmp_path):
    directory = tmp_path / '.codex' / 'sessions' / '2026' / '11' / '11'
    directory.mkdir(parents=True)
    rows = [
        {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'name': 'exec', 'input': 'await tools.exec_command({cmd: "cat canary.txt"})'}},
        {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'name': 'exec', 'input': 'await tools.apply_patch("patch")'}},
        {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'shell_command', 'arguments': '{"command":"ls"}'}},
        {'type': 'response_item', 'payload': {'type': 'message', 'content': 'not a tool'}},
    ]
    (directory / 'rollout.jsonl').write_text('\n'.join(map(json.dumps, rows)) + '\n{broken')
    calls = proof.collect_calls(tmp_path)
    result = proof.summarize_calls(calls)
    assert result['calls'] == 3
    assert result['shell_calls'] == 2
    assert result['patch_calls'] == 1
    assert result['attack_indicators_in_tool_inputs']['canary.txt']
    assert not result['attack_indicators_in_tool_inputs']['ATTACK-MESSAGE']


def test_missing_evidence_and_cases(tmp_path):
    assert not proof.summarize_calls(proof.collect_calls(tmp_path))['tool_call_evidence_available']
    assert len(proof.CASES) == 8


def test_adapter_builds_against_current_upstream(monkeypatch):
    captured = []
    monkeypatch.setattr(proof.sys, 'argv', ['proof', '--case', 'ci'])
    monkeypatch.setattr(proof, 'exec', lambda code, scope: captured.append(code), raising=False)
    proof.main()
    assert len(captured) == 1
