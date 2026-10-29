#!/usr/bin/env python3
"""Build a promo-film staging copy of the CCC static demo.

Creates <stage>/static -> repo static (symlink) and <stage>/api = docs/demo/api
with every timestamp compressed so the newest item in each file reads as
"5m" and the rest keep their relative order (scale 0.05), the version label
set to a real-looking release, and the generic transcript replaced by a
per-session one (no demo disclaimers).

Usage: stage-fixtures.py <repo-root> <stage-dir>
"""
import json, os, re, shutil, sys, time
from datetime import datetime, timezone

REPO, STAGE = sys.argv[1], sys.argv[2]
SRC = os.path.join(REPO, 'docs', 'demo', 'api')
DST = os.path.join(STAGE, 'api')
NOW = time.time() - 300
SCALE = 0.15
VERSION = '5.37'

if os.path.isdir(DST): shutil.rmtree(DST)
shutil.copytree(SRC, DST)
link = os.path.join(STAGE, 'static')
if not os.path.islink(link):
    os.symlink(os.path.join(REPO, 'static'), link)

EPOCH_RE = re.compile(r'("(?:mtime|sidecar_ts|last_message_at|last_mtime|synced_at|fetched_at)":\s*)(1[6-9]\d{8}(?:\.\d+)?)')
ISO_RE = re.compile(r'"(20\d\d-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?)"')

def iso_to_epoch(s):
    s2 = s.replace('Z', '+00:00')
    try: return datetime.fromisoformat(s2).timestamp()
    except Exception: return None

def shift_file(path):
    s = open(path).read()
    ep = [float(m.group(2)) for m in EPOCH_RE.finditer(s)]
    iso = [(m.group(1), iso_to_epoch(m.group(1))) for m in ISO_RE.finditer(s)]
    iso = [(a, b) for a, b in iso if b]
    allts = ep + [b for _, b in iso]
    if not allts: return
    mx = max(allts)
    def newts(t): return NOW - (mx - t) * SCALE
    s = EPOCH_RE.sub(lambda m: m.group(1) + str(int(newts(float(m.group(2))))), s)
    def iso_sub(m):
        t = iso_to_epoch(m.group(1))
        if not t: return m.group(0)
        return '"' + datetime.fromtimestamp(newts(t), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') + '"'
    s = ISO_RE.sub(iso_sub, s)
    open(path, 'w').write(s)

for root, _, files in os.walk(DST):
    for f in files:
        if f.endswith('.json'): shift_file(os.path.join(root, f))

# rows without a sidecar timestamp render their age as NaN; borrow mtime
for name in ('conversations/list.json', 'conversations/all.json'):
    fp = os.path.join(DST, name); d = json.load(open(fp))
    for c in d.get('conversations', []):
        if not c.get('sidecar_ts'): c['sidecar_ts'] = c.get('mtime')
        if not c.get('modified'): c['modified'] = c.get('mtime')
        if not c.get('last_interacted'): c['last_interacted'] = c.get('mtime')
    json.dump(d, open(fp, 'w'), indent=1)

# version label
json.dump({'version': VERSION}, open(os.path.join(DST, 'version.json'), 'w'))
json.dump({'ok': True, 'up_to_date': True, 'current': VERSION, 'latest': VERSION},
          open(os.path.join(DST, 'version/check.json'), 'w'))

# generic transcript: use the auth-cookie one (no demo disclaimers)
shutil.copy(os.path.join(DST, 'conversations/_id-1.json'), os.path.join(DST, 'conversations/_id.json'))
# per-session transcripts for the init-script fetch rewrite: t-<prefix>.json
MAP = {'22222222': '_id-1', 'ffffffff': '_id-1', '11111111': '_id-1', 'dddddddd': '_id-1', '33333333': '_id-1',
       '77777777': '_id-2', '88888888': '_id-2', 'aaaaaaaa': '_id-3', '44444444': '_id-3', '12121212': '_id-4', '55555555': '_id-4'}
for pfx, src in MAP.items():
    shutil.copy(os.path.join(DST, f'conversations/{src}.json'), os.path.join(DST, f'conversations/t-{pfx}.json'))
# history index: present, so the sidebar search never shows the "Build a history index?" prompt
json.dump({'exists': True, 'indexing': False, 'embedding': False, 'message_count': 18422,
           'latest_message_unix': int(NOW), 'semantic': {'available': True}, 'available': True},
          open(os.path.join(DST, 'history/status.json'), 'w'))
print('staged', DST)
