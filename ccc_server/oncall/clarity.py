"""Shared, transport-independent labels, choices and reply wording."""
import re


def label(deps, sid, fallback=''):
    try:
        name, folder = deps.label(sid)
    except Exception:
        name = folder = ''
    name = useful_name(name, sid) or useful_name(fallback, sid)
    parts = list(dict.fromkeys(str(x or '').strip() for x in (name, folder)))
    return ' · '.join(x for x in parts if x) or useful_name(fallback, sid) or 'An unnamed session'


def useful_name(name, sid):
    text = str(name or '').strip()
    if re.fullmatch(r'(?:agent\s+)?session-[a-z0-9-]*|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', text, re.I):
        return ''
    if sid and (text == sid or text.lower() == 'agent ' + sid.lower()
                or (len(sid) >= 8 and sid[:8] in text)):
        return ''
    return text if text.lower() not in ('a session', 'an agent', 'agent') else ''


def unreadable(what='question'):
    return (f'The session is waiting, but On-call could not read the {what}. '
            'Open this session in CCC to read the prompt and answer there.')


def choices(raw):
    """Normalize labels/replies without shortening the actual answer."""
    if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 12:
        return []
    out = []
    for item in raw:
        if isinstance(item, str):
            name = reply = item
        elif isinstance(item, dict):
            name = item.get('label')
            reply = item.get('reply', name)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            name, reply = item
        else:
            return []
        if not all(isinstance(v, str) and v.strip() and len(v) <= 2000 for v in (name, reply)):
            return []
        out.append([name.strip(), reply.strip()])
    return out


def numbered(text):
    """Only a contiguous trailing list after a question; never a work checklist."""
    match = re.search(r'(?s)^(.*?\?)\s*\n((?:[ \t]*\d+[.)][ \t]+[^\n]+\n?)+)\s*$', text)
    if not match or '```' in match[0]:
        return []
    rows = re.findall(r'^\s*(\d+)[.)]\s+(.+)$', match[2], re.M)
    if [int(n) for n, _ in rows] != list(range(1, len(rows) + 1)):
        return []
    return choices([text.strip() for _, text in rows])


def offered(alert):
    if (alert.get('meta') or {}).get('at_keyboard'):
        return []
    if alert['kind'] in ('question', 'plain_question', 'decision'):
        return choices((alert.get('meta') or {}).get('choices')) or numbered(alert.get('body') or '')
    return choices({'permission': ['allow', 'deny'], 'report': ['go', 'no'],
                    'send': ['yes', 'no'], 'stopped': ['continue']}.get(alert['kind']))


def translate(alert, text):
    """Called after alert routing, so an option never changes alert identity."""
    options = offered(alert)
    value = text.strip().casefold()
    if value.isdecimal() and 1 <= int(value) <= len(options):
        return _safe_reply(options[int(value) - 1][1])
    matches = {reply for name, reply in options
               if value in (name.casefold(), reply.casefold())}
    return _safe_reply(matches.pop()) if len(matches) == 1 else text


def _safe_reply(reply):
    from .secret_filter import mask
    _text, hit = mask(reply)
    if hit:
        raise ValueError('This choice contains a possible secret. Answer in the session instead.')
    return reply


def excerpt(text):
    """The last two prose sentences, without the machine status footer."""
    prose = str(text or '').partition('<session-state>')[0].strip()
    sentences = re.split(r'(?<=[.!?])\s+', prose)
    return ' '.join(sentences[-2:])[-1200:].strip()
