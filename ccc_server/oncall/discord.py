"""Discord REST messages. Use Reply on the alert in the configured channel."""
from .chat_common import ChatChannel, quote


class DiscordChannel(ChatChannel):
    type = 'discord'
    base = 'https://discord.com/api/v10'
    auth_scheme = 'Bot'

    def _check(self):
        me = self.call('GET', '/users/@me')
        if me['id'] == self.owner:
            raise OSError('Owner must be a different user from the bot.')
        self._set('self', me['id'])
        self.call('GET', '/channels/' + quote(self._opt('channel_id')))
        return True, 'Connected. Only the configured owner can answer.'

    def endpoint(self):
        return '/channels/' + quote(self._opt('channel_id')) + '/messages'

    def _send(self, msg):
        body = {'content': msg.text[:2000], 'allowed_mentions': {'parse': []}}
        if msg.reply_to:
            body['message_reference'] = {'message_id': msg.reply_to}
        data = self.call('POST', self.endpoint(), body)
        if self._get('cursor') is None:
            self._set('cursor', data['id'])
        return self.remember(data.get('id'), msg)

    def _poll(self):
        cursor = self._get('cursor')
        if cursor is None:
            return []
        rows = self.call('GET', self.endpoint(), query={'after': cursor, 'limit': 100})
        out = []
        for row in sorted(rows, key=lambda r: int(r['id'])):
            author = row.get('author', {})
            msg = self.inbound(row['id'], author.get('id'), row.get('content'),
                               row.get('message_reference', {}).get('message_id'),
                               bot=bool(author.get('bot') or row.get('webhook_id')))
            if msg:
                out.append(msg)
        latest = max([int(cursor)] + [int(row['id']) for row in rows])
        return self.batch(out, {'cursor': str(latest)})
