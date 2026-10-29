"""Opt-in local voice lifecycle, using only temporary installs."""
import threading
from pathlib import Path
from unittest.mock import patch

from ccc_server import free_runtime


def test_scratch_directory_is_used(monkeypatch, tmp_path):
    import importlib
    monkeypatch.setenv('CCC_LOCAL_TTS_DIR', str(tmp_path))
    importlib.reload(free_runtime)
    assert free_runtime._LOCAL_TTS_DIR == tmp_path
    monkeypatch.delenv('CCC_LOCAL_TTS_DIR')
    importlib.reload(free_runtime)


def test_missing_install_does_not_run_installer(tmp_path):
    from ccc_server import local_speech
    with patch.object(free_runtime, '_LOCAL_TTS_DIR', tmp_path), patch('subprocess.run') as run:
        job = local_speech.InstallJob()
        assert job.status()['state'] == 'not_installed'
        run.assert_not_called()


def test_install_is_single_flight_and_failure_has_reason(tmp_path):
    from ccc_server import local_speech
    entered, release = threading.Event(), threading.Event()
    def fail(*args, **kwargs):
        entered.set()
        release.wait(2)
        raise OSError('test failure')
    with patch.object(free_runtime, '_LOCAL_TTS_DIR', tmp_path), patch('subprocess.Popen', side_effect=fail) as popen:
        job = local_speech.InstallJob()
        assert job.start()['state'] == 'installing'
        assert entered.wait(2)
        assert job.start()['state'] == 'installing'
        release.set()
        job.thread.join(2)
        assert job.status()['state'] == 'failed'
        assert job.status()['reason']
        assert popen.call_count == 1


def test_health_requires_streaming():
    with patch('urllib.request.urlopen') as open_url:
        open_url.return_value.__enter__.return_value.read.return_value = b'{"ok":true,"stream":false}'
        assert not free_runtime._local_tts_up()


def test_installer_checks_lameenc():
    source = (Path(__file__).resolve().parents[1] / 'scripts/install_local_speech.sh').read_text()
    assert 'import lameenc' in source


def test_existing_venv_missing_encoder_offers_repair(tmp_path):
    from ccc_server import local_speech
    for name in ('kokoro.fp32.onnx', 'voices.bin', 'venv/bin/python'):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
    with patch.object(free_runtime, '_LOCAL_TTS_DIR', tmp_path), patch('subprocess.run') as run, patch.object(free_runtime, '_local_tts_start') as start:
        run.return_value.returncode = 1
        state = local_speech.InstallJob().status()
        assert state['state'] == 'not_installed'
        assert 'repair' in state['reason']
        assert 'lameenc' in run.call_args.args[0][-1]
        start.assert_not_called()


def test_remote_and_proxied_installs_rejected():
    from ccc_server import local_speech
    from types import SimpleNamespace
    for client, headers in [
        ('192.0.2.1', {}),
        ('127.0.0.1', {'Origin': 'https://example.com'}),
        ('127.0.0.1', {'X-Forwarded-For': '192.0.2.1'}),
        ('127.0.0.1', {'Host': 'example.com'}),
        ('127.0.0.1', {'Forwarded': 'for=192.0.2.1'}),
    ]:
        responses = []
        handler = SimpleNamespace(client_address=(client, 1234), headers=headers,
                                  send_json=lambda body, code: responses.append((body, code)))
        with patch.object(local_speech.JOB, 'start') as start:
            local_speech.handle_api(handler, install=True)
            start.assert_not_called()
        assert responses[0][1] == 403


def test_local_click_starts_fixed_installer():
    from ccc_server import local_speech
    from types import SimpleNamespace
    responses = []
    handler = SimpleNamespace(client_address=('127.0.0.1', 1234), headers={'Host': 'localhost:8090'},
                              send_json=lambda body, code: responses.append((body, code)))
    with patch.object(local_speech.JOB, 'start', return_value={'state': 'installing'}) as start:
        local_speech.handle_api(handler, install=True)
        start.assert_called_once_with()
    assert responses == [({'state': 'installing'}, 202)]


def test_repaired_server_port_is_reused_after_dashboard_restart(tmp_path):
    with patch.object(free_runtime, '_LOCAL_TTS_DIR', tmp_path), patch.object(free_runtime, '_LOCAL_TTS_PORT', 3019), patch.object(free_runtime, '_LOCAL_TTS_PROCESS', None), patch.object(free_runtime, 'local_tts_installed', return_value=True), patch.object(free_runtime, '_local_tts_up', side_effect=[False, True]), patch.object(free_runtime, '_local_tts_health', return_value={'ok': True, 'stream': False}), patch('subprocess.Popen') as popen, patch('time.sleep'):
        popen.return_value.poll.return_value = None
        assert free_runtime._local_tts_start()
        replacement = free_runtime._LOCAL_TTS_PORT
        assert replacement != 3019
        assert (tmp_path / 'server-port.json').is_file()
    with patch.object(free_runtime, '_LOCAL_TTS_DIR', tmp_path), patch.object(free_runtime, '_LOCAL_TTS_PORT', 3019), patch.object(free_runtime, '_LOCAL_TTS_PROCESS', None), patch.object(free_runtime, '_local_tts_up', return_value=True), patch('subprocess.Popen') as popen:
        assert free_runtime._local_tts_start()
        assert free_runtime._LOCAL_TTS_PORT == replacement
        popen.assert_not_called()


def test_installer_timeout_kills_package_and_download_children():
    import os
    import signal
    from types import SimpleNamespace
    from ccc_server import local_speech
    with patch.object(os, 'name', 'posix'), patch.object(os, 'killpg', create=True) as kill:
        local_speech._kill_installer(SimpleNamespace(pid=12345))
        kill.assert_called_once_with(12345, signal.SIGKILL)
