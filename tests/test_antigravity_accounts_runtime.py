"""Credential-free auth routing, including delayed native IDs and child envs."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
import sys
from unittest import mock

import pytest


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    server = importlib.import_module('server')
    from ccc_server import antigravity_accounts as accounts
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path / 'state')
    monkeypatch.setattr(server, 'ANTIGRAVITY_CLI_CONVERSATIONS', tmp_path / 'native' / 'conversations')
    monkeypatch.setattr(server, 'ANTIGRAVITY_CLI_BRAIN', tmp_path / 'native' / 'brain')
    monkeypatch.setattr(server, '_read_antigravity_cli_settings', lambda: (
        {'gcp': {'project': 'test-project', 'location': 'global'}, 'model': 'old'}, None))
    for key in accounts._CLOUD_ENV:
        monkeypatch.delenv(key, raising=False)
    return accounts, server


def test_selection_inheritance_persistence_and_dedupe(runtime):
    accounts, _ = runtime
    accounts.bind('parent', 'secondary')
    assert accounts.spawn_account({'parent_session_id': 'parent'}) == 'secondary'
    for value in ('', '__current__', 'primary'):
        assert accounts.spawn_account({'antigravity_account_id': value, 'parent_session_id': 'parent'}) == 'primary'
    for value in ('unknown', None, 42):
        with pytest.raises(ValueError):
            accounts.spawn_account({'antigravity_account_id': value})
    accounts.bind_pending('/test/log', 'secondary')
    accounts = importlib.reload(accounts)
    accounts.finalize_pending('/test/log', 'native')
    assert accounts.session_account('native') == 'secondary'
    assert accounts.dedupe_matches({'parent_session_id': 'parent'}, 'native')
    assert not accounts.dedupe_matches({'antigravity_account_id': 'primary'}, 'native')
    with pytest.raises(ValueError):
        accounts.bind('native', 'primary')
    assert accounts.public_payload('native')['account_id'] == 'secondary'


def test_concurrent_settings_and_binding_isolation(runtime):
    accounts, _ = runtime
    before = dict(os.environ)
    def launch(i):
        selected = 'primary' if i % 2 else 'secondary'
        cmd = accounts.configure_command(['agy'], selected, f'model-{i}')
        accounts.bind_pending(f'log-{i}', selected)
        accounts.finalize_pending(f'log-{i}', f'sid-{i}')
        return selected, json.loads(cmd[-1]), accounts.session_account(f'sid-{i}')
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(launch, range(24)))
    for i, (selected, settings, persisted) in enumerate(rows):
        assert persisted == selected
        assert settings['model'] == f'model-{i}'
        assert 'auth' not in settings
        assert bool(settings['gcp']['project']) == (selected == 'primary')
    assert dict(os.environ) == before


def test_cloud_adc_and_key_are_distinct(runtime, monkeypatch):
    accounts, server = runtime
    assert accounts.launch_settings('primary')['modelProvider'] == ''
    monkeypatch.setenv('AGY_ADC_AUTH', 'true')
    assert accounts.launch_env('primary')['AGY_ADC_AUTH'] == 'true'
    monkeypatch.setattr(server, '_read_antigravity_cli_settings', lambda: ({'modelProvider': 'gemini'}, None))
    assert accounts.launch_settings('primary')['modelProvider'] == 'gemini'
    source = {key: 'test-credential' for key in accounts._CLOUD_ENV}
    source['PATH'] = '/usr/bin'
    env = accounts.launch_env('secondary', source)
    assert env['PATH'] == '/usr/bin'
    assert env['AGY_ADC_AUTH'] == 'false'
    assert env['ANTIGRAVITY_CREDENTIAL_SCOPE'] == 'ccc-antigravity-secondary'
    assert env['ANTIGRAVITY_CONFIG_REL_DIR'] == 'ccc-antigravity-secondary/config'
    assert all(key not in env for key in accounts._CLOUD_ENV - {'AGY_ADC_AUTH'})
    assert source['GEMINI_API_KEY'] == 'test-credential'
    overlay = accounts.launch_settings('secondary')
    assert overlay['gcp'] == {'project': '', 'location': ''}
    assert 'test-credential' not in json.dumps(overlay)


def test_delayed_spawn_finalizes_binding(runtime):
    accounts, server = runtime
    accounts.bind_pending('spawn.log', 'secondary')
    entry = {'engine': 'antigravity', 'log': 'spawn.log', 'pid': 100}
    with mock.patch.object(server, '_update_spawn_session_id_in_registry'), \
         mock.patch('ccc_server.engines._antigravity_cli_log_meta', return_value={'session_id': 'native'}):
        assert server._spawn_session_id_from_entry(entry) == 'native'
    assert accounts.session_account('native') == 'secondary'


def test_stream_receives_explicit_environment(runtime, tmp_path):
    accounts, _ = runtime
    from ccc_server import antigravity_stream as stream
    env = accounts.launch_env('secondary', {'PATH': os.environ.get('PATH', ''), 'GEMINI_API_KEY': 'test-key'})
    script = 'import os,json; print(json.dumps({"event":"result","result":{"key":os.getenv("GEMINI_API_KEY"),"adc":os.getenv("AGY_ADC_AUTH")}}),flush=True)'
    sp = stream.spawn('env-test', [sys.executable, '-c', script], str(tmp_path), str(tmp_path / 'log'), ('', (), 'secondary'), env=env)
    sp.proc.wait(timeout=5)
    sp.proc.stdout.close()
    stream.retire('env-test')
    assert 'GEMINI_API_KEY' not in env


def test_spawn_uses_child_settings_without_global_model_write(runtime, tmp_path):
    accounts, server = runtime
    proc = mock.Mock(pid=12345)
    proc.poll.return_value = None
    with mock.patch.object(server, '_resolve_antigravity_bin', return_value={'available': True, 'bin': '/test/agy'}), \
         mock.patch.object(server, '_set_antigravity_cli_model') as write_global, \
         mock.patch.object(server, '_record_spawn_to_registry'), \
         mock.patch('ccc_server.engines._antigravity_popen', return_value=proc) as popen, \
         mock.patch('ccc_server.engines._finalize_spawn_response', side_effect=lambda resp, *_: resp), \
         mock.patch.object(server, '_spawned_sessions', []):
        for selected in ('primary', 'secondary'):
            result = server.spawn_session_antigravity('test task', repo_path=str(tmp_path), model='test-model', antigravity_account_id=selected)
            assert result['ok']
            settings = json.loads(popen.call_args.args[0][popen.call_args.args[0].index('--settings') + 1])
            assert settings['model'] == 'test-model'
            assert 'auth' not in settings
            assert popen.call_args.kwargs['env']['CCC_ANTIGRAVITY_ACCOUNT_ID'] == selected
        write_global.assert_not_called()
        for entry in server._spawned_sessions:
            entry['log_fh'].close()


def test_secondary_home_shares_only_native_conversations(runtime):
    accounts, server = runtime
    from pathlib import Path
    env = accounts.launch_env('secondary', {'PATH': '/test/bin', 'XDG_CONFIG_HOME': '/old/config', 'ANTIGRAVITY_GEMINI_DIR': '/old/gemini', 'GEMINI_DIR': '/old/alias', 'AGY_LLM_GATEWAY_CUSTOM': 'test-value'})
    home = Path(env['HOME'])
    cli = home / '.gemini/antigravity-cli'
    assert (cli / 'conversations').resolve() == server.ANTIGRAVITY_CLI_CONVERSATIONS
    assert (cli / 'brain').resolve() == server.ANTIGRAVITY_CLI_BRAIN
    assert not (cli / 'settings.json').exists()
    assert not (home / '.gemini/config').exists()
    assert env['PATH'] == '/test/bin'
    assert 'XDG_CONFIG_HOME' not in env
    assert 'ANTIGRAVITY_GEMINI_DIR' not in env
    assert 'GEMINI_DIR' not in env
    assert 'AGY_LLM_GATEWAY_CUSTOM' not in env


def test_persisted_secondary_resume_routes_same_account(runtime, tmp_path):
    accounts, server = runtime
    from ccc_server import engines, antigravity_stream
    accounts.bind('resume-native', 'secondary')
    accounts = importlib.reload(accounts)
    assert accounts.session_account('resume-native') == 'secondary'
    proc = mock.Mock(pid=12346)
    with mock.patch.object(server, '_antigravity_cli_conversation_path', return_value=None), \
         mock.patch.object(server, '_antigravity_app_conversation_path', return_value=None), \
         mock.patch.object(server, '_resolve_antigravity_bin', return_value={'available': True, 'bin': '/test/agy'}), \
         mock.patch.object(antigravity_stream, 'state', return_value=None), \
         mock.patch.object(antigravity_stream, 'enabled', return_value=False), \
         mock.patch.object(engines, '_antigravity_terminal_holder_pid', return_value=None), \
         mock.patch.object(engines, '_antigravity_live_resume_pid', return_value=None), \
         mock.patch.object(engines.antigravity_ls, 'send', return_value=None), \
         mock.patch.object(server, '_spawned_sessions', []), \
         mock.patch.object(server, '_spawn_registry_entry_for_session', return_value={'cwd': str(tmp_path)}), \
         mock.patch.object(server, '_get_session_override', return_value={'model': 'test-resume-model'}), \
         mock.patch.object(server, '_set_antigravity_cli_model') as write_global, \
         mock.patch.object(server, '_record_spawn_to_registry'), \
         mock.patch.object(engines, '_antigravity_popen', return_value=proc) as popen:
        result = server.resume_session_antigravity('resume-native', 'test continuation')
        assert result['ok']
        cmd = popen.call_args.args[0]
        assert cmd[cmd.index('--conversation') + 1] == 'resume-native'
        assert json.loads(cmd[cmd.index('--settings') + 1])['model'] == 'test-resume-model'
        assert popen.call_args.kwargs['env']['CCC_ANTIGRAVITY_ACCOUNT_ID'] == 'secondary'
        write_global.assert_not_called()
        for entry in server._spawned_sessions:
            entry['log_fh'].close()


def test_secondary_registers_in_original_ls_directory(runtime, tmp_path, monkeypatch):
    accounts, server = runtime
    from ccc_server import antigravity_ls
    env = accounts.launch_env('secondary', {'PATH': '/test/bin'})
    monkeypatch.setenv('HOME', env['HOME'])
    cid = '00000000-0000-4000-8000-000000000001'
    env.update(ANTIGRAVITY_CONVERSATION_ID=cid, ANTIGRAVITY_LS_ADDRESS='127.0.0.1:12345', ANTIGRAVITY_CSRF_TOKEN='test-csrf-value')
    assert antigravity_ls.register_from_env(env) == cid
    entry = antigravity_ls.lookup(cid, server.COMMAND_CENTER_STATE_DIR / 'agy-ls')
    assert entry['address'] == '127.0.0.1:12345'


def test_terminal_carries_secondary_home_and_safe_settings(runtime, tmp_path):
    accounts, server = runtime
    with mock.patch('ccc_server.engines.sys.platform', 'darwin'), \
         mock.patch.object(server, '_resolve_antigravity_bin', return_value={'available': True, 'bin': '/test/agy'}), \
         mock.patch('ccc_server.engines.subprocess.Popen', return_value=mock.Mock(pid=12347)) as popen:
        result = server.launch_antigravity_terminal(repo_path=str(tmp_path), terminal_app='Terminal', antigravity_account_id='secondary')
        assert result['ok']
        script = popen.call_args.args[0][-1]
        assert 'ccc-antigravity-secondary' in script
        assert 'HOME=' in script
        assert '--settings' in script
        assert 'modelProvider' in script
        assert popen.call_args.kwargs['env']['CCC_ANTIGRAVITY_ACCOUNT_ID'] == 'secondary'
        popen.call_args.kwargs['stdout'].close()


def test_primary_selector_marker_excludes_credentials_and_survives_nesting(runtime):
    accounts, _ = runtime
    original = {'HOME': '/original/home', 'AGY_ADC_AUTH': 'true',
                'GOOGLE_GENAI_USE_VERTEXAI': 'true',
                'GOOGLE_APPLICATION_CREDENTIALS': '/original/adc.json',
                'GEMINI_API_KEY': 'test-secret', 'AGY_LLM_GATEWAY_API_KEY': 'test-gateway-secret',
                'ANTIGRAVITY_CREDENTIAL_SCOPE': 'original-scope'}
    child = accounts.launch_env('secondary', original)
    marker = child['CCC_ANTIGRAVITY_PRIMARY_SELECTORS']
    assert 'test-secret' not in marker
    assert 'test-gateway-secret' not in marker
    assert json.loads(marker)['GOOGLE_APPLICATION_CREDENTIALS'] == '/original/adc.json'
    assert child['CCC_ANTIGRAVITY_PRIMARY_HOME'] == '/original/home'
    assert json.loads(child['CCC_ANTIGRAVITY_PRIMARY_REQUIRED_ENV']) == ['AGY_LLM_GATEWAY_API_KEY', 'GEMINI_API_KEY']
    grandchild = accounts.launch_env('secondary', child)
    assert grandchild['CCC_ANTIGRAVITY_PRIMARY_SELECTORS'] == marker
    assert grandchild['CCC_ANTIGRAVITY_PRIMARY_HOME'] == '/original/home'


def test_pending_secondary_dedupe_uses_persisted_log_identity(runtime):
    accounts, _ = runtime
    accounts.bind_pending('pending-secondary.log', 'secondary')
    accounts = importlib.reload(accounts)
    assert accounts.pending_account('pending-secondary.log') == 'secondary'
    assert accounts.pending_account('unknown.log') is None
    assert not accounts.dedupe_matches({'antigravity_account_id': 'primary'}, '', 'unknown.log')
    assert accounts.dedupe_matches({'antigravity_account_id': 'secondary'}, '', 'pending-secondary.log')
    assert not accounts.dedupe_matches({'antigravity_account_id': 'primary'}, '', 'pending-secondary.log')
    assert accounts.dedupe_matches({'antigravity_account_id': 'primary'}, '')


def test_secondary_preserves_native_watchtower_queues_and_defaults(runtime, tmp_path, monkeypatch):
    accounts, server = runtime
    from pathlib import Path
    native_home = tmp_path / 'native-home'
    native_config = tmp_path / 'native-data' / 'queue-config.json'
    monkeypatch.setattr(server, '_wt_config_path', lambda: native_config)
    env = accounts.launch_env('secondary', {'HOME': str(native_home), 'PATH': '/test/bin',
                                          'XDG_DATA_HOME': str(tmp_path / 'native-data'),
                                          'CCC_SERVER': 'http://127.0.0.1:12345'})
    assert (Path(env['HOME']) / '.watchtower').resolve() == native_home / '.watchtower'
    assert env['WATCHTOWER_DATA_DIR'] == str(tmp_path / 'native-data' / 'watchtower')
    assert env['WATCHTOWER_CONFIG_FILE'] == str(native_config)
    assert env['WATCHTOWER_CCC_SPAWN_DEFAULTS_FILE'] == str(server.SPAWN_DEFAULTS_FILE)
    assert env['WATCHTOWER_MODEL_POLICY_FILE'] == str(server.COMMAND_CENTER_STATE_DIR / 'model-policy.json')
    assert env['WATCHTOWER_DELEGATE_URL'] == 'http://127.0.0.1:12345'
    overrides = {**env, 'WATCHTOWER_DATA_DIR': str(tmp_path / 'custom-data'),
                 'WATCHTOWER_CONFIG_FILE': str(tmp_path / 'custom-config'),
                 'WATCHTOWER_CCC_SPAWN_DEFAULTS_FILE': str(tmp_path / 'custom-defaults'),
                 'WATCHTOWER_MODEL_POLICY_FILE': str(tmp_path / 'custom-policy')}
    nested = accounts.launch_env('secondary', overrides)
    for key in ('WATCHTOWER_DATA_DIR', 'WATCHTOWER_CONFIG_FILE',
                'WATCHTOWER_CCC_SPAWN_DEFAULTS_FILE', 'WATCHTOWER_MODEL_POLICY_FILE'):
        assert nested[key] == overrides[key]
