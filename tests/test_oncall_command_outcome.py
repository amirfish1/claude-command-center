import sys
from ccc_server.oncall import alerts, customers
from ccc_server.oncall.channels import CommandChannel
from ccc_server.oncall.store import Store


def test_command_accepts_then_exits_nonzero(tmp_path):
    script = tmp_path / 'send.py'
    log = tmp_path / 'accepted'
    script.write_text(f"from pathlib import Path\nimport sys\nwith Path({str(log)!r}).open('a') as f: f.write(sys.stdin.read()+'\\n')\nsys.exit(7)\n")
    store = Store(tmp_path / 'store')
    channel = CommandChannel({'id': 'local', 'send': f'{sys.executable} {script}'}, kv=store)
    alert, _ = store.alert_upsert('one', kind='decision', title='Choose', body='')
    for now in (1000, 1120, 1240, 9000):
        alerts.send_alert(store, [channel], alert, now=now)
    assert log.read_text().count('Choose') == 2
    assert log.read_text().count('This may be a repeat.') == 1
    msg = store.customer_message_add(ref='', kind='tell', channel='local', address='customer@example.test',
                                     text='Customer text', state='pending')
    for now in (1000, 1120, 9000):
        customers.send_message(store, {'local': channel}, None, msg['id'], now=now)
    assert log.read_text().count('Customer text') == 1
    store.close()
