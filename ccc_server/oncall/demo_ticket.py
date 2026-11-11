"""Private, file-backed ticket fixture for the no-install demonstration.

This implements the engine's queue interface, not WatchTower's scheduler.
"""
import json
from datetime import datetime, timezone


class DemoTickets:
    def __init__(self, root):
        self.path = root / 'tickets.json'
        self.item = None

    def save(self):
        self.path.write_text(json.dumps(self.item, indent=2) + '\n')

    def available(self):
        return True

    def add(self, queue, *, title, text, **kwargs):
        self.item = dict(ref=f'{queue}-1', project=queue, title=title, text=text,
                         status='open', history=[], needs_input=False)
        self.save()
        return self.item['ref']

    def start(self):
        self.item['status'] = 'claimed'
        self.item['history'].append(dict(event='claim', worker='demo'))
        self.save()

    def block(self):
        self.item.update(needs_input=True, blocked_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                         block_question='An empty cart crashes. Show zero or hide the total? Reply zero or hide.',
                         block_proposal={'recommend': 'zero'})
        self.item['history'].append(dict(event='block', question=self.item['block_question']))
        self.save()

    def blocked(self, queue):
        return [self.item] if self.item and self.item['needs_input'] else []

    def ls(self, queue, status='active', limit=0):
        return [self.item] if self.item and self.item['status'] != 'closed' else []

    def find(self, ref):
        return self.item if self.item and self.item['ref'] == ref else None

    def answer(self, ref, text):
        self.item.update(needs_input=False, answer=text)
        self.item['history'].append(dict(event='answer', text=text))
        self.save()

    def comment(self, ref, text):
        self.item['history'].append(dict(event='comment', text=text))
        self.save()

    def close(self, commit):
        self.item.update(status='closed', resolution=dict(commit=commit, summary='Empty cart test passes.'))
        self.item['history'].append(dict(event='close', commit=commit))
        self.save()
