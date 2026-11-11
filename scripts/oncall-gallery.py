#!/usr/bin/env python3
"""Inventory EVERY image/video, including actual source frames, without inventing provenance."""
import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import struct
import subprocess

OUTPUT = Path(__file__).resolve().parents[1]/'.claude/screenshots/launch-captures'
IMAGES={'.png','.jpg','.jpeg','.webp','.gif'}
VIDEOS={'.mp4','.webm','.mov'}


def dimensions(file):
    data=file.read_bytes()
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return list(struct.unpack('>II',data[16:24]))
    if data[:2]==b'\xff\xd8':
        pos=2
        while pos<len(data):
            if data[pos]!=255:
                pos+=1
                continue
            marker=data[pos+1]
            pos+=2
            if marker in (0xD8,0xD9) or 0xD0<=marker<=0xD7:
                continue
            length=int.from_bytes(data[pos:pos+2],'big')
            if marker in (0xC0,0xC1,0xC2):
                h,w=struct.unpack('>HH',data[pos+3:pos+7])
                return [w,h]
            pos+=length
    return None


def provenance(directory):
    """Index explicit sidecars first; filenames alone never establish success."""
    index={}
    for file in sorted(directory.rglob('*provenance.json')):
        if file.name == 'mail-provenance.json':
            data=json.loads(file.read_text())
            for asset in file.parent.glob('mail-fixed-*.png'):
                index[asset]={'caption':data['description'] + (' Actual agent fix verified by the proof.' if data.get('agent') else ' Harness-closed protocol fixture; no agent fix.'),
                              'kind':'scripted-source-render','source':str(file.relative_to(directory))}
        elif file.name == 'cli-provenance.json':
            data=json.loads(file.read_text())
            for asset in file.parent.glob('cli-*.png'):
                index[asset]={'caption':data['description']+' Text rendering, not a terminal recording.',
                              'kind':'scripted-source-render','source':str(file.relative_to(directory))}
        elif file.name.startswith(('ticket-','fixture-ticket-')):
            data=json.loads(file.read_text())
            for asset in file.parent.glob(file.name.replace('-provenance.json','-*.png')):
                index[asset]={'caption':f"Real isolated WatchTower UI, state {data['state']}. "+
                    ('Harness fixture closure, no agent fix.' if data.get('fixture') else 'Actual agent run; completion is established only by its proof JSON/log.'),
                    'kind':'real-ui-fixture' if data.get('fixture') else 'real-ui-proof-still',
                    'source':str(file.relative_to(directory))}
    for file in sorted(directory.rglob('browser-provenance.json')):
        data=json.loads(file.read_text())
        for shot in data.get('screenshots',[]):
            index[file.parent/shot['file']]={**shot,'source':str(file.relative_to(directory))}
    for file in sorted(directory.rglob('recording.json')):
        data=json.loads(file.read_text())
        for frame in data.get('frames',[]):
            index[file.parent/frame['file']]={'caption':'Actual timed browser source frame; not a staged slide.',
                'run_id':data.get('run_id'),'utc':frame.get('utc'),'seconds':frame['seconds'],
                'kind':data.get('kind'),'failure':data.get('failure'),'source':str(file.relative_to(directory))}
    for file in sorted(directory.rglob('gallery-provenance.json')):
        for row in json.loads(file.read_text()):
            index[file.parent/row['file']]={**row, 'kind':'gallery-layout', 'source':str(file.relative_to(directory)), 'capture':row['source']}
    for file in sorted(directory.rglob('launch-pack.json')):
        pack=json.loads(file.read_text())
        for video in pack['assets']:
            index[file.parent/video['file']]={**video,'kind':'edited-real-browser-recording','status':pack['status'],
                'source':str(file.relative_to(directory))}
            # Root stills are byte-identical copies of known proof screenshots.
        for asset in file.parent.glob('*.png'):
            for role in ('agent','session'):
                original=file.parent/role/asset.name
                if original in index and original.read_bytes()==asset.read_bytes():
                    index[asset]={**index[original],'copy_of':str(original.relative_to(directory))}
        for asset in (file.parent/'review').glob('*.png'):
            index[asset]={'caption':'Frame decoded from the completed real browser film; see launch-pack.json for clip boundaries and speed.',
                          'kind':'decoded-review-frame','source':str(file.relative_to(directory))}
        for asset in (file.parent/'edits').glob('*.mp4'):
            index[asset]={'caption':'Intermediate edited clip of actual browser frames. See launch-pack.json for source run, speed and boundaries.',
                          'kind':'edited-real-browser-clip','source':str(file.relative_to(directory))}
    return index


def fallback(asset, directory):
    name=asset.name
    if name.startswith('mail-'):
        return {'caption':'Decoded real local RFC822 email rendered by the harness; NOT webmail or screen-recording footage. Agent/fixture status is in mail-provenance.json.', 'kind':'scripted-source-render'}
    if name.startswith('cli-'):
        return {'caption':'CLI output rendered as text by the harness; NOT a terminal recording.', 'kind':'scripted-source-render'}
    if name.startswith(('try-it-','hero-layout-','independent-')):
        return {'caption':'Landing-page illustration/layout verification with sample/scripted data; NOT real product footage.', 'kind':'sample-ui-verification'}
    if name.startswith('fixture-ticket-'):
        return {'caption':'Isolated real WatchTower UI, protocol fixture closed by harness; no agent fix.', 'kind':'real-ui-fixture'}
    if name.startswith('ticket-'):
        return {'caption':'Isolated WatchTower UI still; consult matching proof log for actual agent outcome. No light theme is supported by this WatchTower UI.', 'kind':'real-ui-proof-still'}
    return {'caption':'Unattributed saved asset. No claim of successful proof, real recording, or agent reply receipt; inspect original evidence before use.', 'kind':'unverified'}


def gallery_layouts(directory):
    """Pad real captures for a gallery; never manufacture product pixels."""
    names = ['session-alert-phone.png', 'session-reply-sent-phone.png',
             'ccc-session-question.png', 'ccc-session-reply.png',
             'report-fixed-desktop.png', 'fixed-result-phone.png']
    rows = []
    for number, name in enumerate(names, 1):
        source = directory / name
        if not source.is_file():
            continue
        dest = directory / f'session-gallery-{number}.png'
        subprocess.run(['/usr/bin/nice', '-n', '15', 'ffmpeg', '-hide_banner',
            '-loglevel', 'error', '-y', '-i', str(source), '-vf',
            'scale=1270:760:force_original_aspect_ratio=decrease,pad=1270:760:(ow-iw)/2:(oh-ih)/2:color=0xf3f5f7',
            '-frames:v', '1', '-threads', '1', str(dest)], check=True, timeout=60)
        rows.append({'file': dest.name, 'source': name,
            'caption': 'Real capture scaled and padded to 1270x760. No product UI was added.'})
    if rows:
        (directory / 'gallery-provenance.json').write_text(json.dumps(rows, indent=2))


def inventory(directory):
    sources=provenance(directory)
    entries=[]
    for asset in sorted(directory.rglob('*')):
        if not asset.is_file() or asset.is_symlink() or asset.suffix.lower() not in IMAGES|VIDEOS:
            continue
        entry={'file':str(asset.relative_to(directory)), 'bytes':asset.stat().st_size,
               'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),
               **sources.get(asset,fallback(asset,directory))}
        # Sidecar screenshot names are local, inventory names must be root-relative.
        entry['file']=str(asset.relative_to(directory))
        if asset.suffix.lower() in IMAGES:
            entry['pixels']=dimensions(asset)
        else:
            result=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(asset)],
                                  check=True,text=True,capture_output=True,timeout=30)
            media=json.loads(result.stdout)
            video=next((s for s in media['streams'] if s['codec_type']=='video'),{})
            entry.update(seconds=float(media['format']['duration']),pixels=[video.get('width'),video.get('height')],
                         audio=any(s['codec_type']=='audio' for s in media['streams']))
        entries.append(entry)
    return entries


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args()
    directory=args.output.resolve()
    if not directory.is_relative_to(OUTPUT.resolve()):
        parser.error('Gallery must stay in launch-captures')
    directory.mkdir(parents=True,exist_ok=True)
    gallery_layouts(directory)
    entries=inventory(directory)
    (directory/'capture-manifest.json').write_text(json.dumps(entries,indent=2))
    entries.sort(key=lambda item: (0 if item.get('kind')=='edited-real-browser-recording' else 1 if '/' not in item['file'] else 2, item['file']))
    figures=[]
    frame_groups={}
    for entry in entries:
        # Thousands of source frames remain inventoried but are grouped as links
        # rather than decoded into the gallery page all at once.
        name=escape(entry['file'],quote=True)
        caption=escape(entry['caption'])
        if '/frame-' in name:
            frame_groups.setdefault(str(Path(entry['file']).parent), []).append(
                f'<figure><img src="{name}" loading="lazy" alt="{caption}"><figcaption>{name}<p>{caption}</p><p>Source time: {entry.get("seconds")} seconds. Run: {escape(str(entry.get("run_id")))}</p></figcaption></figure>')
            continue
        if Path(name).suffix.lower() in VIDEOS:
            media=f'<video src="{name}" controls preload="metadata"></video>'
        else:
            media=f'<img src="{name}" loading="lazy" alt="{caption}">'
        figures.append(f'<figure>{media}<figcaption><b>{name}</b><p>{caption}</p><p>{escape(str(entry.get("pixels")))}</p></figcaption></figure>')
    pack_links=''.join('<p><a href="'+escape(str(p.parent.relative_to(directory)/'index.html'),quote=True)+'">Open '+escape(str(p.parent.relative_to(directory)))+' capture pack</a></p>' for p in sorted(directory.rglob('launch-pack.json')) if p.parent!=directory)
    frame_html=''.join('<details><summary>'+escape(group)+' source frames</summary><main>'+''.join(items)+'</main></details>' for group,items in frame_groups.items())
    pack=directory/'launch-pack.json'
    status='40-second film and 12-second loop passed media/proof checks. Review decoded film frames before sharing.' if pack.is_file() and json.loads(pack.read_text()).get('status')=='verified-media' else 'Evidence inventory. Each asset has its own status and provenance; a saved image alone does not establish a successful proof.'
    (directory/'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>On-call real capture evidence</title><style>body{font:16px/1.5 system-ui;margin:24px;background:#f3f5f7;color:#162332}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:20px}figure{margin:0;background:white;padding:16px;overflow-wrap:anywhere}img,video{max-width:100%;max-height:600px}</style><h1>On-call real capture evidence</h1><p>'''+escape(status)+'</p>'+pack_links+'''<p>Real local services with seeded reports and scripted test inputs. Separate proof runs are identified in the film. Rendered CLI/email sources and sample landing-page illustrations are labelled individually and excluded from film footage.</p><p><a href="capture-manifest.json">Every image/video: hashes, dimensions, precise available provenance (including all source frames)</a></p><main>'''+''.join(figures)+'</main>'+frame_html+'</html>')
    print(f'Inventoried every image/video: {len(entries)} assets in {directory}')


if __name__=='__main__':
    main()
