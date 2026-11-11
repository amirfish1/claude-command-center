"""Webhook-free voice escalation using an existing SMS reply channel."""
import re
from . import alerts

DEFAULT_KINDS = 'stopped,stuck,ci_failed'


def kinds(entry):
    return set(str(entry.get('kinds', DEFAULT_KINDS)).split(','))


def resolve(entry, cfg):
    if entry.get('type') != 'call':
        return entry
    sms = next((c for c in cfg.get('channels', []) if c['id'] == entry.get('sms_channel')
                and c['type'] == 'sms' and c.get('enabled', True) and c.get('alerts', True)), None)
    if not sms:
        raise ValueError('A call needs an enabled SMS alert channel.')
    return {**sms, **entry}


def validate(cfg):
    errors = []
    for c in cfg.get('channels', []):
        if c.get('type') != 'call':
            continue
        try:
            resolve(c, cfg)
            if not kinds(c) or not all(re.fullmatch(r'[a-z_]+', k) for k in kinds(c)):
                raise ValueError('Call kinds must be comma-separated alert kinds.')
            if not 1 <= int(c.get('hourly_cap', 2)) <= 12:
                raise ValueError('Call hourly_cap must be between 1 and 12.')
            if not 1 <= int(c.get('after_minutes', 10)) <= 10080:
                raise ValueError('Call after_minutes must be between 1 and 10080.')
            if str(c.get('during_quiet', 'false')).lower() not in ('true', 'false'):
                raise ValueError('Call during_quiet must be true or false.')
            from .escalation import person
            if person(cfg, c['id']) != person(cfg, c['sms_channel']):
                raise ValueError('Call and SMS must belong to the same person.')
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
    return errors


def escalates(cfg, alert):
    return alert['needs_reply'] or any(c.get('type') == 'call' and c.get('enabled', True)
        and alert['kind'] in kinds(c) for c in cfg.get('channels', []))


def eligible(store, entry, alert, step, now):
    if store.kv_get(f"reply-action:{alert['id']}", False):
        return False
    if (alert.get('meta') or {}).get('call_kind', alert['kind']) not in kinds(entry) or step == 0:
        return False
    # Earlier steps will not be retried after the scheduler reaches this step.
    sms = store._one("SELECT at FROM escalation_sends WHERE alert_id=? AND channel=? AND step<? AND state='sent'",
                     (alert['id'], entry['sms_channel'], step))
    return bool(sms and sms['at'] < now)


def allowed(cfg, store, entry, alert, row, step, now):
    if now < row['origin'] + int(entry.get('after_minutes', 10)) * 60:
        return False
    cap = min(int(c.get('hourly_cap', 2)) for c in cfg['channels'] if c['type'] == 'call')
    used = store._one('SELECT COUNT(*) AS n FROM oncall_call_attempts WHERE at>=?', (now - 3600,))['n']
    return used < cap


def spoken(text, choices):
    # Use render's wording, but remove addressing and reply instructions.
    text = '\n'.join(line for line in text.splitlines() if not line.startswith('Reply '))
    text = re.sub(r'https?://\S+|www\.\S+', '', text)
    text = re.sub(r'\b[A-Z][A-Z0-9_]*(?:-[A-Z0-9_]+)*-\d+\b|#\d+|\b[0-9a-f]{8}(?:-[0-9a-f]+)*\b', '', text, flags=re.I)
    text = re.sub(r'\(\s*\)', '', text)
    parts = ['This is CCC On-call.', ' '.join(text.split())[:1800]]
    for n, choice in enumerate(choices, 1):
        label = choice[0] if isinstance(choice, (tuple, list)) else choice
        # Apply the same redaction to choice labels.
        label = re.sub(r'https?://\S+|\b[A-Z][A-Z0-9_]*-\d+\b|#\d+|\b[0-9a-f]{8}(?:-[0-9a-f]+)*\b', '', str(label), flags=re.I)
        parts.append(f'Option {n}. {label}.')
    parts.append('Hang up and answer the text message with your choice in words. '
                 'If several alerts are waiting, use the alert number shown in the text. '
                 'This call cannot hear your answer or take keypad input.')
    return ' '.join(parts)
