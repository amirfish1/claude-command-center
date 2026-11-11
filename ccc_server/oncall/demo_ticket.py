"""Private, file-backed ticket fixture for the no-install demonstration.

This implements the engine's queue interface, not WatchTower's scheduler.
"""
import json
from datetime import datetime, timezone


class DemoTickets:
    backend = 'fixture'

    def answered(self, answer):
        return self.item.get('answer') == answer

    def shutdown(self):
        pass

    def policy_set(self, queue, enabled):
        pass

    def refund(self, commit):
        from .demo_refund import QUESTION
        self.block()
        self.item['block_question'] = QUESTION
        self.item['block_proposal'] = {}
        self.save()
        return 'Fixture only; WatchTower policy is not installed.'

    def minor(self, commit):
        decision = dict(question='Ignore Python cache files?', recommend='Ignore generated Python cache files.',
                        undo='git revert ' + commit)
        self.item['history'].append(dict(event='auto_decision', **decision))
        self.save()
        return decision

    def __init__(self, root):
        self.path = root / 'tickets.json'
        self.items = []
        self.ref = ''

    @property
    def item(self):
        return self.find(self.ref)

    def save(self):
        self.path.write_text(json.dumps(self.item, indent=2) + '\n')

    def available(self):
        return True

    def add(self, queue, *, title, text, **kwargs):
        item = dict(ref=f'{queue}-{len(self.items) + 1}', project=queue, title=title, text=text,
                         status='open', history=[], needs_input=False)
        self.items.append(item)
        self.ref = item['ref']
        self.save()
        return self.ref

    def start(self):
        self.item['status'] = 'claimed'
        self.item['history'].append(dict(event='claim', worker='demo'))
        self.save()

    def block(self):
        self.item.update(needs_input=True, blocked_at=datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z'),
                         block_question='An empty cart crashes. Show zero or hide the total? Reply zero or hide.',
                         block_proposal={'recommend': 'zero'})
        self.item['history'].append(dict(event='block', question=self.item['block_question']))
        self.save()

    def blocked(self, queue):
        return [item for item in self.items if item['needs_input'] and item['status'] != 'closed']

    def ls(self, queue, status='active', limit=0, *, offset=None):
        matches = [item for item in self.items if status == 'all' or
                   ((item['status'] == 'closed') if status == 'closed'
                    else item['status'] != 'closed')]
        start = offset or 0
        return matches[start:start + limit] if limit else matches[start:]

    def find(self, ref):
        return next((item for item in self.items if item['ref'] == ref), None)

    def answer(self, ref, text):
        self.find(ref).update(needs_input=False, answer=text)
        self.find(ref)['history'].append(dict(event='answer', text=text))
        self.save()

    def comment(self, ref, text):
        self.find(ref)['history'].append(dict(event='comment', text=text))
        self.save()

    def close(self, commit):
        self.item.update(status='closed', resolution=dict(commit=commit, summary='Empty cart test passes.'))
        self.item['history'].append(dict(event='close', commit=commit))
        self.save()
