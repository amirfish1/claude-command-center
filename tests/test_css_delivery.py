"""CSS delivery preserves token boundaries and literal comment lookalikes."""

import pytest

from ccc_server.css_delivery import compact_comments


@pytest.mark.parametrize("source, expected", [
    (b"a/* description */b{}", b"a/**/b{}"),
    (b"a /* description */ b{}", b"a /**/ b{}"),
    (b"/* multiline\n explanation */\na{}", b"/**/\na{}"),
    (b'a{content:"/* literal */"}', b'a{content:"/* literal */"}'),
    (b"a{content:'/* literal */'}", b"a{content:'/* literal */'}"),
    (br'a{content:"\\\"/* literal */"}', br'a{content:"\\\"/* literal */"}'),
    (b"a{background:url(data:text/plain,/*literal*/)}/* end */",
     b"a{background:url(data:text/plain,/*literal*/)}/**/"),
    (b"a{width:calc(1px + (2px /* keep */))}/* end */",
     b"a{width:calc(1px + (2px /* keep */))}/**/"),
    (br"a\(b/* shorten */{}", br"a\(b/**/{}"),
    (b"/*! license */ /* @LICENSE author */ /* @preserve author */",
     b"/*! license */ /* @LICENSE author */ /* @preserve author */"),
    (b"/* unclosed", b"/* unclosed"),
])
def test_css_comment_boundaries_and_literals(source, expected):
    assert compact_comments(source) == expected


def test_embed_style_and_preloads_use_same_canonical_source():
    from pathlib import Path
    from ccc_server.app_embed import application_page, conversation_page
    from ccc_server.conversation_assets import ConversationScript, STYLE_NAME
    root = Path(__file__).resolve().parents[1] / "static"
    original = (root / "app.css").read_bytes()
    derived = ConversationScript(root, STYLE_NAME).read_bytes()
    assert derived == compact_comments(original)
    assert len(derived) < len(original) * .85
    html = conversation_page((root / "index.html").read_text())
    assert f'href="/static/{STYLE_NAME}"' in html
    assert 'href="/static/app.css"' not in html
    assert f'href="/static/{STYLE_NAME}"' in application_page("<head></head>", "example", "session")
