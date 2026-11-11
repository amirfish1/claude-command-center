"""An unchanged question from one session is sent once, not every turn."""
import json

from ccc_server.oncall import session_signals as signals
from ccc_server.oncall.engine import Engine


def _turn(path, uuid, text):
    row = {'type': 'assistant', 'uuid': uuid,
           'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}}
    path.write_text(json.dumps(row) + '\n')


def test_same_question_across_idle_periods_alerts_once(tmp_path):
    clock = type('C', (), {'now': 1000.0})()
    eng = Engine(tmp_path, flag_on=lambda: True, clock=lambda: clock.now)
    cfg = eng.config()
    cfg.update(enabled=True, queue='TEST')
    eng.save_config(cfg)
    transcript = tmp_path / 't.jsonl'
    eng.deps = type('D', (), {})()
    eng.deps.permission = lambda sid: None
    eng.deps.question = lambda sid, store, since=0: signals.question(store, sid, transcript)
    idle, busy = {'s1': {'state': 'idle'}}, {'s1': {'state': 'working'}}

    def period():
        signals.observe(eng, idle)
        clock.now += signals.GRACE_S
        signals.observe(eng, idle)
        signals.observe(eng, busy)

    _turn(transcript, 'u1', 'Should I keep the old client?')
    period()
    _turn(transcript, 'u2', 'Should I keep the old client?')
    period()
    assert len(eng.store.alerts()) == 1
    _turn(transcript, 'u3', 'Should I drop the old client?')
    period()
    assert len(eng.store.alerts()) == 2


def _label_alert(tmp_path, label):
    from ccc_server.oncall import alerts
    eng = Engine(tmp_path, flag_on=lambda: True, clock=lambda: 1000.0)
    cfg = eng.config()
    cfg.update(enabled=True, queue='TEST')
    eng.save_config(cfg)
    transcript = tmp_path / 't.jsonl'
    _turn(transcript, 'u1', 'Should I keep the old client?')
    eng.deps = type('D', (), {})()
    if label is not None:
        eng.deps.label = label
    eng.deps.permission = lambda sid: None
    eng.deps.question = lambda sid, store, since=0: signals.question(store, sid, transcript)
    signals.observe(eng, {'9977c333aa': {'state': 'idle'}})
    eng.clock = lambda: 2000.0
    signals.observe(eng, {'9977c333aa': {'state': 'idle'}})
    return alerts.render(eng.store.alerts()[0])[1].splitlines()


def test_alert_label_named_unnamed_and_layout(tmp_path):
    lines = _label_alert(tmp_path / 'a', lambda sid: ('Fix login', 'webapp'))
    assert lines[1:3] == ['Fix login · webapp', 'Should I keep the old client?']
    lines = _label_alert(tmp_path / 'b', lambda sid: ('', ''))
    assert lines[1] == 'Agent 9977c333' and lines[2] == 'Should I keep the old client?'
    lines = _label_alert(tmp_path / 'c', None)
    assert lines[1] == 'Agent 9977c333'
    lines = _label_alert(tmp_path / 'd', lambda sid: ('webapp', 'webapp'))
    assert lines[1] == 'webapp'


import pytest

from ccc_server.oncall import alerts


def _footer(action):
    return ('Which one?\n\n<session-state>\nNEXT_STEP_USER: Pick one\n'
            f'NEXT_ACTION: {action}\n</session-state>')


def _choices(n):
    return json.dumps([{'label': f'Opt {i}', 'reply': f'reply {i}'} for i in range(n)])


@pytest.mark.parametrize('action,expected', [
    (_choices(1), 1), (_choices(3), 3), (_choices(4), 0), ('null', 0),
    ('[{"label": "x"', 0), ('[{"label": "x"}]', 0), ('{"label":"A","reply":"b"}', 1)])
def test_footer_choices_become_ntfy_buttons(action, expected):
    signal = signals.from_message(_footer(action), 't')
    assert signal and signal['body'] == 'Pick one'
    alert = {'id': 7, 'kind': 'plain_question', 'title': 'x', 'body': signal['body'],
             'meta': signal['meta']}
    choices = alerts.render(alert)[2]
    assert len(choices) == expected
    if expected:
        assert choices[0][1].startswith(('reply 0', 'b'))


def test_ntfy_button_publishes_reply_text():
    from ccc_server.oncall import channels
    from ccc_server.oncall.store import Outbound
    payloads = []
    ch = channels.build({'id': 'p', 'type': 'ntfy', 'topic': 'alerts', 'server': 'http://x'},
                        kv=None, secret=lambda name: '', clock=lambda: 1000.0)
    ch.http = lambda method, url, headers=None, data=None, timeout=0: (
        payloads.append(json.loads(data)) or (200, b'{"id":"1"}'))
    ch.send(Outbound(text='q', thread='TOK', choices=(('Keep', 'keep the old one'),)))
    action = payloads[0]['actions'][0]
    assert action['label'] == 'Keep' and action['body'] == 'TOK keep the old one'


def _rig(tmp_path):
    clock = type('C', (), {'now': 1000.0})()
    eng = Engine(tmp_path, flag_on=lambda: True, clock=lambda: clock.now)
    cfg = eng.config()
    cfg.update(enabled=True, queue='TEST')
    eng.save_config(cfg)
    transcript = tmp_path / 't.jsonl'
    eng.deps = type('D', (), {})()
    eng.deps.permission = lambda sid: None
    eng.deps.question = lambda sid, store, since=0: signals.question(store, sid, transcript)

    def period(uuid):
        _turn(transcript, uuid, 'Should I approve the deploy?')
        idle = {'s1': {'state': 'idle'}}
        signals.observe(eng, idle)
        clock.now += signals.GRACE_S
        signals.observe(eng, idle)
    return eng, period, lambda: signals.observe(eng, {'s1': {'state': 'working'}})


def test_same_question_after_owner_answered_alerts_again(tmp_path):
    eng, period, busy = _rig(tmp_path)
    period('u1')
    assert eng.store.alert_close(eng.store.alerts()[0]['id'], 'answered', 'not yet')
    busy()
    period('u2')
    assert len(eng.store.alerts()) == 2
    busy()
    period('u3')  # the second one was cleared, not answered
    assert len(eng.store.alerts()) == 2


def test_cleared_without_answer_then_repeated_gives_none(tmp_path):
    eng, period, busy = _rig(tmp_path)
    period('u1')
    busy()  # clears the open alert unanswered
    assert eng.store.alerts()[0]['state'] == 'cleared'
    period('u2')
    assert len(eng.store.alerts()) == 1
