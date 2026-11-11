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
