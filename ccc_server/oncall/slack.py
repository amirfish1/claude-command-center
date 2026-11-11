"""Slack bot Web API. Only a direct message with the configured owner."""
from .chat_common import ChatChannel


class SlackChannel(ChatChannel):
    type = 'slack'
    base = 'https://slack.com/api'

    def destination(self, verify=False):
        dest = self._get('destination')
        if not dest:
            me = self.call('POST', '/auth.test', {})
            if me['user_id'] == self.owner:
                raise OSError('Owner must be a different user from the bot.')
            self._set('self', me['user_id'])
            dest = self.call('POST', '/conversations.open', {'users': self.owner})['channel']['id']
            verify = True
        if verify:
            info = self.call('GET', '/conversations.info', query={'channel': dest})['channel']
            if not info.get('is_im') or info.get('user') != self.owner:
                raise OSError('Slack requires a direct message with the owner.')
            self._set('destination', dest)
        return dest

    def _check(self):
        self.destination(verify=True)
        return True, 'Connected to your direct message.'

    def _send(self, msg):
        body = {'channel': self.destination(verify=True), 'text': msg.text[:39000],
                'unfurl_links': False, 'unfurl_media': False}
        if msg.reply_to:
            body['thread_ts'] = msg.reply_to
        data = self.call('POST', '/chat.postMessage', body)
        return self.remember(data.get('ts'), msg)

    def _poll(self):
        if not self.roots():
            return []
        dest = self.destination()
        cursors = dict(self._get('cursors', {}) or {})
        # Round robin: at most one thread page per poll, regardless of history.
        roots = list(self.roots())
        index = int(self._get('index', 0)) % len(roots)
        root = roots[index]
        state = cursors.get(root, {})
        query = {'channel': dest, 'ts': root, 'limit': 15,
                 'oldest': state.get('oldest', root), 'inclusive': 'false'}
        if state.get('page'):
            query['cursor'] = state['page']
        data = self.call('GET', '/conversations.replies', query=query)
        out = []
        latest = state.get('latest', query['oldest'])
        for row in data.get('messages', []):
            ident = row.get('ts', '')
            latest = max(latest, ident)
            msg = self.inbound(ident, row.get('user'), row.get('text'), row.get('thread_ts'),
                               bot=bool(row.get('bot_id') or row.get('subtype')))
            if msg:
                out.append(msg)
        page = data.get('response_metadata', {}).get('next_cursor', '')
        cursors[root] = {'page': page, 'oldest': query['oldest'] if page else latest, 'latest': latest}
        return self.batch(out, {'cursors': cursors, 'index': index if page else index + 1})
