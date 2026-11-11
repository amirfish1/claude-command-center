"""Adversarial routing checks. All stores and transports are local fixtures."""
from ccc_server.oncall import customers, replies
from ccc_server.oncall.store import Inbound
from tests.test_oncall_engine import rig


def test_report_thread_cannot_cross_sender_or_channel(rig):
    store = rig.eng.store
    report, _ = store.report_upsert('channel:mail', 'first', title='bug', text='original',
        reporter_channel='mail', reporter_address='customer@example.test')
    for channel, sender in [('mail', 'stranger@example.test'), ('tg', 'customer@example.test')]:
        customers.intake(rig.eng.config(), store, rig.wt,
            Inbound(channel, channel, sender, 'injected', thread=report['thread']))
    assert store.report(report['id'])['text'] == 'original'

