"""Focused follow-up coverage for Home and missing alert context."""
import json
from pathlib import Path
import subprocess

import pytest

from ccc_server.oncall import alerts, api, home
from tests.test_oncall_engine import rig  # noqa: F401


def test_home_reply_contract_and_real_stuck_alert(rig):
    store = rig.eng.store
    store.alert_upsert('permission', kind='permission', title='Checkout',
                       body='Run the test command?', session_id='s')
    store.alert_upsert('question', kind='question', title='Checkout',
                       body='Which redirect?', session_id='s',
                       meta={'choices': [['Keep', 'Keep the redirect'], ['Remove', 'Remove it']]})
    stuck = rig.eng.raise_stuck('SHOP', 'No worker has claimed the oldest ticket.', 'turn')
    assert stuck['needs_reply'] is False
    cards = {card['kind']: card for card in home.summary(rig.eng)['needs_you']}
    permission = cards['permission']
    assert permission['session_only'] and not permission['choices'] and not permission['free_text']
    assert 'Open this session' in permission['reply_help']
    assert 'allow or deny' not in json.dumps(permission)
    question = cards['question']
    assert 'Accepted replies:' in question['detail']
    assert 'Keep the redirect' in question['detail']
    assert 'option number' in question['reply_help']
    stuck = cards['stuck']
    assert stuck['choices'] == [] and not stuck['free_text'] and not stuck['session_only']
    assert 'No worker has claimed' in stuck['detail']
    assert 'No reply action' in stuck['reply_help']


@pytest.mark.parametrize('late_question', ['', 'Keep the legacy redirect?'])
def test_decision_defers_once_then_retries_with_ticket_context(rig, late_question):
    ref = rig.wt.add('SHOP', title='Checkout redirect', text='Legacy clients still use it.')
    rig.wt.park(ref, '')
    collect = lambda: alerts.collect_decisions(rig.eng.config(), rig.eng.store, rig.wt, now=rig.clock())
    assert collect() == 0
    assert not rig.eng.store.alerts()
    rig.wt.tickets[ref]['block_question'] = late_question
    assert collect() == 1
    alert = rig.eng.store.alerts()[0]
    subject, text, _ = alerts.render(alert)
    assert ref in subject and 'Checkout redirect' in subject
    assert 'The agent needs a decision.' not in text
    if late_question:
        assert alert['body'] == late_question
    else:
        assert 'could not read the decision question' in alert['body']
        assert ref in alert['body'] and 'Open the ticket' in alert['body']
    assert collect() == 0
    assert len(rig.eng.store.alerts()) == 1


def test_missing_decision_that_clears_is_not_raised_later(rig):
    ref = rig.wt.add('SHOP', title='Checkout redirect', text='')
    rig.wt.park(ref, '')
    alerts.collect_decisions(rig.eng.config(), rig.eng.store, rig.wt)
    rig.wt.tickets[ref]['needs_input'] = False
    alerts.collect_decisions(rig.eng.config(), rig.eng.store, rig.wt)
    assert not rig.eng.store.alerts()
    rig.wt.park(ref, '')
    assert alerts.collect_decisions(rig.eng.config(), rig.eng.store, rig.wt) == 0


@pytest.mark.parametrize('last', ['', 'The redirect fails when the cookie expires.'])
def test_generic_stop_reason_defers_then_reads_actual_transcript(rig, last):
    context = {'last': '<session-state>idle'}
    rig.deps.context = lambda sid, store: context
    reason = 'The agent process exited unexpectedly (exit 1).'
    assert api.session_stopped('s', 'Checkout stopped', reason, 'turn', eng=rig.eng) is None
    assert not rig.eng.store.alerts()
    context['last'] = last
    api.observe_sessions({}, {}, eng=rig.eng)
    alert = rig.eng.store.alerts()[0]
    assert reason in alert['body']
    if last:
        assert last in alert['body']
        assert 'could not read' not in alert['body']
    else:
        assert 'could not read the last assistant message' in alert['body']
    subject, text, _ = alerts.render(alert)
    assert subject == f"Checkout stopped (#{alert['id']})"
    assert text.count('Checkout') == 1 and 'stopped stopped' not in text
    api.observe_sessions({}, {}, eng=rig.eng)
    assert len(rig.eng.store.alerts()) == 1


def test_stop_with_transcript_is_immediate_and_keeps_reason(rig):
    rig.deps.context = lambda sid, store: {'last': 'Need to fix the redirect.'}
    alert = api.session_stopped('s', '', 'Process exited.', 'turn', eng=rig.eng)
    assert 'Need to fix the redirect.' in alert['body'] and 'Process exited.' in alert['body']
    assert alert['title'] == 'An unnamed session'


@pytest.mark.parametrize('kind', ['decision', 'send'])
def test_subject_contains_title_and_ref_without_inventing_session(kind):
    subject, text, _ = alerts.render({'id': 4, 'kind': kind, 'title': 'Checkout redirect',
                                    'ref': 'SHOP-3', 'body': 'Keep it?', 'meta': {}})
    assert 'Checkout redirect' in subject and 'SHOP-3' in subject
    assert text.count('Checkout redirect') == 1
    subject, _, _ = alerts.render({'id': 4, 'kind': kind, 'title': '', 'ref': 'SHOP-3'})
    assert 'SHOP-3' in subject and 'session' not in subject.lower()


def test_home_js_renders_reply_help_as_text():
    # Exercise the actual card function without a server or browser connection.
    source = Path('static/oncall-home.js').read_text()
    function = source[source.index('  function needCard(item) {'):source.index('\n  function note(text)')]
    script = r'''
const vm = require('node:vm');
function el(tag, cls, text) {
  return {tag, cls, text, dataset: {}, children: [],
    append(...nodes) {this.children.push(...nodes);},
    setAttribute() {}, addEventListener() {}};
}
const context = {el, sending: new Set(), drafts: new Map(), cardNotes: new Map(), window: {}};
vm.createContext(context);
vm.runInContext(FUNCTION, context);
const card = context.needCard({id: 1, headline: 'Checkout', detail: 'Which redirect?',
  choices: [], free_text: false, reply_help: 'Reply <Keep> or Remove.'});
const help = card.children.find(child => child.text === 'Reply <Keep> or Remove.');
if (!help || help.tag !== 'div') throw new Error('Reply help was not rendered as text');
'''.replace('FUNCTION', json.dumps(function))
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
