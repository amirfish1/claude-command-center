"""Matrix HTTP sync. Unencrypted rooms only; limited timelines fail closed."""
import json
import uuid
from .chat_common import ChatChannel, quote


class MatrixChannel(ChatChannel):
    type = 'matrix'

    def room(self):
        return '/_matrix/client/v3/rooms/' + quote(self._opt('channel_id'))

    def _check(self):
        me = self.call('GET', '/_matrix/client/v3/account/whoami')
        if me['user_id'] == self.owner:
            raise OSError('Use a separate account for the bot.')
        self._set('self', me['user_id'])
        states = self.call('GET', self.room() + '/state')
        if any(row.get('type') == 'm.room.encryption' for row in states):
            raise OSError('Encrypted Matrix rooms are not supported.')
        return True, 'Connected. Use an unencrypted room and reply in the alert thread.'

    def sync(self, since=None):
        query = {'timeout': 1000, 'filter': json.dumps({'room': {
            'rooms': [self._opt('channel_id')], 'timeline': {'limit': 100}}})}
        if since:
            query['since'] = since
        return self.call('GET', '/_matrix/client/v3/sync', query=query)

    def _send(self, msg):
        self._check()
        if self._get('cursor') is None:
            self._set('cursor', self.sync()['next_batch'])
        body = {'msgtype': 'm.text', 'body': msg.text[:16000]}
        if msg.reply_to:
            body['m.relates_to'] = {'rel_type': 'm.thread', 'event_id': msg.reply_to,
                                   'm.in_reply_to': {'event_id': msg.reply_to}}
        data = self.call('PUT', self.room() + '/send/m.room.message/' + uuid.uuid4().hex, body)
        return self.remember(data.get('event_id'), msg)

    def _poll(self):
        cursor = self._get('cursor')
        if cursor is None:
            return []
        data = self.sync(cursor)
        timeline = data.get('rooms', {}).get('join', {}).get(self._opt('channel_id'), {}).get('timeline', {})
        if timeline.get('limited'):
            raise OSError('Matrix timeline has a gap. Cursor retained; review room history.')
        out = []
        for row in timeline.get('events', []):
            content = row.get('content', {})
            rel = content.get('m.relates_to', {})
            root = rel.get('event_id') if rel.get('rel_type') == 'm.thread' else rel.get('m.in_reply_to', {}).get('event_id')
            if row.get('type') != 'm.room.message' or content.get('msgtype') != 'm.text' or rel.get('rel_type') == 'm.replace':
                continue
            text = content.get('body', '')
            if rel.get('m.in_reply_to') and text.startswith('> '):
                _, separator, answer = text.partition('\n\n')
                if separator:
                    text = answer
            msg = self.inbound(row['event_id'], row.get('sender'), text, root)
            if msg:
                out.append(msg)
        return self.batch(out, {'cursor': data['next_batch']})
