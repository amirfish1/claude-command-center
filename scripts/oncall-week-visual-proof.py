#!/usr/bin/env python3
"""Generate seeded exports and Puppeteer captures. No live stores or accounts.

Requires pytest (for the shared test fixture), Node and the repo's Puppeteer.
Keeps artifacts in .claude/screenshots/week; temporary stores are removed.
"""
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ccc_server.oncall import week_visual as visual


def main():
    seeded = runpy.run_path(str(ROOT/'tests/test_oncall_week_visual.py'))['seeded']
    output = ROOT/'.claude/screenshots/week'
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='oncall-week-visual-') as tmp:
        for name,options in [('month',{}),('empty',{'empty':True}),('large',{'large':True})]:
            data = seeded(Path(tmp)/name,**options)
            for theme in ('light','dark'):
                visual.write_export(data,output/f'{name}-{theme}.html',html=True,theme=theme)
                for square in (False,True):
                    shape = 'square' if square else 'wide'
                    visual.write_export(data,output/f'{name}-{theme}-{shape}.svg',square=square,theme=theme)
            if name == 'month':
                visual.write_export(data,output/'month-dark-square-export.png',square=True,theme='dark')
    subprocess.run(['node',str(ROOT/'scripts/oncall-week-visual-proof.js'),str(output)],check=True,timeout=120)
    print('PASS: seeded month, empty and large stores; SVG, PNG and HTML exports. Artifacts:',output)


if __name__ == '__main__':
    main()
