"""Alertmanager grouped-alert intake, with conservative recovery reconciliation."""
import hashlib
import json
import urllib.parse
import uuid

from .ci_source import scrub
from .sources import Source, _Problem, _field, _finding, _text, _web_url

FIELDS = [_field('url', 'Alertmanager base URL'),
          _field('token_vault', 'Token', secret=True, optional=True),
          _field('every_s', 'Seconds between checks', '300', optional=True)]
MAX_GROUPS = 100
MAX_ALERTS = 1000
MAX_BODY = 1024 * 1024


class AlertmanagerSource(Source):
    type = 'alertmanager'

    def _setup(self, cfg):
        self.base = _web_url({'url': _text(cfg, 'url') or _text(cfg, 'base')}, 'url').rstrip('/')
        parts = urllib.parse.urlsplit(self.base)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('URL must not contain credentials, a query or a fragment.')

    def _groups(self):
        token = self._token('Alertmanager') if _text(self.cfg, 'token_vault') else ''
        headers = {'Accept': 'application/json', 'User-Agent': 'ccc-oncall/1'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if self._cooling_down():
            raise _Problem('Alertmanager asked to wait before the next request (Retry-After).')
        try:
            status, body = self._request('GET', self.base + '/api/v2/alerts/groups', headers=headers, timeout=20)
        except Exception:
            raise _Problem('Could not reach Alertmanager.')
        if status in (401, 403):
            raise _Problem(f'Alertmanager refused access (HTTP {status}).')
        if status == 429:
            raise _Problem('Alertmanager is rate limiting requests (HTTP 429).')
        if status != 200:
            raise _Problem(f'Alertmanager answered HTTP {status}.')
        try:
            if len(body) > MAX_BODY:
                raise ValueError
            rows = json.loads(body)
            if not isinstance(rows, list) or len(rows) > MAX_GROUPS:
                raise ValueError
            groups = {}
            total = 0
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('labels'), dict) or not isinstance(row.get('receiver'), dict) or not isinstance(row.get('alerts'), list):
                    raise ValueError
                total += len(row['alerts'])
                if total > MAX_ALERTS:
                    raise ValueError
                identity = json.dumps([row['receiver'], row['labels']], sort_keys=True, separators=(',', ':'))
                key = hashlib.sha256(identity.encode()).hexdigest()
                active = []
                for alert in row['alerts']:
                    status = alert.get('status') if isinstance(alert, dict) else None
                    if not isinstance(status, dict) or status.get('state') not in ('active', 'suppressed', 'unprocessed') or not isinstance(status.get('silencedBy'), list) or not isinstance(status.get('inhibitedBy'), list):
                        raise ValueError
                    if status['state'] == 'active' and not status['silencedBy'] and not status['inhibitedBy']:
                        active.append(alert)
                groups.setdefault(key, []).extend(active[:10])
            return groups
        except Exception:
            raise _Problem('Alertmanager response unavailable or exceeds supported limits.')

    def _check(self):
        self._update(baseline=True)
        return 'Alertmanager connected. Existing active groups are left alone.'

    def _poll(self):
        return self._update(baseline=not self._get('baselined', False))

    def _update(self, baseline=False):
        groups = self._groups()
        states = self._get('groups', {})
        recovered = self._get('recovered', {})
        for key, state in list(states.items()):
            if key not in groups:
                if state.get('fingerprint'):
                    recovered[state['fingerprint']] = True
                del states[key]
        findings = []
        for key, alerts in groups.items():
            if key in states:
                continue
            if baseline:
                states[key] = {}
                continue
            if not alerts or len(findings) >= 3:
                continue
            fp = 'alertmanager:' + hashlib.sha256((key + uuid.uuid4().hex).encode()).hexdigest()[:40]
            sample = []
            for alert in alerts[:5]:
                sample.append({'labels': alert.get('labels', {}), 'annotations': alert.get('annotations', {})})
            excerpt = scrub(json.dumps(sample, ensure_ascii=True), self._redact)[:3000]
            findings.append(_finding(fp, 'Alertmanager group firing', 'Active alerts (untrusted):\n' + excerpt))
            states[key] = {'fingerprint': fp}
        self._set('recovered', recovered)
        self._set('groups', states)
        self._set('baselined', True)
        return findings

    def reconcile(self, store, wt):
        recovered = self._get('recovered', {})
        reports = [r for r in store.reports(state='filed', limit=500)
                   if r.get('source') == 'source:' + self.id
                   and r.get('fingerprint') in recovered and r.get('ticket_ref')]
        if not reports:
            return
        start = int(self._get('reconcile_cursor', 0)) % len(reports)
        candidates = (reports[start:] + reports[:start])[:3]
        self._set('reconcile_cursor', (start + len(candidates)) % len(reports))
        for report in candidates:
            fp, ref = report['fingerprint'], report['ticket_ref']
            item = wt.find(ref)
            if not item:
                continue
            if item.get('status') == 'closed':
                recovered.pop(fp, None)
                self._set('recovered', recovered)
            else:
                # Recovery is evidence, not authorization to close a worker's
                # ticket or claim that its code has been repaired.
                wt.comment(ref, 'Alertmanager no longer reports this group as firing. '
                           'Verify the cause and close this ticket through the normal worker path; '
                           'monitoring recovery alone does not establish a code fix.')
                recovered.pop(fp, None)
                self._set('recovered', recovered)
