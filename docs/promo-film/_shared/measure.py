#!/usr/bin/env python3
"""Print click/key times (relative to session t0) and video facts for a take, and
optionally dump preview frames: measure.py <take-dir> [t1 t2 ...] (seconds)."""
import json, os, subprocess, sys
take = sys.argv[1]
sess = json.load(open(os.path.join(take, 'session.json')))
t0 = sess.get('t0_wallclock') or sess.get('t0')
clicks, keys = [], []
for line in open(os.path.join(take, 'input.jsonl')):
    line = line.strip()
    if not line: continue
    ev = json.loads(line)
    t = round(ev['ts'] - t0, 3)
    if ev.get('device') == 'mouse' and ev.get('type') == 'click' and ev.get('pressed'): clicks.append((t, ev.get('x'), ev.get('y')))
    elif ev.get('device') == 'keyboard' and ev.get('type') == 'press': keys.append((t, ev.get('key')))
print('session keys:', [k for k in sess.keys()][:12])
print('clicks:', len(clicks)); [print('  ', c) for c in clicks]
print('keys:', len(keys), [t for t, _ in keys])
probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,r_frame_rate,nb_frames,duration', '-of', 'default=nw=1', os.path.join(take, 'screen.mp4')], capture_output=True, text=True)
print(probe.stdout.strip())
out = os.path.join(take, 'preview'); os.makedirs(out, exist_ok=True)
for t in sys.argv[2:]:
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', t, '-i', os.path.join(take, 'screen.mp4'), '-frames:v', '1', '-vf', 'scale=1280:720', os.path.join(out, f'{float(t):06.2f}.png')])
    print('frame', t)
