#!/usr/bin/env python3
"""Crafted Authentication-Results over local TLS SMTP/IMAP, without agents.

GreenMail transports messages but does not verify DMARC, DKIM or SPF. The
trusted pass below is fixture evidence, not a live-provider or DNS claim.
"""
from pathlib import Path
import sys

if '--with-agent' in sys.argv:
    raise SystemExit('This mail-authentication proof does not run an agent.')
source = Path(__file__).with_name('oncall-proof.py').read_text()

def replace(old, new):
    global source
    if source.count(old) != 1:
        raise RuntimeError('Base proof changed; inspect its isolation before updating anchors')
    source = source.replace(old, new)

replace("msg['Authentication-Results'] = 'fixture-mail; dmarc=pass header.from=example.test'", """if not subject.startswith('No authentication'):
            service = 'untrusted-hop' if subject.startswith('Forged authentication') else 'fixture-mail'
            msg['Authentication-Results'] = service + '; dmarc=pass header.from=example.test'""")
replace("cli('customer', 'add', 'reporter@example.test')", """cli('customer', 'add', 'reporter@example.test')
        eng.wt._ok('config', '-q', eng.config()['queue'], '--auto-drain', 'off')
        for title, state in [('No authentication', 'held'),
                             ('Forged authentication', 'held'), ('Trusted authentication', 'filed')]:
            mail('reporter@example.test', title, 'Fixture report: ' + title)
            eng.tick(force=True)
            report = next(r for r in eng.store.reports() if r['title'] == title)
            assert report['state'] == state, (title, report['state'])
            if state == 'held':
                alert = eng.store.alert_by_key('report:' + str(report['id']))
                assert 'sender could not be verified' in alert['body']
            print('TLS mail authentication: ' + title + ' -> ' + state, flush=True)
        original = next(r for r in eng.store.reports() if r['title'] == 'Trusted authentication')
        before = len(eng.store.reports())
        mail('reporter@example.test', 'No authentication [#' + original['thread'] + ']', 'Forged continuation')
        eng.tick(force=True)
        assert len(eng.store.reports()) == before + 1
        assert 'Forged continuation' not in json.dumps(eng.wt.find(original['ticket_ref']))
        mail('reporter@example.test', 'Reply [#' + original['thread'] + ']', 'Verified continuation')
        eng.tick(force=True)
        assert len(eng.store.reports()) == before + 1
        assert 'Verified continuation' in json.dumps(eng.wt.find(original['ticket_ref']))
        print('TLS mail continuation: forged held separately; verified token reply attached', flush=True)
        # Leave the base proof's fixture walk unchanged after our extra checks.
""")
# Re-enter this adapter in the isolated child; the base proof owns teardown.
exec(compile(source, __file__, 'exec'), globals())
