from ccc_server.oncall import customers
from ccc_server.oncall.store import Inbound
from tests.test_oncall_engine import rig


def test_held_report_cannot_grow_without_limit(rig):
    for i in range(10):
        customers.intake(rig.eng.config(), rig.eng.store, rig.wt,
            Inbound('mail', str(i), 'stranger@example.test', 'x' * 8000))
    reports = rig.eng.store.reports()
    assert len(reports) == 1
    assert len(reports[0]['text']) <= 20000
