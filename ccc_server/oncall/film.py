# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Offline replay of one retained On-call fix. Never starts an agent."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time

from . import alerts
from .store import default_root, load_config
from .week import stamp

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def read_db(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def preview_enabled():
    from ccc_server.paths import COMMAND_CENTER_STATE_DIR
    value = os.environ.get('CCC_FF_ONCALL', '').lower() in ('1', 'true', 'yes', 'on')
    try:
        return bool(json.loads((Path(COMMAND_CENTER_STATE_DIR) / 'feature-flags.json').read_text()).get('oncall', value))
    except (OSError, ValueError):
        return value


def ticket_for(ref):
    """Read local WatchTower data without importing its mutable/network backend."""
    base = Path(os.environ.get('WATCHTOWER_STORE') or
                str(Path(os.environ.get('WATCHTOWER_DATA_DIR') or
                         str(Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'watchtower')) / 'queues.json')).expanduser()
    if base.with_suffix('.db').exists():
        with read_db(base.with_suffix('.db')) as db:
            row = db.execute('SELECT item_json FROM items WHERE ref=?', (ref,)).fetchone()
            return json.loads(row[0]) if row else {}
    if base.exists():
        return next((r for r in json.loads(base.read_text()).get('items', []) if r.get('ref') == ref), {})
    return {}


def redact(text, repo, identities=(), show_names=False):
    text = str(text or '')
    if not show_names:
        for name in sorted(filter(None, identities), key=len, reverse=True):
            text = re.sub(re.escape(name), 'a customer', text, flags=re.I)
        text = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', 'a customer', text)
        text = re.sub(r'(?<!\w)\+?\d[\d ()-]{8,}\d', 'a customer', text)
    text = re.sub(r'https?://[^\s<>"\x27]+', '[link removed]', text)
    text = re.sub(r'(?i)\b(?:sk-[\w-]+|gh[pousr]_[\w]+|Bearer\s+\S+)', '[secret removed]', text)
    text = re.sub(r'(?i)\b(password|token|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+', r'\1=[secret removed]', text)
    text = text.replace(str(Path(repo).resolve()) + '/', '')
    text = re.sub(r'(?<![\w])(?:~?/|[A-Za-z]:\\)[^\s<>"\x27]+', '[path removed]', text)
    return text


def transcript_evidence(ticket, repo):
    """Only typed file operations and test summaries, never arbitrary conversation."""
    resolution = ticket.get('resolution') or {}
    target = Path(resolution.get('transcript_path') or '/nonexistent')
    if not target.is_file() or target.stat().st_size > 64 * 1024 * 1024:
        return [], []
    lo, hi = stamp(ticket.get('claimed_at')), stamp(ticket.get('closed_at'))
    if not lo or not hi:
        return [], []
    steps, checks, test_ids = [], [], set()
    with target.open() as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            when = stamp(row.get('timestamp'))
            if not lo <= when <= hi:
                continue
            msg = row.get('message') or {}
            blocks = msg.get('content') or []
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_use':
                    args = block.get('input') or {}
                    name = block.get('name')
                    file = args.get('file_path') or args.get('path')
                    if file and name in ('Read', 'Edit', 'Write'):
                        p = Path(file)
                        p = (Path(repo) / p).resolve() if not p.is_absolute() else p.resolve()
                        if p.is_relative_to(Path(repo).resolve()):
                            steps.append(f'{name}: {p.relative_to(Path(repo).resolve())}')
                    if name == 'Bash':
                        command = args.get('command', '')
                        # Show filenames referenced by the tool, not shell text or output.
                        for candidate in re.findall(r'[\w.-]+(?:/[\w.-]+)+\.py', command):
                            p = (Path(repo) / candidate).resolve()
                            if p.is_relative_to(Path(repo).resolve()) and p.is_file():
                                steps.append(f'Worked with {p.relative_to(Path(repo).resolve())}')
                    if name == 'Bash' and re.search(r'\b(pytest|unittest)\b', args.get('command', '')):
                        test_ids.add(block.get('id'))
                if block.get('type') == 'tool_result' and block.get('tool_use_id') in test_ids and not block.get('is_error'):
                    content = block.get('content', '')
                    if isinstance(content, list):
                        content = '\n'.join(x.get('text', '') for x in content if isinstance(x, dict))
                    for summary in re.findall(r'[^\n]*\b\d+ passed\b[^\n]*', str(content)):
                        if not re.search(r'\b[1-9]\d* (failed|errors?)\b', summary):
                            checks.append(summary.strip('= '))
    return list(dict.fromkeys(steps))[:5], list(dict.fromkeys(checks))[:2]


def collect(ref, root=None, show_names=False):
    root = Path(root or default_root())
    cfg = load_config(root)
    repo = cfg.get('repo')
    if not repo:
        raise ValueError('No repository recorded in On-call config.')
    with read_db(root / 'oncall.sqlite3') as db:
        row = db.execute('SELECT * FROM reports WHERE ticket_ref=? OR CAST(id AS TEXT)=? ORDER BY id LIMIT 1', (ref, ref)).fetchone()
        if not row:
            raise ValueError('No local On-call report matches that reference.')
        report = dict(row); ref = report['ticket_ref']
        row = db.execute("SELECT * FROM alerts WHERE ref=? AND kind='fixed' ORDER BY id DESC LIMIT 1", (ref,)).fetchone()
        if not row:
            raise ValueError('No Fixed notice is recorded for this report.')
        alert = dict(row); alert['meta'] = json.loads(alert['meta'])
        if alert['meta'].get('outcome', 'fixed') != 'fixed':
            raise ValueError('This report was closed without a fix.')
        delivered = bool(db.execute('SELECT 1 FROM deliveries WHERE alert_id=? AND ok=1 LIMIT 1', (alert['id'],)).fetchone())
        messages = [dict(r) for r in db.execute("SELECT * FROM customer_messages WHERE ref=? AND state='sent' AND sent_at IS NOT NULL ORDER BY id", (ref,))]
    ticket = ticket_for(ref)
    if ticket.get('repo_path') and Path(ticket['repo_path']).resolve() != Path(repo).resolve():
        raise ValueError('The ticket belongs to a different repository.')
    identities = [report.get('reporter_name'), report.get('reporter_address')]
    for message in messages:
        identities += [message.get('name'), message.get('address')]
    clean = lambda s: redact(s, repo, identities, show_names)
    scenes = []
    def add(kind, caption, text):
        body = clean(text).strip()
        lines = body.splitlines()
        if len(lines) > 12:
            body = '\n'.join(lines[:12]) + '…'
        # A bounded excerpt, with an explicit mark rather than an invented summary.
        if len(body) > 650:
            body = body[:647] + '…'
        scenes.append(dict(kind=kind, caption=caption, text=body, seconds=min(10, max(3, len(body.split()) / 3 + 2))))
    add('report', 'The report', report['text'] or report['title'])
    steps, checks = transcript_evidence(ticket, repo)
    add('work', 'The agent picks it up', '\n'.join(steps) or 'No scoped work steps retained.')
    commit = str((ticket.get('resolution') or {}).get('commit') or '')
    diff = ''
    if re.fullmatch(r'[a-fA-F0-9]{7,40}', commit):
        proc = subprocess.run(['git', '-C', repo, '--no-pager', 'show', '--format=', '--no-ext-diff', '--no-textconv', '--unified=2', commit, '--'], capture_output=True, text=True, timeout=20)
        if proc.returncode == 0:
            diff = '\n'.join(line for line in proc.stdout.splitlines() if not line.startswith(('diff --git ', 'index ')))
            diff = '\n'.join(diff.splitlines()[:10])
    add('change', 'The change', diff or 'No local commit diff retained.')
    add('check', 'The recorded check', '\n'.join(checks) or 'No passing check retained in the scoped transcript.')
    add('fixed', 'Fixed notice' if delivered else 'Fixed notice not confirmed delivered', alerts.render(alert)[1])
    for message in messages[:3]:
        add('customer', 'The message sent to the customer', message['text'])
    end = stamp(ticket.get('closed_at')) or alert['created_at']
    seconds = max(0, round(end - report['created_at']))
    add('duration', 'Report to fix', f'{seconds // 60} min {seconds % 60} sec')
    return dict(ref=ref, scenes=scenes, limits=['Fixed notice is reconstructed with current wording; exact historical channel text is not retained.', 'Only scoped Claude file operations and pytest result summaries are supported. Missing evidence is labelled.'])


def player(data, dark=False):
    css = (ROOT / 'static/q2.css').read_text()
    palette = '\n'.join(re.findall(r':root(?:\[data-theme="light"\])?\s*\{[^}]*\}', css))
    sections = []
    for n, scene in enumerate(data['scenes']):
        sections.append(f'<section class="scene {"active" if n == 0 else ""}" data-seconds="{scene["seconds"]}"><header>CCC On-call</header><h1>{html.escape(scene["caption"])}</h1><article class="{scene["kind"]}"><pre>{html.escape(scene["text"])}</pre></article><footer>{n+1} / {len(data["scenes"])}</footer></section>')
    return '<!doctype html><html data-theme="' + ('dark' if dark else 'light') + '"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'"><title>On-call film</title><style>' + palette + '''
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--font-ui)}
.scene{display:none;padding:48px 90px;box-sizing:border-box;height:720px}.scene.active{display:block}
header,footer{color:var(--text-muted);font-size:20px}h1{font-size:38px;font-weight:600;margin:26px 0}
article{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:28px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:24px/1.5 var(--font-ui);margin:0}.change pre{font:20px/1.4 var(--font-mono)}
.fixed{max-width:650px;border-radius:28px;border:3px solid var(--border)}footer{margin-top:24px}
nav{position:fixed;bottom:16px;right:24px}button{padding:8px 16px}
</style>''' + ''.join(sections) + '''<nav><button onclick="play()">Play</button><button onclick="stop()">Pause</button></nav><script>
let timer, i=0; const scenes=[...document.querySelectorAll('.scene')];
function stop(){clearTimeout(timer)}function play(){stop();scenes.forEach((s,n)=>s.classList.toggle('active',n===i));timer=setTimeout(()=>{i=(i+1)%scenes.length;play()},Number(scenes[i].dataset.seconds)*1000)}
</script></html>'''


def render(data, output, dark=False):
    started = time.monotonic()
    output = Path(output).expanduser().resolve()
    if output.suffix.lower() != '.mp4':
        raise ValueError('--output must end in .mp4')
    folder = output.with_suffix('.frames')
    if output.exists() or folder.exists() or output.with_suffix('.png').exists() or output.with_suffix('.html').exists() or output.with_suffix('.json').exists():
        raise ValueError('Output already exists. Choose a new filename.')
    output.parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir(mode=0o700)
    page = output.with_suffix('.html'); page.write_text(player(data, dark))
    subprocess.run(['node', str(Path(__file__).with_name('film_capture.cjs')), str(page), str(folder)], check=True, capture_output=True, timeout=120)
    poster = output.with_suffix('.png'); shutil.copyfile(folder / 'frame-000.png', poster)
    ffmpeg = shutil.which('ffmpeg')
    result = dict(html=str(page), poster=str(poster), frames=str(folder), mp4=None, limits=data['limits'])
    if ffmpeg:
        listing = folder / 'frames.txt'
        lines = []
        for n, scene in enumerate(data['scenes']):
            lines += [f"file 'frame-{n:03}.png'", f"duration {scene['seconds']:.3f}"]
        lines += [f"file 'frame-{len(data['scenes'])-1:03}.png'"]
        listing.write_text('\n'.join(lines) + '\n')
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-threads', '1', '-f', 'concat', '-safe', '1', '-i', str(listing), '-an', '-c:v', 'libx264', '-threads', '1', '-filter_threads', '1', '-pix_fmt', 'yuv420p', '-r', '24', '-movflags', '+faststart', str(output)], check=True, capture_output=True, timeout=180)
        result['mp4'] = str(output)
    else:
        result['notice'] = 'ffmpeg is missing. Saved the HTML player, frames and poster; no MP4 was made.'
    result['render_seconds'] = round(time.monotonic() - started, 2)
    output.with_suffix('.json').write_text(json.dumps(dict(result, scenes=data['scenes']), indent=2) + '\n')
    return result


def main(argv=None):
    p = argparse.ArgumentParser(prog='ccc oncall film')
    p.add_argument('ref'); p.add_argument('--output'); p.add_argument('--dark', action='store_true'); p.add_argument('--show-names', action='store_true')
    args = p.parse_args(argv)
    try:
        if not preview_enabled():
            raise ValueError('On-call preview is off. Enable it with ccc oncall setup.')
        data = collect(args.ref, show_names=args.show_names)
        print(json.dumps(render(data, args.output or f'{data["ref"]}-film.mp4', args.dark), indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f'ccc oncall film: {exc}', file=__import__('sys').stderr)
        return 1
