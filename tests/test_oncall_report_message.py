"""A filed report is not evidence of a worker starting."""
from ccc_server.oncall import cli


def test_report_does_not_claim_dispatch_when_only_filing_is_confirmed():
    lines = []
    calls = []
    def post(base, route, body, **kwargs):
        calls.append((route, body))
        return 200, {'ok': True, 'ref': 'SHOP-1'}
    assert cli.run(['report', 'Example bug'], base='http://localhost',
                   get=lambda *a, **k: {}, post=post,
                   io=cli.IO(out=lines.append)) == 0
    assert calls == [('/api/oncall/report', {'text': 'Example bug', 'title': ''})]
    assert lines == ['Filed as SHOP-1. Worker start is not confirmed.']
