"""Keep the landing-page example tied to the actual message renderer."""
from html.parser import HTMLParser
from pathlib import Path

from ccc_server.oncall.alerts import render


class ExampleMessage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'pre' and dict(attrs).get('id') == 'try-message':
            self.active = True

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def test_example_uses_actual_decision_message():
    page = ExampleMessage()
    page.feed((Path(__file__).resolve().parents[1] / 'docs/index.html').read_text())
    _, message, _ = render({
        'id': 4, 'kind': 'decision', 'ref': 'SHOP-12',
        'title': 'Checkout button does nothing on Safari',
        'body': 'Keep the old redirect as a fallback for one release, or remove it now?',
        'meta': {'recommend': 'keep it for one release.'},
    })
    assert ''.join(page.parts) == message
