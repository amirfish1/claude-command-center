#!/usr/bin/env python3
"""Emit <area>/capture.json from <area>/capture.src.json + _shared/init.js.

capture.src.json = the normal taskfilm capture.json, plus a top-level "seeds"
object (localStorage) that is folded into stage.init_scripts[0]. Anything in
stage.init_scripts of the source is appended after the shared script.
"""
import json, os, sys
area = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
src = json.load(open(os.path.join(area, 'capture.src.json')))
base = open(os.path.join(here, 'init.js')).read()
CLEAN = {'ccc-last-seen-version': '5.37', 'ccc-whats-new-dismissed-version': '5.37',
         'ccc-pwa-install-dismissed': '9999999999999', 'ccc-telemetry-bar-dismissed': '1',
         'ccc-tour-done': '1', 'ccc-archive-window': 'all', 'ccc-status-rail-collapsed': '1',
         'ccc-spawn-cwd': '/home/demo/code/widgets-api'}
seeds = dict(CLEAN); seeds.update(src.pop('seeds', {}))
stage = src.setdefault('stage', {})
stage['init_scripts'] = [base.replace('__SEEDS__', json.dumps(seeds))] + list(stage.get('init_scripts', []))
stage['chrome_args'] = ['--disable-gpu', '--disable-smooth-scrolling', '--disable-gpu-vsync']
json.dump(src, open(os.path.join(area, 'capture.json'), 'w'), indent=1)
print('wrote', os.path.join(area, 'capture.json'))
