"""Private static exports. Only validated numbers cross the rendering boundary."""
from datetime import datetime, timezone
from html import escape
import math
import os
from pathlib import Path
import subprocess
import signal
import tempfile

from .week import span

LABELS = ('Bugs reported', 'Fixed by agents', 'Questions that reached you', 'Decisions made without you')
PALETTES = {
    'light': ('#f5f1e8', '#172b25', '#52635b', '#b23c24', '#d3d8cc'),
    'dark': ('#172b25', '#f5f1e8', '#bfcdbf', '#ffa38b', '#42574d'),
}


def number(value, *, count=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Export unavailable: a count or time is missing or invalid.')
    if count and (int(value) != value or value > 999999999):
        raise ValueError('Export unavailable: count is outside the supported range.')
    return int(value) if count else value


def public_periods(data):
    """Do not pass through arbitrary keys, labels, metadata or strings."""
    if data.get('limits') or data.get('ok') is not True:
        raise ValueError('Export unavailable: weekly counts are incomplete.')
    if not data.get('periods'):
        raise ValueError('Export unavailable: no weekly periods were returned.')
    result = []
    try:
        for p in data['periods'][:8]:
            if p['tickets']['unknown']:
                raise ValueError('Export unavailable: ticket outcomes are incomplete.')
            start, end = number(p['start']), number(p['end'])
            if end <= start:
                raise ValueError('Export unavailable: invalid weekly dates.')
            counts = [number(v, count=True) for v in (p['reports']['received'], p['tickets']['fixed'],
                                                       p['owner_questions_sent'], p['decisions']['made'])]
            times = [None if v is None else number(v) for v in
                     (p['report_to_fix_seconds']['median'], p['replies']['median_seconds'])]
            result.append(dict(start=start, end=end, counts=counts, times=times))
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError('Export unavailable: required weekly counts are missing. Update the dashboard.') from exc
    return sorted(result, key=lambda p: p['start'], reverse=True)


def date(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%d %b %Y')


def dates(p):
    return date(p['start']) + ' to ' + date(p['end']) + ' UTC'


def card(data, *, square=False, theme='light'):
    periods = public_periods(data)
    bg, fg, muted, accent, line = PALETTES[theme]
    w, h = (1080, 1080) if square else (1200, 675)
    p = periods[0]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="title desc">',
             '<title id="title">A week of On-call</title>',
             '<desc id="desc">'+escape(dates(p)+'. '+'. '.join(f'{l}: {v:,}' for l,v in zip(LABELS,p['counts'])))+'</desc>',
             f'<rect width="{w}" height="{h}" fill="{bg}"/>',
             f'<g font-family="Arial, Helvetica, sans-serif" fill="{fg}">']
    def text(x,y,value,size=20,color=fg,weight=400):
        parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}">{escape(str(value))}</text>')
    def rule(y):
        parts.append(f'<path d="M48 {y} H{w-48}" stroke="{line}"/>')
    text(48,85,'A week of On-call',42,weight=700)
    text(48,123,dates(p),24 if square else 20,muted)
    rule(154)
    for i,(label,value) in enumerate(zip(LABELS,p['counts'])):
        x = 48 + (i%2)*500 if square else 48+i*280
        y = 278+(i//2)*218 if square else 262
        formatted = f'{value:,}'
        font = min(88 if square else 76, int((450 if square else 250)/(len(formatted)*.62)))
        text(x,y,formatted,font,accent if i==1 else fg,700)
        words = label.split(' ')
        if len(label)>20:
            cut = 2
            text(x,y+40,' '.join(words[:cut]),28 if square else 22,muted)
            text(x,y+(76 if square else 68),' '.join(words[cut:]),28 if square else 22,muted)
        else:
            text(x,y+40,label,28 if square else 22,muted)
    divider = 610 if square else 365
    rule(divider)
    for i,(label,value) in enumerate(zip(('Median report to fix','Median time to reply'),p['times'])):
        x = 48+i*(500 if square else 560)
        text(x,divider+60,span(value),36,weight=700)
        text(x,divider+95,label,20,muted)
    chart_y = 890 if square else 570
    history = list(reversed(periods))
    if len(history)>1 and any(any(row['counts']) for row in history[:-1]):
        text(48,chart_y-48,f'Bugs reported · {len(history)} weeks',18,muted)
        maximum = max(1,max(row['counts'][0] for row in history))
        step = (w-96)/len(history)
        for i,row in enumerate(history):
            x = 48+i*step
            height = 46*row['counts'][0]/maximum
            parts.append(f'<rect x="{x}" y="{chart_y-height}" width="{step-12}" height="{max(1,height)}" fill="{accent if i==len(history)-1 else muted}"/>')
            text(x,chart_y+25,datetime.fromtimestamp(row['start'],timezone.utc).strftime('%d %b'),16,muted)
    text(48,h-31,'Recorded counts. A fix means a closed ticket with a commit.',16,muted)
    parts.append('</g></svg>')
    return '\n'.join(parts)


def page(data, *, theme='light'):
    periods = public_periods(data)
    bg,fg,muted,accent,line = PALETTES[theme]
    sections = []
    for p in periods:
        metrics = ''.join(f'<div><dt>{label}</dt><dd>{value:,}</dd></div>' for label,value in zip(LABELS,p['counts']))
        times = ''.join(f'<div><dt>{label}</dt><dd>{span(value)}</dd></div>' for label,value in
                        zip(('Median report to fix','Median time to reply'),p['times']))
        sections.append(f'<section><h2>{dates(p)}</h2><dl class="counts">{metrics}</dl><dl class="times">{times}</dl></section>')
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>On-call by the week</title><style>
:root{{color-scheme:{theme};background:{bg};color:{fg};font-family:Arial,Helvetica,sans-serif}}
*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1200px;margin:auto;padding:64px 48px}}
::selection{{background:{accent};color:{bg}}}h1{{font-size:clamp(36px,5vw,64px);letter-spacing:-.03em;margin:0 0 20px}}
header p,footer{{color:{muted};line-height:1.6;max-width:70ch}}header{{padding-bottom:40px}}section{{border-top:1px solid {line};padding:28px 0 36px}}
h2{{font-size:20px;margin:0 0 36px}}dl{{margin:0;display:grid;gap:24px}}.counts{{grid-template-columns:repeat(4,minmax(0,1fr))}}
dt{{font-size:16px;color:{muted};line-height:1.4;min-height:45px}}dd{{margin:8px 0 0;font-size:clamp(28px,4.5vw,58px);font-weight:700;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}}
.counts>div:nth-child(2) dd{{color:{accent}}}.times{{grid-template-columns:repeat(2,minmax(0,1fr));margin-top:32px}}.times dt{{min-height:0}}.times dd{{font-size:24px}}footer{{border-top:1px solid {line};padding-top:28px;font-size:14px}}
@media(max-width:650px){{main{{padding:32px 20px}}header{{padding-bottom:24px}}.counts{{grid-template-columns:repeat(2,minmax(0,1fr));gap:28px 16px}}h2{{font-size:17px;line-height:1.5;margin-bottom:24px}}dd{{font-size:36px}}.times{{gap:16px}}.times dd{{font-size:22px}}}}
</style></head><body><main><header><h1>On-call by the week</h1><p>{len(periods)} weeks of recorded reports, fixes and decisions.</p></header>
{''.join(sections)}
<footer>Dates are in UTC; each period includes its start and excludes its end. Fixes are closed tickets with a recorded commit, not proof of deployment or agent authorship. Questions count distinct reply-required alerts successfully sent, not every channel delivery. Decisions count recorded automatic decisions. Missing times say “not recorded”.</footer>
</main></body></html>'''


def write_export(data, destination, *, square=False, theme='light', html=False):
    target = Path(destination).expanduser()
    suffix = target.suffix.lower()
    if suffix not in (('.html',) if html else ('.svg','.png')):
        raise ValueError('Use .html for --page, or .svg or .png for --card.')
    content = (page(data,theme=theme) if html else card(data,square=square,theme=theme)).encode()
    if suffix == '.png':
        script = Path(__file__).resolve().parents[1] / 'oncall_week_png.js'
        try:
            proc = subprocess.Popen(['node',str(script)],stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                    start_new_session=os.name == 'posix')
            try:
                png, _error = proc.communicate(content, timeout=45)
            except BaseException:
                # Include Chromium children if rendering times out or is interrupted.
                if os.name == 'posix':
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                proc.communicate()
                raise
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ValueError('PNG needs Node and the repo Puppeteer browser. Export .svg instead.') from exc
        if proc.returncode or not png.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('PNG could not render with the repo Puppeteer browser. Export .svg instead.')
        content = png
    # A failed render leaves an existing destination intact.
    fd, name = tempfile.mkstemp(prefix='.oncall-export-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as handle:
            handle.write(content)
        os.replace(name,target)
    finally:
        Path(name).unlink(missing_ok=True)
