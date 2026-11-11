"""A reply is actionable as soon as one owner channel received its alert."""
from tests.test_oncall_engine import rig


def test_reply_does_not_wait_for_second_channel_budget(rig):
    cfg = rig.eng.config()
    cfg['hourly_cap'] = 1
    rig.eng.save_config(cfg)
    rig.eng.store.alert_upsert('question', kind='question', title='Choose', body='Which?', session_id='fixture')
    rig.tick()
    assert len(rig.chan('tg').sent) == 1
    assert not rig.chan('mail').sent
    rig.msg('tg', 'use the fallback', '4242')
    rig.tick()
    assert rig.deps.injected == [('fixture', 'use the fallback')]
