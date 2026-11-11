"""GlitchTip uses HTTP 422 for the Sentry sort name."""
import json
from ccc_server.oncall.sources import SentrySource


def test_glitchtip_slug_falls_back_to_project_route():
    calls = []
    def http(method, url, **kwargs):
        calls.append(url)
        if '/organizations/' in url:
            return 422, b'{"detail": "sort must be a supported field"}'
        return 200, json.dumps([]).encode()
    source = SentrySource({'id': 'proof', 'org': 'proof', 'project': 'sample', 'token_vault': 'test'},
                          secret=lambda _: 'test-token', http=http)
    assert source.check()[0]
    assert len(calls) == 2
    assert '/projects/proof/sample/issues/' in calls[1]
    assert 'sort=-first_seen' in calls[1]
