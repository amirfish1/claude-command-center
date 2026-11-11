#!/usr/bin/env python3
"""Inventory saved assets and pad available real captures to 1270x760.

Never turn unrelated still images into a purported screen recording.
"""
from html import escape
from pathlib import Path
import hashlib
import json
import struct
import subprocess

OUTPUT = Path(__file__).resolve().parents[1] / '.claude/screenshots/launch-captures'
SCREENS = [
    ('session-alert-phone.png', 'Real ntfy alert from a local session-proof attempt.'),
    ('session-reply-typed-phone.png', 'Reply typed in real ntfy during a local session-proof attempt.'),
    ('session-reply-sent-phone.png', 'Reply published in real ntfy; session receipt is not established by this image.'),
    ('ccc-session-question.png', 'Real CCC question view from a local session-proof attempt.'),
    ('mail-fixed-desktop.png', 'Decoded real mailbox message. Read its in-frame fixture/agent label.'),
]


def describe(name):
    if name.startswith(('try-it-', 'independent-')):
        return 'Landing-page illustration verification. Sample data, scripted fix. node scripts/test-oncall-try.cjs; independent images came from the separate browser review.'
    if name.startswith('session-gallery-'):
        return 'Product Hunt layout: existing capture scaled to fit and padded with ffmpeg. No UI was added. See gallery-provenance.json for the source.'
    if name.startswith(('session-', 'ccc-session-')):
        return 'Real local ntfy/CCC capture from scripts/oncall-session-proof.py --with-agent --capture. Pixel dimensions are listed below. A screenshot alone does not establish proof completion or next-turn receipt.'
    if name.startswith('cli-'):
        return 'Actual scripts/oncall-proof.py --capture CLI output rendered as text. Paths/server redacted. This is not a terminal recording; sample minor decisions are submitted by the harness to the real gate, not by an agent.'
    if name.startswith('fixture-ticket-'):
        return 'Real isolated WatchTower UI from scripts/oncall-proof.py --capture. The harness reports and closes a protocol fixture. No agent ran.'
    if name.startswith('ticket-'):
        return 'Real isolated WatchTower page from scripts/oncall-proof.py --with-agent --capture. The saved reported state does not establish a fix.'
    if name.startswith('mail-'):
        return 'Actual local GreenMail message from scripts/oncall-proof.py --capture. Decoded source rendering, not webmail. Read mail-provenance.json and the in-frame label for whether the harness or an agent closed the ticket.'
    return 'Saved supporting file from capture attempts. Consult its contents; presence is not proof of a completed run.'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    gallery = []
    for number, (name, caption) in enumerate(SCREENS, 1):
        if not (OUTPUT / name).is_file():
            continue
        dest = f'session-gallery-{number}.png'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(OUTPUT / name),
                        '-vf', 'scale=1270:760:force_original_aspect_ratio=decrease,pad=1270:760:(ow-iw)/2:(oh-ih)/2:color=0x111b22',
                        '-frames:v', '1', '-threads', '1', str(OUTPUT / dest)], check=True)
        gallery.append({'file':dest, 'source':name, 'caption':caption})
    (OUTPUT / 'gallery-provenance.json').write_text(json.dumps(gallery, indent=2))
    entries, figures = [], []
    for asset in sorted(OUTPUT.iterdir()):
        if not asset.is_file() or asset.name in ('index.html', 'capture-manifest.json'):
            continue
        caption = describe(asset.name)
        entry = {'file':asset.name, 'caption':caption,
                 'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(), 'bytes':asset.stat().st_size}
        name = escape(asset.name, quote=True)
        if asset.suffix == '.png':
            entry['pixels'] = list(struct.unpack('>II', asset.read_bytes()[16:24]))
            media = f'<img src="{name}" alt="{escape(caption, quote=True)}" loading="lazy">'
            caption += ' Pixels: ' + ' × '.join(map(str, entry['pixels'])) + '.'
        elif asset.suffix in ('.mp4', '.webm'):
            media = f'<video src="{name}" controls preload="metadata"></video>'
        else:
            media = f'<a href="{name}">{name}</a>'
        figures.append(f'<figure>{media}<figcaption><b>{name}</b><p>{escape(caption)}</p></figcaption></figure>')
        entries.append(entry)
    (OUTPUT / 'capture-manifest.json').write_text(json.dumps(entries, indent=2))
    (OUTPUT / 'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>On-call capture evidence</title>
<style>body{font:16px/1.5 system-ui;margin:24px;background:#111;color:#eee}a{color:#9df}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:20px}figure{margin:0;background:#222;padding:16px;overflow-wrap:anywhere}img,video{max-width:100%;max-height:600px}figcaption{padding-top:12px}</style>
<h1>On-call capture evidence: partial pack</h1>
<p>Unpublished local evidence. These assets come from separate runs. No continuous screen recording was saved. No 40-second film or 12-second loop is supplied. Read the pixel dimensions and proof results before use. A no-agent protocol run does not establish an automatic code fix.</p>
<p>This tool does not create a continuous film or a terminal recording. Images of the landing-page illustration are verification evidence only, not product screenshots.</p>
<p><a href="capture-manifest.json">Asset hashes, dimensions and provenance</a></p><main>''' + '\n'.join(figures) + '</main></html>')
    print(f'Inventoried {len(entries)} assets; generated {len(gallery)} gallery layouts. Pack remains partial.')


if __name__ == '__main__':
    main()
