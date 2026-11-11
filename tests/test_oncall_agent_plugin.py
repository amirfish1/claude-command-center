"""No credentials, real services, or model calls are used by these checks."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / 'plugins/oncall/scripts/hook.py'


def env_for(tmp_path, with_ccc=False):
    bins = tmp_path / 'bin'
    bins.mkdir(exist_ok=True)
    if with_ccc:
        (bins / 'ccc').symlink_to(REPO / 'ccc')
    return {'HOME': str(tmp_path), 'PATH': str(bins), 'CCC_SERVER': 'http://127.0.0.1:1',
            'CCC_STATE_DIR': str(tmp_path / 'state'), 'WT_NO_AUTOSTART': '1'}


@pytest.mark.parametrize('event', ['SessionStart', 'Stop', 'PermissionRequest', 'Notification'])
def test_missing_ccc_fast(tmp_path, event):
    start = time.monotonic()
    result = subprocess.run([sys.executable, str(HOOK), event], input='{}', text=True,
                            capture_output=True, env=env_for(tmp_path), timeout=2)
    assert result.returncode == 0
    assert not result.stderr
    assert time.monotonic() - start < 2
    if event == 'SessionStart':
        assert 'Install CCC' in json.loads(result.stdout)['systemMessage']
    else:
        assert result.stdout == ''


@pytest.mark.parametrize('event', ['Stop', 'PermissionRequest', 'Notification'])
def test_recorded_payload_service_down(tmp_path, event):
    payload = {'session_id': 'test-session-1234', 'hook_event_name': event,
               'cwd': str(tmp_path), 'tool_name': 'Bash',
               'tool_input': {'command': 'echo example'}, 'notification_type': 'permission_prompt',
               'type': 'permission_prompt', 'message': 'Permission needed',
               'last_assistant_message': 'Should I use blue?'}
    result = subprocess.run([sys.executable, str(HOOK), event], input=json.dumps(payload),
                            text=True, capture_output=True, env=env_for(tmp_path, True), timeout=2)
    assert result.returncode == 0 and not result.stderr and not result.stdout
    live = tmp_path / '.claude/command-center/live-state'
    filename = 'test-session-1234' + ('.json' if event == 'Stop' else '_needs_approval.json')
    assert (live / filename).exists()


@pytest.mark.parametrize('payload', ['{', '[]', '{"session_id":"../../escape"}'])
def test_bad_payload_fails_open(tmp_path, payload):
    result = subprocess.run([sys.executable, str(HOOK), 'Stop'], input=payload, text=True,
                            capture_output=True, env=env_for(tmp_path, True), timeout=2)
    assert result.returncode == 0 and result.stdout == '' and result.stderr == ''


def test_codex_install_idempotent_preserves_config(tmp_path):
    env = env_for(tmp_path)
    env['CODEX_HOME'] = str(tmp_path / 'codex')
    home = Path(env['CODEX_HOME'])
    home.mkdir()
    config = home / 'config.toml'
    config.write_text('model = "example"\n')
    prior = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo existing'}]}]}}
    (home / 'hooks.json').write_text(json.dumps(prior))
    for _ in range(2):
        subprocess.run([sys.executable, str(REPO / 'ccc'), 'oncall', 'install-codex'],
                       env=env, check=True, capture_output=True, timeout=5)
    assert config.read_text() == 'model = "example"\n'
    installed = json.loads((home / 'hooks.json').read_text())
    assert len(installed['hooks']['Stop']) == 2
    assert installed['hooks']['Stop'][0] == prior['hooks']['Stop'][0]


def test_service_refuses_remote_address():
    from ccc_server.oncall.agent_install import local_url
    assert local_url('http://127.0.0.1:1234')
    for url in ['http://example.test', 'https://127.0.0.1', 'http://user@127.0.0.1',
                'http://127.0.0.1/api', 'http://0.0.0.0:1234']:
        assert not local_url(url)


def test_permission_clears_after_tool(tmp_path):
    env = env_for(tmp_path, True)
    payload = json.dumps({'session_id': 'test-session-1234', 'tool_name': 'Bash',
                          'tool_input': {'command': 'echo example'}})
    for event in ['PermissionRequest', 'PostToolUse']:
        result = subprocess.run([sys.executable, str(HOOK), event], input=payload,
                                text=True, capture_output=True, env=env, timeout=2)
        assert result.returncode == 0 and not result.stdout and not result.stderr
    assert not (tmp_path / '.claude/command-center/live-state/test-session-1234_needs_approval.json').exists()


def test_idle_notification_is_not_permission(tmp_path):
    env = env_for(tmp_path, True)
    payload = json.dumps({'session_id': 'test-session-1234', 'notification_type': 'idle_prompt'})
    subprocess.run([sys.executable, str(HOOK), 'Notification'], input=payload, text=True,
                   env=env, check=True, timeout=2)
    live = tmp_path / '.claude/command-center/live-state'
    assert (live / 'test-session-1234.json').exists()
    assert not (live / 'test-session-1234_needs_approval.json').exists()


def test_idle_signal_is_plugin_only_and_cache_tracks_option(tmp_path):
    from ccc_server.oncall.session_signals import question
    class Store:
        data = {}
        def kv_get(self, key):
            return self.data.get(key)
        def kv_set(self, key, value):
            self.data[key] = value
    transcript = tmp_path / 'turn.jsonl'
    transcript.write_text(json.dumps({'type': 'assistant', 'uuid': 'test-turn',
                          'message': {'content': 'Finished the requested work.'}}) + '\n')
    store = Store()
    assert question(store, 'test-session', transcript) is None
    signal = question(store, 'test-session', transcript, notify_idle=True)
    assert signal['body'] == 'Turn finished. Finished the requested work.'
    assert question(store, 'test-session', transcript) is None
