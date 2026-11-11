"""Command-only configuration and a no-network escalation walk."""
import argparse
import json
from . import escalation


def run(argv, client, io):
    parser = argparse.ArgumentParser(prog='ccc oncall ' + argv[0])
    parser.add_argument('action', choices=['add', 'list', 'rm', 'test', 'weekly', 'hours', 'override', 'clear', 'urgent'])
    parser.add_argument('name', nargs='?', default='')
    parser.add_argument('--channels', default='')
    parser.add_argument('--person', default='owner')
    parser.add_argument('--after', type=float, default=0, help='Minutes after the alert')
    parser.add_argument('--may-answer', default='decision,permission')
    parser.add_argument('--quiet', default='', help='HH:MM-HH:MM, host local time')
    parser.add_argument('--time', default='09:00', help='UTC weekly handover')
    parser.add_argument('--weekday', type=int, default=0, help='Monday=0')
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--end', type=int, default=24)
    parser.add_argument('--for-hours', type=float, default=12)
    args = vars(parser.parse_args(argv[1:]))
    code, result = client.post('escalation', dict(args, group=argv[0]))
    io.out(json.dumps(result, indent=2))
    return 0 if code == 200 and result.get('ok') else 1


def command(eng, body):
    cfg = eng.config()
    group, action = body.get('group'), body.get('action')
    key = {'person': 'people', 'escalate': 'escalation', 'rota': 'rota'}.get(group)
    if not key:
        raise ValueError('Unknown escalation command.')
    if group == 'escalate' and action == 'urgent':
        aid = int(body.get('name', '0'))
        alert = eng.store.alert(aid)
        if not alert or alert['state'] != 'open' or not alert['needs_reply']:
            raise ValueError('Choose an open alert that needs a reply.')
        meta = dict(alert.get('meta') or {}, skip_wait=True)
        eng.store._run('UPDATE alerts SET meta=? WHERE id=?', (json.dumps(meta), aid))
        eng.store._run('UPDATE escalation_jobs SET due=? WHERE alert_id=? AND done=0', (eng.clock(), aid))
        return {'ok': True, 'alert_id': aid, 'skip_wait': True}
    if action == 'list':
        return {'ok': True, key: cfg.get(key, {} if key == 'rota' else []), 'oncall_now': escalation.who(cfg, eng.clock())}
    if group == 'escalate' and action == 'test':
        from .escalation_preview import preview
        return preview(eng)
    if group == 'person':
        rows = list(cfg.get(key, []))
        name = body.get('name', '')
        if action == 'rm':
            removed = next((p for p in rows if p['id'] == name), None)
            if removed:
                cfg['channels'] = [c for c in cfg['channels'] if c['id'] not in removed['channels']]
            rows = [p for p in rows if p['id'] != name]
        elif action == 'add':
            if name == 'owner' or any(p['id'] == name for p in rows):
                raise ValueError('That person already exists or is reserved.')
            p = {'id': name, 'channels': body.get('channels', '').split(','),
                 'may_answer': body.get('may_answer', 'decision,permission').split(',')}
            if body.get('quiet'):
                start, end = body['quiet'].split('-')
                p['quiet_hours'] = {'start': start, 'end': end}
            rows.append(p)
        else:
            raise ValueError('Use person add, list or rm.')
        cfg[key] = rows
    elif group == 'escalate':
        rows = list(cfg.get(key, []))
        if action == 'add':
            rows.append({'after': float(body.get('after', 0)) * 60, 'person': body.get('person', 'owner'),
                         'channels': body.get('channels', '').split(',')})
        elif action == 'rm':
            n = int(body.get('name', '0'))
            if not 1 <= n <= len(rows):
                raise ValueError('Step number is out of range.')
            rows.pop(n - 1)
        else:
            raise ValueError('Use escalate add, list, rm or test.')
        cfg[key] = rows
    else:
        rota = dict(cfg.get('rota') or {})
        if action == 'weekly':
            rota = {'mode': 'weekly', 'people': body.get('name', '').split(','),
                    'time': body.get('time', '09:00'), 'weekday': body.get('weekday', 0)}
        elif action == 'hours':
            rota = {'mode': 'hours', 'hours': [*rota.get('hours', []),
                    {'person': body.get('name'), 'start': body.get('start', 0), 'end': body.get('end', 24)}]}
        elif action == 'override':
            hours = float(body.get('for_hours', 12))
            if not 0 < hours <= 168:
                raise ValueError('Override must last between zero and 168 hours.')
            rota['override'] = {'person': body.get('name'), 'until': eng.clock() + hours * 3600}
        elif action == 'clear':
            rota = {}
        else:
            raise ValueError('Use rota weekly, hours, override, clear or list.')
        cfg[key] = rota
    eng.save_config(cfg)
    return {'ok': True, key: cfg[key]}
