#!/usr/bin/env python3
"""Visible-change times in a source region, same maths as build.py:
changes.py <take-dir> x0 y0 x1 y1 t_from t_to [thresh]"""
import subprocess, sys, numpy as np
take = sys.argv[1]; x0, y0, x1, y1 = map(int, sys.argv[2:6]); t0, t1 = float(sys.argv[6]), float(sys.argv[7])
th = float(sys.argv[8]) if len(sys.argv) > 8 else 0.35
w, h = (x1 - x0) // 4 * 4, (y1 - y0) // 4 * 4
raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t0:.3f}', '-t', f'{t1 - t0:.3f}', '-i', take + '/screen.mp4', '-vf', f'crop={w}:{h}:{x0}:{y0},scale={w // 4}:{h // 4},format=gray', '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
fr = np.frombuffer(raw, np.uint8).reshape(-1, h // 4, w // 4).astype(np.int16)
d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
print([round(t0 + (i + 1) / 30, 3) for i, x in enumerate(d) if x > th])
