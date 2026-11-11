"""Mattermost REST posts, restricted to owner replies in known threads."""
from .chat_common import ChatChannel, quote


class MattermostChannel(ChatChannel):
    type = 'mattermost'

    def _check(self):
        me = self.call('GET', '/api/v4/users/me')
        owner = self.call('GET', '/api/v4/users/' + quote(self.owner))
        if owner.get('is_bot') or me['id'] == self.owner:
            raise OSError('Owner must be a human account different from the bot.')
        self._set('self', me['id'])
        self.call('GET', '/api/v4/channels/' + quote(self._opt('channel_id')))
        return True, 'Connected. Only the configured owner can answer.'

    def _send(self, msg):
        if not self._get("self"):
            self._check()
        data = self.call('POST', '/api/v4/posts', {
            'channel_id': self._opt('channel_id'), 'message': msg.text[:16000],
            'root_id': msg.reply_to or ''})
        if self._get('cursor') is None:
            self._set('cursor', data['id'])
        return self.remember(data.get('id'), msg)

    def _poll(self):
        cursor = self._get('cursor')
        if cursor is None:
            return []
        data = self.call('GET', '/api/v4/channels/' + quote(self._opt('channel_id')) + '/posts',
                         query={'after': cursor, 'per_page': 100, 'page': 0})
        rows = [data['posts'][ident] for ident in reversed(data.get('order', []))]
        out = []
        for row in rows:
            msg = self.inbound(row['id'], row.get('user_id'), row.get('message'), row.get('root_id'),
                               bot=bool(row.get('type') or row.get('props', {}).get('from_webhook')))
            if msg:
                out.append(msg)
        return self.batch(out, {'cursor': rows[-1]['id'] if rows else cursor})
