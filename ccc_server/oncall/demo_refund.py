"""A money hard stop, with a local-only refund ledger after an explicit reply."""
import json

QUESTION = ('The customer also needs a $5 refund for a duplicate charge. Money is a hard stop. '
            'This demo only records a simulated refund locally. Reply approve or decline.')


def run(engine, tickets, phone, project, commit, reply, tick, emit):
    from .api import save_config
    save_config(engine, {'minor_decisions': {'enabled': True}})
    evidence = tickets.refund(commit)
    if not tickets.item['needs_input']:
        raise RuntimeError('The refund must remain blocked on the owner.')
    ledger = project / 'refund.json'
    before = (project / 'cart.py').read_bytes()
    phone.state = 'Refund needs your decision'
    emit('On-call: Refund stopped. The policy cannot approve money for you.' if tickets.backend == 'watchtower'
         else 'On-call: Refund stopped in the fixture. Install WatchTower to exercise its real money policy.')
    tick()
    if ledger.exists() or (project / 'cart.py').read_bytes() != before:
        raise RuntimeError('Work advanced before the refund answer.')
    while True:
        answer = reply('decline').strip().lower()
        if answer in ('approve', 'decline'):
            break
        emit('Reply approve or decline. No refund has happened.')
    phone.receive(answer)
    tick()
    if not tickets.answered(answer):
        raise RuntimeError('Refund answer did not reach the ticket.')
    ledger.write_text(json.dumps(dict(state=answer, simulated=True, amount=5)) + '\n')
    emit('You: ' + ('Approved a simulated refund in the local ledger.' if answer == 'approve'
                    else 'Declined the refund. No money moved.'))
    return dict(answer=answer, policy=tickets.backend, blocked=True, evidence=evidence, simulated=True)
