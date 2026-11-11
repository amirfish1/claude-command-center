from ccc_server.oncall.demo_ticket import DemoTickets


def test_demo_queue_supports_closed_page(tmp_path):
    queue = DemoTickets(tmp_path)
    queue.add('DEMO', title='Fixture', text='Fixture')
    assert queue.ls('DEMO', 'closed', 100, offset=0) == []
    queue.close('test-commit')
    assert len(queue.ls('DEMO', 'closed', 100, offset=0)) == 1
    assert queue.ls('DEMO', 'closed', 100, offset=100) == []
