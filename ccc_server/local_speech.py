"""Explicit, single-flight installation of the optional offline Speak voice.

Only start() runs the fixed installer. Status checks never install packages.
No request-supplied paths or commands; runtime dependencies remain stdlib-only.
"""
import os
from pathlib import Path
import subprocess
import signal
import threading
import time

from ccc_server import free_runtime

_INSTALLER = Path(__file__).resolve().parents[1] / 'scripts' / 'install_local_speech.sh'

def _kill_installer(process):
    try:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass



class InstallJob:
    def __init__(self):
        self.lock = threading.RLock()
        self.thread = None
        self.state = 'not_installed'
        self.reason = ''
        self.progress = ''
        self.checked = 0

    def status(self):
        with self.lock:
            if self.state not in ('installing', 'failed') and time.monotonic() - self.checked > 10:
                self.checked = time.monotonic()
                if free_runtime.local_tts_installed():
                    try:
                        probe = subprocess.run(
                            [str(free_runtime._LOCAL_TTS_DIR / 'venv/bin/python'), '-c',
                             'import kokoro_onnx, soundfile, lameenc'],
                            capture_output=True, timeout=15)
                        if probe.returncode:
                            self.state, self.reason = 'not_installed', 'Voice dependencies need repair.'
                        elif free_runtime._local_tts_start():
                            self.state, self.reason = 'ready', ''
                        else:
                            self.state, self.reason = 'failed', 'Local voice server could not start.'
                    except (OSError, subprocess.TimeoutExpired):
                        self.state, self.reason = 'not_installed', 'Voice dependencies need repair.'
                else:
                    self.state = 'not_installed'
            return dict(state=self.state, reason=self.reason, progress=self.progress)

    def start(self):
        with self.lock:
            if self.state == 'installing':
                return dict(state=self.state, reason=self.reason, progress=self.progress)
            self.state, self.reason, self.progress = 'installing', '', 'Preparing offline voice'
            self.thread = threading.Thread(target=self._install, daemon=True)
            self.thread.start()
            return dict(state=self.state, reason=self.reason, progress=self.progress)

    def _install(self):
        phase = 'Preparing offline voice'
        try:
            env = dict(os.environ, CCC_LOCAL_TTS_DIR=str(free_runtime._LOCAL_TTS_DIR))
            # On POSIX, bound the installer and its package/download children.
            with subprocess.Popen(['bash', str(_INSTALLER)], env=env,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, start_new_session=True) as process:
                timer = threading.Timer(1800, _kill_installer, args=(process,))
                timer.start()
                try:
                    for line in process.stdout:
                        if 'Installing voice dependencies' in line:
                            phase = 'Installing voice dependencies'
                        elif 'Downloading' in line:
                            phase = 'Downloading offline voice (~330 MB)'
                        elif 'have voices.bin' in line or 'Kokoro OK' in line:
                            phase = 'Checking voice and MP3 streaming'
                        with self.lock:
                            self.progress = phase
                    code = process.wait()
                except Exception:
                    _kill_installer(process)
                    raise
                finally:
                    timer.cancel()
                if code:
                    raise RuntimeError(f'{phase} failed (exit {code}). Check your connection and available disk space, then retry.')
            if not free_runtime._local_tts_start():
                raise RuntimeError('Local voice server could not start with MP3 streaming. Retry the install.')
            with free_runtime.local_tts_open('Local voice ready.', 'af_heart')[0] as response:
                if response.headers.get_content_type() != 'audio/mpeg' or not response.read(4096):
                    raise RuntimeError('Local voice did not return MP3 audio. Retry the install.')
            with self.lock:
                self.state, self.reason, self.progress = 'ready', '', 'Offline voice ready'
                self.checked = time.monotonic()
        except Exception as exc:
            # Do not return subprocess output, install paths, or environment data.
            reason = str(exc) if isinstance(exc, RuntimeError) else f'{phase} failed ({type(exc).__name__}). Retry the install.'
            with self.lock:
                self.state, self.reason = 'failed', reason


JOB = InstallJob()


def handle_api(handler, install=False):
    """Use the same local-admin boundary as network and config consent writes."""
    import re
    from ccc_server import phone_access
    origin = (handler.headers.get('Origin') or '').strip()
    if ((origin and not re.fullmatch(r'https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?', origin))
            or phone_access.is_remote_request(handler.client_address[0], handler.headers)):
        handler.send_json({'error': 'Offline voice installation is localhost-only'}, 403)
        return
    handler.send_json(JOB.start() if install else JOB.status(), 202 if install else 200)
