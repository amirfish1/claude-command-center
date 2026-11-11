"""Optional Puppeteer/ffmpeg capture; the demo itself remains stdlib-only."""
import json
from pathlib import Path
import selectors
import shutil
import signal
import os
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[2]
LIMIT = 4_000_000


def available():
    if not shutil.which('node') or not shutil.which('ffmpeg'):
        return False
    probe = subprocess.run(['node', '-e', 'require(process.argv[1])', str(REPO / 'require-puppeteer.js')],
                           capture_output=True, timeout=10)
    return probe.returncode == 0


class Recorder:
    def __init__(self, url, destination):
        self.destination = Path(destination)
        self.temp = tempfile.TemporaryDirectory(prefix='ccc-demo-frames-')
        self.frames = Path(self.temp.name)
        self.log = None
        self.proc = None
        try:
            self.log = (self.destination / 'capture.log').open('w')
            self.proc = subprocess.Popen(['node', str(Path(__file__).with_suffix('.cjs')), str(REPO), url,
                                      str(self.frames)], stdout=subprocess.PIPE, stderr=self.log,
                                     text=True, start_new_session=True)
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(20) or self.proc.stdout.readline().strip() != 'READY':
                    raise RuntimeError('Browser capture did not start; see capture.log.')
        except BaseException:
            self.close()
            raise

    def finish(self):
        if self.proc.wait(timeout=130):
            raise RuntimeError('Browser recording failed; see capture.log.')
        shots = json.loads((self.frames / 'frames.json').read_text())
        concat = []
        for i, shot in enumerate(shots):
            duration = shots[i + 1]['at'] - shot['at'] if i + 1 < len(shots) else .2
            concat.extend(["file '" + shot['file'] + "'", f'duration {duration:.6f}'])
        (self.frames / 'frames.txt').write_text('\n'.join(concat) + '\n')
        mp4, gif = self.destination / 'customer.mp4', self.destination / 'customer.gif'
        self.encode(['-f', 'concat', '-safe', '0', '-i', 'frames.txt', '-vf', 'scale=800:-2',
                     '-r', '10', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28',
                     '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(mp4)])
        for fps, colours in ((6, 64), (4, 48), (2, 32)):
            self.encode(['-i', str(mp4), '-filter_complex',
                         f'fps={fps},scale=800:-1,split[a][b];[a]palettegen=max_colors={colours}[p];'
                         '[b][p]paletteuse=dither=bayer:bayer_scale=5', '-loop', '0', str(gif)])
            if gif.stat().st_size < LIMIT:
                break
        if max(mp4.stat().st_size, gif.stat().st_size) >= LIMIT:
            raise RuntimeError('Recording exceeds the 4 MB limit.')
        # Keep representative original frames for review without retaining every frame.
        for label, fraction in (('opening', .15), ('decision', .48), ('finished', .98)):
            shot = shots[min(len(shots)-1, int(len(shots)*fraction))]
            shutil.copyfile(self.frames / shot['file'], self.destination / (label + '.png'))
        return dict(mp4=str(mp4), gif=str(gif), duration=round(shots[-1]['at'], 2), width=800,
                    mp4_bytes=mp4.stat().st_size, gif_bytes=gif.stat().st_size)

    def encode(self, args):
        result = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                                 '-threads', '2', '-filter_threads', '1', '-filter_complex_threads', '1',
                                 *args[:-1], '-threads', '2', args[-1]], cwd=self.frames, stdout=self.log,
                                stderr=self.log, timeout=90)
        if result.returncode:
            raise RuntimeError('ffmpeg failed; see capture.log.')

    def close(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=3)
        if self.proc:
            self.proc.stdout.close()
        if self.log:
            self.log.close()
        self.temp.cleanup()
